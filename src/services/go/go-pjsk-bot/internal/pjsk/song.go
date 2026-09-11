package pjsk

import (
	"context"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"

	"golang.org/x/text/unicode/norm"
	"gopkg.in/yaml.v3"

	"github.com/kazuhira/go-pjsk-bot/internal/cards"
	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/store"
)

// SongModule 实现歌曲信息查询（pjskinfo/song）与查物量。
// 歌曲搜索支持：数字 id / 数据库别名 / 标题(含翻译)归一化精确匹配。
// 模糊拼音评分匹配暂未迁移（作为增强项），精确匹配未命中时提示未找到。
type SongModule struct {
	md      *masterdata.Loader
	store   *store.Store
	draw    *draw.Client
	dataDir string
	chara   *cards.CharaAliasResolver
}

// NewSongModule 创建歌曲模块。chara 可为 nil（此时 pjskinfo 的 ena7 箱活歌曲短写禁用）。
func NewSongModule(md *masterdata.Loader, s *store.Store, d *draw.Client, dataDir string, chara *cards.CharaAliasResolver) *SongModule {
	return &SongModule{md: md, store: s, draw: d, dataDir: dataDir, chara: chara}
}

// Register 注册歌曲相关指令。
func (m *SongModule) Register(r *router.Router) {
	r.Register("pjskinfo", []string{"song", "查曲"}, m.handleInfo)
	r.Register("查物量", nil, m.handleNoteCount)
	r.Register("pjskbpm", []string{"bpm", "查曲bpm"}, m.handleBPM)
	r.Register("查bpm", nil, m.handleBPMFind)
	r.Register("pjskalias", []string{"查别称"}, m.handleAlias)
	r.Register("pjskdel", nil, m.handleAliasDel)
	// pjskset 用正则触发（含 "to" 分隔），对齐 ^(cn|tw)?pjskset(.+to.+)。
	r.RegisterRegex("pjskset", `^(cn|tw)?pjskset\s*(.+to.+)$`, m.handleAliasSet)
}

func serverDirName(serverType int) string {
	switch serverType {
	case 1:
		return "tw"
	case 2:
		return "cn"
	default:
		return "jp"
	}
}

// normalizeSongQuery 对齐 old-python _normalize_song_query：
// NFKC 归一化 + 小写 + 只保留字母数字/日文假名/中日韩汉字。
func normalizeSongQuery(text string) string {
	text = strings.ToLower(norm.NFKC.String(text))
	var b strings.Builder
	for _, ch := range text {
		if unicode.IsLetter(ch) || unicode.IsDigit(ch) ||
			(ch >= 0x3040 && ch <= 0x30ff) || (ch >= 0x3400 && ch <= 0x9fff) {
			b.WriteRune(ch)
		}
	}
	return b.String()
}

// loadTranslations 读取 {server}/translate.yaml，返回 musicId -> 翻译。
func (m *SongModule) loadTranslations(serverType int) map[int]string {
	path := filepath.Join(m.dataDir, "ondemand", serverDirName(serverType), "translate.yaml")
	raw, err := os.ReadFile(path)
	if err != nil {
		return map[int]string{}
	}
	var parsed map[int]string
	if err := yaml.Unmarshal(raw, &parsed); err != nil {
		return map[int]string{}
	}
	return parsed
}

// songResult 是一次歌曲查询的结果。
type songResult struct {
	musicID int
	title   string
	found   bool
}

// findSong 按 id / 别名 / 标题精确匹配查歌曲。
func (m *SongModule) findSong(ctx context.Context, arg string, serverType int) songResult {
	musics, err := m.md.Load("musics.json", serverType)
	if err != nil {
		return songResult{}
	}
	byID := make(map[int]map[string]any, len(musics))
	for _, mu := range musics {
		byID[intField(mu, "id")] = mu
	}

	// 1. 数字 id
	if n, err := strconv.Atoi(arg); err == nil {
		if mu, ok := byID[n]; ok {
			return songResult{musicID: n, title: strField(mu, "title"), found: true}
		}
	}
	// 2. 数据库别名
	if m.store != nil {
		if sid, ok, _ := m.store.QuerySongID(ctx, arg); ok {
			if mu, ok := byID[sid]; ok {
				return songResult{musicID: sid, title: strField(mu, "title"), found: true}
			}
		}
	}
	// 3. 标题（含翻译）归一化精确匹配
	trans := m.loadTranslations(serverType)
	want := normalizeSongQuery(arg)
	if want != "" {
		for id, mu := range byID {
			if normalizeSongQuery(strField(mu, "title")) == want {
				return songResult{musicID: id, title: strField(mu, "title"), found: true}
			}
			for _, tt := range strings.Split(trans[id], "/") {
				if normalizeSongQuery(strings.TrimSpace(tt)) == want {
					return songResult{musicID: id, title: strField(mu, "title"), found: true}
				}
			}
		}
	}
	return songResult{}
}

