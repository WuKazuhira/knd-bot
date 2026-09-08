package pjsk

import (
	"context"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/cards"
	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

// cstZone 是 Asia/Shanghai (UTC+8)，用于活动时间格式化（避免依赖系统 tzdata）。
var cstZone = time.FixedZone("CST", 8*3600)

// EventModule 实现活动信息查询（event）与活动图鉴筛选（findevent），
// 出图走 pjsk-draw 的 "event_info" / "event_catalog" 任务。
type EventModule struct {
	md    *masterdata.Loader
	draw  *draw.Client
	chara *cards.CharaAliasResolver
}

// NewEventModule 创建活动模块。chara 可为 nil（此时 findevent 的别名解析退化为内置缩写）。
func NewEventModule(md *masterdata.Loader, d *draw.Client, chara *cards.CharaAliasResolver) *EventModule {
	return &EventModule{md: md, draw: d, chara: chara}
}

// Register 注册活动信息与活动图鉴指令。
func (m *EventModule) Register(r *router.Router) {
	r.Register("event", nil, m.handle)
	r.Register("findevent", []string{"查活动", "查询活动", "活动图鉴", "活动总览", "活动手册", "活动列表"}, m.handleFindEvent)
}

func (m *EventModule) handle(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	events, err := m.md.Load("events.json", server)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}

	// 解析活动 id：参数为数字则用之，否则取当前活动
	eventID := 0
	if arg := digitsOnly(req.Arg); arg != "" {
		eventID = atoiDefault(arg, 0)
	} else {
		eventID = currentEventID(events, time.Now().UnixMilli())
	}
	if eventID == 0 {
		return onebot.ReplyText(req.Event, "未找到活动", false)
	}

	payload, ok := m.buildEventPayload(events, eventID, server)
	if !ok {
		return onebot.ReplyText(req.Event, "未找到活动或生成失败", false)
	}
	img, err := m.draw.Render(ctx, "event_info", map[string]any{
		"event":     payload,
		"pjsk_type": server,
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}

// currentEventID 返回当前活动 id，对齐 old-python currentevent：
// 进行中/结算缓冲期内 > 下一期未开始 > 最后一期。
func currentEventID(events []map[string]any, nowMS int64) int {
	// 进行中或结算缓冲期（aggregateAt + 10min）
	for _, e := range events {
		start := int64(intField(e, "startAt"))
		aggregate := int64(intField(e, "aggregateAt"))
		if start < nowMS && nowMS < aggregate+600000 {
			return intField(e, "id")
		}
	}
	// 下一期未开始（最早的）
	nextID, minStart := 0, int64(1)<<62
	for _, e := range events {
		start := int64(intField(e, "startAt"))
		if start > nowMS && start < minStart {
			minStart = start
			nextID = intField(e, "id")
		}
	}
	if nextID != 0 {
		return nextID
	}
	// 最后一期
	lastID, maxStart := 0, int64(-1)
	for _, e := range events {
		if start := int64(intField(e, "startAt")); start > maxStart {
			maxStart = start
			lastID = intField(e, "id")
		}
	}
	return lastID
}

// buildEventPayload 构造 event_info 出图载荷，对齐 EventInfoView._FIELDS + getevent。
func (m *EventModule) buildEventPayload(events []map[string]any, eventID, server int) (map[string]any, bool) {
	var ev map[string]any
	for _, e := range events {
		if intField(e, "id") == eventID {
			ev = e
			break
		}
	}
	if ev == nil {
		return nil, false
	}

	aggregateMS := int64(intField(ev, "aggregateAt"))
	payload := map[string]any{
		"id":              eventID,
		"eventType":       strField(ev, "eventType"),
		"name":            strField(ev, "name"),
		"assetbundleName": strField(ev, "assetbundleName"),
		"startAt":         fmtEventTime(int64(intField(ev, "startAt"))),
		"aggregateAtorin": aggregateMS,
		"aggregateAt":     fmtEventTime(aggregateMS + 1000),
		"unit":            strField(ev, "unit"),
		"music":           0,
	}

	// 活动卡
	var cardIDs []int
	if ec, err := m.md.Load("eventCards.json", server); err == nil {
		for _, c := range ec {
			if intField(c, "eventId") == eventID {
				cardIDs = append(cardIDs, intField(c, "cardId"))
			}
		}
	}
	payload["cards"] = cardIDs

	// 加成角色/属性
	var bonusChara []int
	bonusAttr := ""
	if bonuses, err := m.md.Load("eventDeckBonuses.json", server); err == nil {
		for _, b := range bonuses {
			if intField(b, "eventId") != eventID {
				continue
			}
			if a := strField(b, "cardAttr"); a != "" {
				bonusAttr = a
			}
			if gcid := intField(b, "gameCharacterUnitId"); gcid != 0 {
				bonusChara = append(bonusChara, gcid)
			}
		}
	}
	payload["bonusechara"] = bonusChara
	payload["bonuseattr"] = bonusAttr

	return payload, true
}

// fmtEventTime 把毫秒时间戳格式化为 Asia/Shanghai 的 yyyy/MM/dd HH:mm:ss。
func fmtEventTime(ms int64) string {
	if ms <= 0 {
		return ""
	}
	return time.Unix(ms/1000, 0).In(cstZone).Format("2006/01/02 15:04:05")
}
