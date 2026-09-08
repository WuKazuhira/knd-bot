package pjsk

import (
	"context"
	"encoding/base64"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/profile"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

// ProfileModule 实现个人档案查询（烧烤档案/profile），出图走 pjsk-draw 的 "profile" 任务。
//
// 档案查询主流程 + 背景设置管理（清除背景 / 调整个人信息，读写共享的
// profile_bg/settings.json）。背景图片上传涉及图像处理，仍保留 Python。
type ProfileModule struct {
	fetcher   *profile.Fetcher
	resolver  *UserResolver
	draw      *draw.Client
	staticDir string
}

// NewProfileModule 创建 profile 模块。
func NewProfileModule(f *profile.Fetcher, resolver *UserResolver, d *draw.Client, staticDir string) *ProfileModule {
	return &ProfileModule{fetcher: f, resolver: resolver, draw: d, staticDir: staticDir}
}

// Register 注册档案查询与背景设置指令。
func (m *ProfileModule) Register(r *router.Router) {
	r.Register("烧烤档案", []string{"profile", "pjskprofile", "个人信息"}, m.handle)
	r.Register("清除个人信息背景", []string{"清空个人信息背景", "清除个人背景"}, m.handleClearBg)
	r.Register("调整个人信息", []string{"设置个人信息"}, m.handleAdjust)
}

func (m *ProfileModule) handle(ctx context.Context, req router.Request) *onebot.ActionRequest {
	userid, isPrivate, errMsg := m.resolver.Resolve(ctx, req)
	if errMsg != "" {
		return onebot.ReplyText(req.Event, errMsg, true)
	}
	p, err := m.fetcher.GetProfile(ctx, userid, int(req.Server))
	if err != nil {
		return onebot.ReplyText(req.Event, apiErrText(err), true)
	}
	img, err := m.draw.Render(ctx, "profile", map[string]any{
		"profile":    p.ProfilePayload(),
		"userid":     userid,
		"is_private": isPrivate,
		"pjsk_type":  int(req.Server),
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64.StdEncoding.EncodeToString(img))
}
