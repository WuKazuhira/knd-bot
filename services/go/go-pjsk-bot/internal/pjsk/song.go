package pjsk

import (
	"context"
	"encoding/base64"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
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

type bpmCacheKey struct {
	server  int
	musicID int
}

type bpmCacheEntry struct {
	mtime int64
	size  int64
	bpms  []string
}

type songSearchIndex struct {
	byID      map[int]map[string]any
	titleByID map[int]string
	normExact map[string]songResult
}

type SongModule struct {
	md      *masterdata.Loader
	store   *store.Store
	draw    *draw.Client
	dataDir string
	chara   *cards.CharaAliasResolver

	bpmMu    sync.RWMutex
	bpmCache map[bpmCacheKey]bpmCacheEntry
}

func NewSongModule(md *masterdata.Loader, s *store.Store, d *draw.Client, dataDir string, chara *cards.CharaAliasResolver) *SongModule {
	return &SongModule{
		md:       md,
		store:    s,
		draw:     d,
		dataDir:  dataDir,
		chara:    chara,
		bpmCache: make(map[bpmCacheKey]bpmCacheEntry),
	}
}

func (m *SongModule) Register(r *router.Router) {
	r.RegisterNumericSuffix("pjskinfo", []string{"song", "查曲"}, m.handleInfo)
	r.RegisterNumericSuffix("查物量", nil, m.handleNoteCount)
	r.RegisterNumericSuffix("pjskbpm", []string{"bpm", "查曲bpm"}, m.handleBPM)
	r.RegisterNumericSuffix("查bpm", nil, m.handleBPMFind)
	r.RegisterNumericSuffix("pjskalias", []string{"查别称"}, m.handleAlias)
	r.RegisterNumericSuffix("pjskdel", nil, m.handleAliasDel)
	r.RegisterRegex("pjskset", `(?i)^(cn|tw)?pjskset\s*(.+to.+)$`, m.handleAliasSet)
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

type songResult struct {
	musicID int
	title   string
	found   bool
}

func (m *SongModule) searchIndex(serverType int) (*songSearchIndex, error) {
	val, err := m.md.Derived("song_search_index", serverType, []string{"musics.json", "translate.yaml"}, func() (any, error) {
		musics, err := m.md.Load("musics.json", serverType)
		if err != nil {
			return nil, err
		}
		trans := m.loadTranslations(serverType)
		byID := make(map[int]map[string]any, len(musics))
		titleByID := make(map[int]string, len(musics))
		normExact := make(map[string]songResult, len(musics)*2)
		for _, mu := range musics {
			id := intField(mu, "id")
			title := strField(mu, "title")
			byID[id] = mu
			titleByID[id] = title
			if key := normalizeSongQuery(title); key != "" {
				if _, exists := normExact[key]; !exists {
					normExact[key] = songResult{musicID: id, title: title, found: true}
				}
			}
			if tr := trans[id]; tr != "" {
				for _, tt := range strings.Split(tr, "/") {
					if key := normalizeSongQuery(strings.TrimSpace(tt)); key != "" {
						if _, exists := normExact[key]; !exists {
							normExact[key] = songResult{musicID: id, title: title, found: true}
						}
					}
				}
			}
		}
		return &songSearchIndex{byID: byID, titleByID: titleByID, normExact: normExact}, nil
	})
	if err != nil {
		return nil, err
	}
	return val.(*songSearchIndex), nil
}

func (m *SongModule) findSong(ctx context.Context, arg string, serverType int) songResult {
	idx, err := m.searchIndex(serverType)
	if err != nil {
		return songResult{}
	}

	if n, err := strconv.Atoi(arg); err == nil {
		if title, ok := idx.titleByID[n]; ok {
			return songResult{musicID: n, title: title, found: true}
		}
	}
	if m.store != nil {
		if sid, ok, _ := m.store.QuerySongID(ctx, arg); ok {
			if title, ok := idx.titleByID[sid]; ok {
				return songResult{musicID: sid, title: title, found: true}
			}
		}
	}
	want := normalizeSongQuery(arg)
	if want != "" {
		if res, ok := idx.normExact[want]; ok {
			return res
		}
	}
	return songResult{}
}

func (m *SongModule) isLeak(musicID, serverType int) bool {
	idx, err := m.searchIndex(serverType)
	if err != nil {
		return true
	}
	mu, ok := idx.byID[musicID]
	if !ok {
		return true
	}
	return time.Now().UnixMilli() < int64(intField(mu, "publishedAt"))
}

func (m *SongModule) handleInfo(ctx context.Context, req router.Request) *onebot.ActionRequest {
	arg := strings.TrimSpace(req.Arg)
	if arg == "" {
		return onebot.ReplyText(req.Event, "使用方法：pjskinfo + 曲名", false)
	}
	server := int(req.Server)
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
	info, err := os.Stat(path)
	if err != nil {
		return []string{"无数据"}
	}
	mtime, size := info.ModTime().UnixNano(), info.Size()
	key := bpmCacheKey{server: server, musicID: musicID}

	m.bpmMu.RLock()
	if ent, ok := m.bpmCache[key]; ok && ent.mtime == mtime && ent.size == size {
		m.bpmMu.RUnlock()
		return ent.bpms
	}
	m.bpmMu.RUnlock()

	raw, err := os.ReadFile(path)
	if err != nil {
		return []string{"无数据"}
	}
	palette := map[string]string{}
	var sequence []string
	for _, line := range strings.Split(string(raw), "\n") {
		trimmed := strings.TrimSpace(line)
		// 只看 #BPM 和 08 信道行，跳过大量音符行避免无谓正则
		if len(trimmed) < 7 || trimmed[0] != '#' {
			continue
		}
		if !strings.HasPrefix(trimmed, "#BPM") && !(trimmed[4] == '0' && trimmed[5] == '8') {
			continue
		}
		match := chartBPMLine.FindStringSubmatch(trimmed)
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
		sequence = []string{"无数据"}
	}
	m.bpmMu.Lock()
	if m.bpmCache == nil {
		m.bpmCache = make(map[bpmCacheKey]bpmCacheEntry)
	}
	m.bpmCache[key] = bpmCacheEntry{mtime: mtime, size: size, bpms: sequence}
	m.bpmMu.Unlock()
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

func (m *SongModule) titleByID(musicID, serverType int) string {
	if idx, err := m.searchIndex(serverType); err == nil {
		return idx.titleByID[musicID]
	}
	return ""
}

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

func (m *SongModule) handleAliasSet(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if m.store == nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	server := int(req.Server)
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
	// 只折叠 ASCII 的 T/O 大小写，保证 UTF-8 字节偏移不变
	foldedBody := strings.NewReplacer("T", "t", "O", "o").Replace(body)
	idx := 0
	for {
		pos := strings.Index(foldedBody[idx:], "to")
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
	newSID, _, _ := m.store.QuerySongID(ctx, newAlias)
	if title := m.titleByID(newSID, server); title != "" {
		return onebot.ReplyText(req.Event, "添加失败，此称呼已经属于歌曲："+title, true)
	}
	return onebot.ReplyText(req.Event, "添加失败，此称呼已经属于其它歌曲", true)
}

func (m *SongModule) findDeckSong(ctx context.Context, arg string, server int) songResult {
	if exact := m.findSong(ctx, arg, server); exact.found {
		return exact
	}
	query := []rune(normalizeSongQuery(arg))
	if len(query) == 0 || len(query) > 256 {
		return songResult{}
	}
	index, err := m.searchIndex(server)
	if err != nil {
		return songResult{}
	}
	var best songResult
	bestScore := 0.5
	for name, song := range index.normExact {
		title := []rune(name)
		longest := max(len(title), len(query))
		if len(title)*2 < len(query) || len(query)*2 < len(title) {
			continue
		}
		score := 1 - float64(songEditDistance(query, title))/float64(longest)
		if strings.Contains(name, string(query)) {
			score = math.Max(score, 0.5+float64(len(query))/(2*float64(len(title))))
		}
		if score > bestScore || score == bestScore && (!best.found || song.musicID < best.musicID) {
			best, bestScore = song, score
		}
	}
	return best
}

func songEditDistance(a, b []rune) int {
	row := make([]int, len(b)+1)
	for i := range row {
		row[i] = i
	}
	for i, left := range a {
		diag := row[0]
		row[0] = i + 1
		for j, right := range b {
			cost := 0
			if left != right {
				cost = 1
			}
			row[j+1], diag = min(row[j]+1, row[j+1]+1, diag+cost), row[j+1]
		}
	}
	return row[len(b)]
}
