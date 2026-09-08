package pjsk

import (
	"context"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
	"unicode"

	"golang.org/x/text/unicode/norm"
	"gopkg.in/yaml.v3"

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
}

// NewSongModule 创建歌曲模块。
func NewSongModule(md *masterdata.Loader, s *store.Store, d *draw.Client, dataDir string) *SongModule {
	return &SongModule{md: md, store: s, draw: d, dataDir: dataDir}
}

// Register 注册歌曲相关指令。
func (m *SongModule) Register(r *router.Router) {
	r.Register("pjskinfo", []string{"song", "查曲"}, m.handleInfo)
	r.Register("查物量", nil, m.handleNoteCount)
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
	res := m.findSong(ctx, arg, int(req.Server))
	if !res.found {
		return onebot.ReplyText(req.Event, "没有找到你要的歌曲哦", false)
	}
	img, err := m.draw.Render(ctx, "pjskinfo", map[string]any{"music_id": res.musicID, "pjsk_type": int(req.Server)})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	var text string
	if m.isLeak(res.musicID, int(req.Server)) {
		text = "⚠该内容为剧透内容"
	} else {
		text = res.title
	}
	return onebot.SendMessageAction(req.Event, onebot.Message{
		onebot.Text(text + "\n"),
		onebot.ImageBytes(base64.StdEncoding.EncodeToString(img)),
	})
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
