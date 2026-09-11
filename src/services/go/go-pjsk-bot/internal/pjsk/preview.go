package pjsk

import (
	"context"
	"encoding/base64"
	"strings"

	"github.com/kazuhira/go-pjsk-bot/internal/cards"
	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/store"
)

// PreviewModule 迁移谱面预览/技能预览的请求编排；图片生成仍由 pjsk-draw 完成。
type PreviewModule struct {
	song *SongModule
	draw *draw.Client
}

func NewPreviewModule(md *masterdata.Loader, s *store.Store, d *draw.Client, dataDir string, chara *cards.CharaAliasResolver) *PreviewModule {
	return &PreviewModule{song: NewSongModule(md, s, d, dataDir, chara), draw: d}
}

func (m *PreviewModule) Register(r *router.Router) {
	r.Register("谱面预览", []string{"谱面预览1", "谱面预览2", "谱面预览3"}, m.handleChart)
	r.Register("技能预览", nil, m.handleSkill)
}

var previewDiffs = map[string]string{
	"master": "master", "ma": "master",
	"expert": "expert", "ex": "expert",
	"hard": "hard", "hd": "hard",
	"normal": "normal", "nm": "normal",
	"easy": "easy", "ez": "easy",
	"append": "append", "ap": "append", "app": "append", "apd": "append",
}

func parsePreviewArgs(arg string) (string, string) {
	var diff string
	var rest []string
	for _, token := range strings.Fields(arg) {
		if value, ok := previewDiffs[strings.ToLower(token)]; ok && diff == "" {
			diff = value
			continue
		}
		rest = append(rest, token)
	}
	if diff == "" {
		diff = "master"
	}
	return diff, strings.TrimSpace(strings.Join(rest, " "))
}

func (m *PreviewModule) handleChart(ctx context.Context, req router.Request) *onebot.ActionRequest {
	diff, query := parsePreviewArgs(req.Arg)
	if query == "" {
		return onebot.ReplyText(req.Event, "使用方法：谱面预览 [难度] [曲名]", false)
	}
	result := m.song.findSong(ctx, query, int(req.Server))
	if !result.found {
		return onebot.ReplyText(req.Event, "没有找到你要的歌曲哦", false)
	}
	getType := 1
	if strings.HasSuffix(strings.ToLower(req.RawCmd), "2") {
		getType = 2
	} else if strings.HasSuffix(strings.ToLower(req.RawCmd), "3") {
		getType = 3
	}
	images, meta, err := m.draw.RenderWithMeta(ctx, "map_preview", map[string]any{
		"music_id":   result.musicID,
		"difficulty": diff,
		"get_type":   getType,
		"pjsk_type":  int(req.Server),
	})
	if err != nil || len(images) == 0 {
		return onebot.ReplyText(req.Event, "暂无谱面图片，请等待更新", false)
	}
	text := result.title + " " + strings.ToUpper(diff)
	if bpms := m.song.chartBPM(result.musicID, int(req.Server)); len(bpms) > 0 {
		text += "\nBPM: " + strings.Join(bpms, " - ")
	}
	if source, ok := meta["source"].(string); ok && source != "" {
		text += "\n谱面图片来源：" + source
	}
	return onebot.SendMessageAction(req.Event, onebot.Message{
		onebot.Text(text + "\n"),
		onebot.ImageBytes(base64.StdEncoding.EncodeToString(images[0])),
	})
}

func (m *PreviewModule) handleSkill(ctx context.Context, req router.Request) *onebot.ActionRequest {
	diff, query := parsePreviewArgs(req.Arg)
	if query == "" {
		return onebot.ReplyText(req.Event, "使用方法：技能预览 [难度] [曲名]", false)
	}
	result := m.song.findSong(ctx, query, int(req.Server))
	if !result.found {
		return onebot.ReplyText(req.Event, "没有找到你要的歌曲哦", false)
	}
	images, _, err := m.draw.RenderWithMeta(ctx, "skill_preview", map[string]any{
		"music_id":   result.musicID,
		"difficulty": diff,
		"pjsk_type":  int(req.Server),
	})
	if err != nil || len(images) == 0 {
		return onebot.ReplyText(req.Event, "暂无技能谱面图片，请等待更新", false)
	}
	text := result.title + " " + strings.ToUpper(diff)
	if bpms := m.song.chartBPM(result.musicID, int(req.Server)); len(bpms) > 0 {
		text += "\nBPM: " + strings.Join(bpms, " - ")
	}
	return onebot.SendMessageAction(req.Event, onebot.Message{
		onebot.Text(text + "\n"),
		onebot.ImageBytes(base64.StdEncoding.EncodeToString(images[0])),
	})
}
