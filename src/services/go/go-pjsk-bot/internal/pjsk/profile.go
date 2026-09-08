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
// 背景上传/调整等指令涉及用户上传图片的存储，属于绘图服务 profile_bg 范畴，
// 保留在 Python 侧，本模块只负责档案查询主流程。
type ProfileModule struct {
	fetcher  *profile.Fetcher
	resolver *UserResolver
	draw     *draw.Client
}

// NewProfileModule 创建 profile 模块。
func NewProfileModule(f *profile.Fetcher, resolver *UserResolver, d *draw.Client) *ProfileModule {
	return &ProfileModule{fetcher: f, resolver: resolver, draw: d}
}

// Register 注册档案查询指令。
func (m *ProfileModule) Register(r *router.Router) {
	r.Register("烧烤档案", []string{"profile", "pjskprofile", "个人信息"}, m.handle)
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
