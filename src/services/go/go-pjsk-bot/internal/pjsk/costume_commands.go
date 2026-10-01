package pjsk

import (
	"context"
	"github.com/kazuhira/go-pjsk-bot/internal/notifysub"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

func (m *SubscribeModule) costumeGroupHandler(on bool) router.Handler {
	return func(ctx context.Context, req router.Request) *onebot.ActionRequest {
		if int(req.Server) != 0 {
			return onebot.ReplyText(req.Event, "新服装通知仅支持日服", false)
		}
		if !req.Event.IsGroup() || !m.isAdmin(req.Event) {
			return nil
		}
		if on && (m.newCard == nil || m.newCard.costumes == nil || !m.newCard.costumes.enabled()) {
			return onebot.ReplyText(req.Event, "新服装功能未启用，请先配置官方 3D 引擎与一致快照", false)
		}
		return m.groupSubHandler(notifysub.KindCostume, on)(ctx, req)
	}
}
func (m *SubscribeModule) handleCostume(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if int(req.Server) != 0 {
		return onebot.ReplyText(req.Event, "新服装速报仅支持日服", false)
	}
	if m.newCard == nil || m.newCard.costumes == nil {
		return onebot.ReplyText(req.Event, "新服装速报暂不可用", false)
	}
	item, err := m.newCard.costumes.BuildLatest(ctx)
	if err != nil {
		return onebot.ReplyText(req.Event, "新服装资源尚未就绪："+err.Error(), false)
	}
	return onebot.SendForwardAction(req.Event, item.ForwardNodes)
}
func (s *NewCardSubscriptionSource) SetCostumes(renderer *CostumeRenderer) *CostumeSubscriptionSource {
	s.costumes = NewCostumeSubscriptionSource(s, renderer)
	return s.costumes
}