// isLeak 判断歌曲是否未公开（剧透），对齐 old-python isleak。
func (m *SongModule) isLeak(musicID, serverType int) bool {
	musics, err := m.md.Load("musics.json", serverType)
	if err != nil {
		return true
	}
	nowMS := time.Now().UnixMilli()
	for _, mu := range musics {
		if intField(mu, "id") == musicID {
			return nowMS < int64(intField(mu, "publishedAt"))
		}
	}
	return true
}

func (m *SongModule) handleInfo(ctx context.Context, req router.Request) *onebot.ActionRequest {
	arg := strings.TrimSpace(req.Arg)
	if arg == "" {
		return onebot.ReplyText(req.Event, "使用方法：pjskinfo + 曲名", false)
	}
	server := int(req.Server)
	// ena7 箱活短写：列出/展示该活动的歌曲（对齐 Python pjskinfo 的 ban_event 分支）。
	if m.chara != nil {
		if ev, _, banErr := extractBanEventArg(m.md, server, arg, m.chara.Resolve); banErr != "" {
			return onebot.ReplyText(req.Event, banErr, true)
		} else if ev != nil {
			return m.eventSongs(ctx, req, ev, server)
		}
	}
	res := m.findSong(ctx, arg, server)
	if !res.found {
		return onebot.ReplyText(req.Event, "没有找到你要的歌曲哦", false)
	}
	img, err := m.draw.Render(ctx, "pjskinfo", map[string]any{"music_id": res.musicID, "pjsk_type": server})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	var text string
	if m.isLeak(res.musicID, server) {
		text = "⚠该内容为剧透内容"
	} else {
		text = res.title
	}
	return onebot.SendMessageAction(req.Event, onebot.Message{
		onebot.Text(text + "\n"),
		onebot.ImageBytes(base64.StdEncoding.EncodeToString(img)),
	})
}

// eventSongs 展示某箱活的活动歌曲：无歌提示、多首列出、单首出图（对齐 pjskinfo ban_event）。
func (m *SongModule) eventSongs(ctx context.Context, req router.Request, ev *banEvent, server int) *onebot.ActionRequest {
	name := ev.Name
	if name == "" {
		name = "该活动"
	}
	musicIDs := eventMusicIDList(m.md, server, ev.ID)
	if len(musicIDs) == 0 {
		return onebot.ReplyText(req.Event, fmt.Sprintf("%s Event ID:%d 暂未找到活动歌曲", name, ev.ID), false)
	}
	if len(musicIDs) > 1 {
		lines := []string{fmt.Sprintf("%s Event ID:%d 的活动歌曲：", name, ev.ID)}
		for _, mid := range musicIDs {
			title := m.titleByID(mid, server)
			if title == "" {
				title = strconv.Itoa(mid)
			}
			lines = append(lines, fmt.Sprintf("%s ID:%d", title, mid))
		}
		return onebot.ReplyText(req.Event, strings.Join(lines, "\n"), false)
	}
	mid := musicIDs[0]
	img, err := m.draw.Render(ctx, "pjskinfo", map[string]any{"music_id": mid, "pjsk_type": server})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	title := m.titleByID(mid, server)
	if title == "" {
		title = strconv.Itoa(mid)
	}
	text := fmt.Sprintf("%s Event ID:%d\n%s ID:%d", name, ev.ID, title, mid)
	if m.isLeak(mid, server) {
		text += "\n⚠该内容为剧透内容"
	}
	return onebot.SendMessageAction(req.Event, onebot.Message{
		onebot.Text(text + "\n"),
		onebot.ImageBytes(base64.StdEncoding.EncodeToString(img)),
	})
}

