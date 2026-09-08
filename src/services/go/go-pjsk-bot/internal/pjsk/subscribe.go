package pjsk

import (
	"context"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

// serverNameCN 服务器中文名。
var serverNameCN = map[int]string{0: "日服", 1: "台服", 2: "国服"}

// SubscribeModule 实现订阅相关的查询指令。
//
// 本模块目前实现「虚拟live 列表」（纯主数据 + 出图）。订阅开关/状态（需独立
// sqlite 订阅库）与定时推送（需 OneBot 主动推送 + 新曲/live 检测）作为增量。
type SubscribeModule struct {
	md   *masterdata.Loader
	draw *draw.Client
}

// NewSubscribeModule 创建订阅模块。
func NewSubscribeModule(md *masterdata.Loader, d *draw.Client) *SubscribeModule {
	return &SubscribeModule{md: md, draw: d}
}

// Register 注册订阅相关指令。
func (m *SubscribeModule) Register(r *router.Router) {
	r.Register("虚拟live", []string{"vlive", "pjsklive列表"}, m.handleVlive)
}

// isNotifiableVlive 判定虚拟 live 是否可展示，对齐 _is_notifiable_vlive：
// 非 beginner + 未结束 + 持续 < 30 天。
func isNotifiableVlive(v map[string]any, nowMS int64) bool {
	if strField(v, "virtualLiveType") == "beginner" {
		return false
	}
	start := int64(intField(v, "startAt"))
	end := int64(intField(v, "endAt"))
	if end <= nowMS {
		return false
	}
	return end-start < int64(30*24*3600*1000)
}

func (m *SubscribeModule) handleVlive(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	name := serverNameCN[server]
	vlives, err := m.md.Load("virtualLives.json", server)
	if err != nil {
		return onebot.ReplyText(req.Event, "当前"+name+"没有7天内的虚拟Live", false)
	}
	nowMS := time.Now().UnixMilli()
	withinMS := int64(7 * 24 * 3600 * 1000)
	var recent []map[string]any
	for _, v := range vlives {
		if !isNotifiableVlive(v, nowMS) {
			continue
		}
		start := int64(intField(v, "startAt"))
		if start-nowMS < withinMS {
			recent = append(recent, v)
		}
	}
	if len(recent) == 0 {
		return onebot.ReplyText(req.Event, "当前"+name+"没有7天内的虚拟Live", false)
	}
	img, err := m.draw.Render(ctx, "vlive_cards", map[string]any{
		"title":     "近期虚拟Live（" + name + "）",
		"vlives":    recent,
		"footer":    "KNDBOT · 虚拟Live",
		"pjsk_type": server,
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}
