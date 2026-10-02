package pjsk

import (
	"context"
	"fmt"
	"github.com/kazuhira/go-pjsk-bot/internal/notifysub"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"strconv"
	"strings"
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
func parseCardCostumeArgs(arg string) (int, int, error) {
	fields := strings.Fields(arg)
	if len(fields) < 1 || len(fields) > 2 {
		return 0, 0, fmt.Errorf("用法：查服装 [卡号] [配色序号可选，默认一]（一二三四或 1、2、3、4）")
	}
	card, err := strconv.Atoi(fields[0])
	if err != nil || card <= 0 {
		return 0, 0, fmt.Errorf("卡号应为正整数")
	}
	if len(fields) == 1 {
		return card, 1, nil
	}
	ordinal := fields[1]
	for i, v := range []string{"一", "二", "三", "四"} {
		if ordinal == v {
			ordinal = strconv.Itoa(i + 1)
		}
	}
	variant, err := strconv.Atoi(ordinal)
	if err != nil || variant <= 0 {
		return 0, 0, fmt.Errorf("配色序号应为正整数或一二三四")
	}
	return card, variant, nil
}
func (m *SubscribeModule) handleCardCostume(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if req.Server != router.ServerJP {
		return onebot.ReplyText(req.Event, "查服装仅支持日服", false)
	}
	card, variant, err := parseCardCostumeArgs(req.Arg)
	if err != nil {
		return onebot.ReplyText(req.Event, err.Error(), false)
	}
	if m.newCard == nil || m.newCard.costumes == nil {
		return onebot.ReplyText(req.Event, "查服装暂不可用", false)
	}
	item, err := m.newCard.costumes.BuildCardCostume(ctx, card, variant)
	if err != nil {
		return onebot.ReplyText(req.Event, "查服装失败："+err.Error(), false)
	}
	var message onebot.Message
	for _, node := range item.ForwardNodes {
		message = append(message, node.Data.Content...)
	}
	if len(message) == 0 {
		return onebot.ReplyText(req.Event, "查服装失败：未生成服装图片", false)
	}
	return onebot.SendMessageAction(req.Event, message)
}
func (s *NewCardSubscriptionSource) SetCostumes(renderer *CostumeRenderer) *CostumeSubscriptionSource {
	s.costumes = NewCostumeSubscriptionSource(s, renderer)
	return s.costumes
}
