package pjsk

import (
	"context"
	"encoding/base64"
	"strings"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/profile"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

// RopModule 实现收歌进度查询（pjsk进度/烧烤进度），出图走 pjsk-draw 的 "rop" 任务。
type RopModule struct {
	fetcher  *profile.Fetcher
	resolver *UserResolver
	draw     *draw.Client
}

// NewRopModule 创建 rop 模块。
func NewRopModule(f *profile.Fetcher, resolver *UserResolver, d *draw.Client) *RopModule {
	return &RopModule{fetcher: f, resolver: resolver, draw: d}
}

// Register 注册 rop 指令。
func (m *RopModule) Register(r *router.Router) {
	r.RegisterNumericSuffix("pjsk进度", []string{"pjskrop", "烧烤进度"}, m.handle)
}

func (m *RopModule) handle(ctx context.Context, req router.Request) *onebot.ActionRequest {
	// 难度参数：含 ex/expert 查 expert，否则 master（与 uid 参数可共存）。
	diff := "master"
	low := strings.ToLower(req.Arg)
	if strings.Contains(low, "ex") || strings.Contains(low, "expert") {
		diff = "expert"
	}

	userid, isPrivate, errMsg := m.resolver.Resolve(ctx, req)
	if errMsg != "" {
		return onebot.ReplyText(req.Event, errMsg, true)
	}

	p, err := m.fetcher.GetSuite(ctx, userid, int(req.Server))
	if err != nil {
		return onebot.ReplyText(req.Event, apiErrText(err), true)
	}

	img, err := m.draw.Render(ctx, "rop", map[string]any{
		"diff":      diff,
		"score":     p.ScoreMap(diff),
		"header":    p.HeaderPayload(isPrivate),
		"pjsk_type": int(req.Server),
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64.StdEncoding.EncodeToString(img))
}