var chartBPMLine = regexp.MustCompile(`^#(...)(...?)\s*:\s*(\S+)`)

func (m *SongModule) chartBPM(musicID, server int) []string {
	path := filepath.Join(m.dataDir, "ondemand", serverDirName(server), "startapp", "music", "music_score", fmt.Sprintf("%04d_01", musicID), "expert")
	raw, err := os.ReadFile(path)
	if err != nil {
		return []string{"无数据"}
	}
	palette := map[string]string{}
	var sequence []string
	for _, line := range strings.Split(string(raw), "\n") {
		match := chartBPMLine.FindStringSubmatch(strings.TrimSpace(line))
		if len(match) != 4 {
			continue
		}
		if match[1] == "BPM" {
			palette[match[2]] = match[3]
			continue
		}
		if match[2] != "08" {
			continue
		}
		value := match[3]
		for i := 0; i+1 < len(value); i += 2 {
			if bpm := palette[value[i:i+2]]; bpm != "" && (len(sequence) == 0 || sequence[len(sequence)-1] != bpm) {
				sequence = append(sequence, bpm)
			}
		}
	}
	if len(sequence) == 0 {
		return []string{"无数据"}
	}
	return sequence
}

func (m *SongModule) handleBPM(ctx context.Context, req router.Request) *onebot.ActionRequest {
	arg := strings.TrimSpace(req.Arg)
	if arg == "" {
		return onebot.ReplyText(req.Event, "使用方法：pjskbpm + 曲名", false)
	}
	res := m.findSong(ctx, arg, int(req.Server))
	if !res.found {
		return onebot.ReplyText(req.Event, "没有找到你要的歌曲哦", false)
	}
	return onebot.ReplyText(req.Event, fmt.Sprintf("%s\\nBPM: %s", res.title, strings.Join(m.chartBPM(res.musicID, int(req.Server)), " - ")), false)
}

func (m *SongModule) handleBPMFind(ctx context.Context, req router.Request) *onebot.ActionRequest {
	target, err := strconv.Atoi(strings.TrimSpace(req.Arg))
	if err != nil {
		return onebot.ReplyText(req.Event, "请输入数字！", false)
	}
	musics, err := m.md.Load("musics.json", int(req.Server))
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	var lines []string
	for _, music := range musics {
		id := intField(music, "id")
		bpms := m.chartBPM(id, int(req.Server))
		for _, value := range bpms {
			n, e := strconv.ParseFloat(value, 64)
			if e == nil && int(n) == target {
				lines = append(lines, fmt.Sprintf("%s: %s", strField(music, "title"), strings.Join(bpms, " - ")))
				break
			}
		}
	}
	if len(lines) == 0 {
		return onebot.ReplyText(req.Event, "没有找到", false)
	}
	return onebot.ReplyText(req.Event, strings.Join(lines, "\\n"), false)
}

func (m *SongModule) handleNoteCount(ctx context.Context, req router.Request) *onebot.ActionRequest {
	n, err := strconv.Atoi(strings.TrimSpace(req.Arg))
	if err != nil {
		return onebot.ReplyText(req.Event, "请输入数字！", false)
	}
	diffs, err := m.md.Load("musicDifficulties.json", int(req.Server))
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	musics, _ := m.md.Load("musics.json", int(req.Server))
	nameByID := make(map[int]string, len(musics))
	for _, mu := range musics {
		nameByID[intField(mu, "id")] = strField(mu, "title")
	}
	var b strings.Builder
	for _, d := range diffs {
		if intField(d, "totalNoteCount") == n {
			mid := intField(d, "musicId")
			fmt.Fprintf(&b, "%s[%s %d]\n",
				nameByID[mid], strings.ToUpper(strField(d, "musicDifficulty")), intField(d, "playLevel"))
		}
	}
	text := b.String()
	if text == "" {
		text = "没有找到"
	}
	return onebot.ReplyText(req.Event, text, false)
}

// titleByID 按 musicId 返回标题，对齐 idtoname；未找到返回空串。
func (m *SongModule) titleByID(musicID, serverType int) string {
	musics, err := m.md.Load("musics.json", serverType)
	if err != nil {
		return ""
	}
	for _, mu := range musics {
		if intField(mu, "id") == musicID {
			return strField(mu, "title")
		}
	}
	return ""
}

// handleAlias 实现 pjskalias/查别称：按别名/曲名查歌曲，返回标题与匹配信息。
func (m *SongModule) handleAlias(ctx context.Context, req router.Request) *onebot.ActionRequest {
	arg := strings.TrimSpace(req.Arg)
	if arg == "" {
		return onebot.ReplyText(req.Event, "请使用正确格式：pjskalias 昵称", false)
	}
	res := m.findSong(ctx, arg, int(req.Server))
	if !res.found || res.musicID == 0 {
		return onebot.ReplyText(req.Event, "没有找到你要的歌曲哦", false)
	}
	return onebot.ReplyText(req.Event, res.title+"\n", false)
}

// handleAliasDel 实现 pjskdel：删除一个歌曲别称。
func (m *SongModule) handleAliasDel(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if m.store == nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	arg := strings.TrimSpace(req.Arg)
	if arg == "" {
		return onebot.ReplyText(req.Event, "请输入要删除的别称", true)
	}
	sid, _, _ := m.store.QuerySongID(ctx, arg)
	songName := ""
	if sid != 0 {
		songName = m.titleByID(sid, int(req.Server))
	}
	deleted, err := m.store.DeleteAlias(ctx, arg)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	if !deleted {
		return onebot.ReplyText(req.Event, "删除失败，找不到歌曲", true)
	}
	if songName != "" {
		return onebot.ReplyText(req.Event, "已成功删除歌曲:"+songName+"的别称:"+arg, true)
	}
	return onebot.ReplyText(req.Event, "删除成功！", true)
}

// handleAliasSet 实现 pjskset：`新别称 to 旧别称`，为旧别称对应歌曲添加新别称。
// 支持别称中本身含 "to" 的情况：从左到右尝试每个 "to" 分割点，直到右侧能查到歌曲。
func (m *SongModule) handleAliasSet(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if m.store == nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	server := int(req.Server)
	// RegexGroups: [0]=整体 [1]=cn/tw前缀 [2]="新 to 旧" 主体
	var body string
	if len(req.RegexGroups) >= 3 {
		switch req.RegexGroups[1] {
		case "cn":
			server = 2
		case "tw":
			server = 1
		}
		body = strings.TrimSpace(req.RegexGroups[2])
	} else {
		body = strings.TrimSpace(req.Arg)
	}

	var oldAlias, newAlias string
	var oldSID int
	idx := 0
	for {
		pos := strings.Index(body[idx:], "to")
		if pos < 0 {
			break
		}
		at := idx + pos
		tmpNew := strings.TrimSpace(body[:at])
		tmpOld := strings.TrimSpace(body[at+2:])
		idx = at + 2
		if sid, ok, _ := m.store.QuerySongID(ctx, tmpOld); ok && sid != 0 {
			oldSID = sid
			oldAlias = tmpOld
			newAlias = tmpNew
			break
		}
	}
	if oldSID == 0 || oldAlias == "" || newAlias == "" {
		return onebot.ReplyText(req.Event, "添加失败，可能是找不到对应称呼", true)
	}
	if oldAlias == newAlias {
		return onebot.ReplyText(req.Event, "添加失败，新称呼与旧称呼相同", true)
	}

	groupID := int64(-1)
	if req.Event.GroupID > 0 {
		groupID = req.Event.GroupID
	}
	added, err := m.store.AddAlias(ctx, oldSID, newAlias, req.Event.UserID, groupID, false)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	if added {
		title := m.titleByID(oldSID, server)
		return onebot.ReplyText(req.Event, "设置成功！"+newAlias+"->"+title, false)
	}
	// 添加失败：新别称已被占用
	newSID, _, _ := m.store.QuerySongID(ctx, newAlias)
	if title := m.titleByID(newSID, server); title != "" {
		return onebot.ReplyText(req.Event, "添加失败，此称呼已经属于歌曲："+title, true)
	}
	return onebot.ReplyText(req.Event, "添加失败，此称呼已经属于其它歌曲", true)
}
