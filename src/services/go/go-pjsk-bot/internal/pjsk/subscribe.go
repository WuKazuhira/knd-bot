package pjsk

import (
	"context"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/notifysub"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

// serverNameCN 服务器中文名。
var serverNameCN = map[int]string{0: "日服", 1: "台服", 2: "国服"}

// kindLabel 订阅类型中文标签，对齐 _KIND_LABEL。
var kindLabel = map[string]string{
	notifysub.KindMusic:   "新曲通知",
	notifysub.KindVLive:   "虚拟Live通知",
	notifysub.KindNewCard: "新卡速递",
}

// SubscribeModule 实现订阅相关的查询与开关指令。
//
// 「虚拟live 列表」为纯主数据 + 出图；新曲/live 群订阅与个人 @ 提醒的开关/状态
// 走独立 sqlite 订阅库（与 Python 共享）。定时推送检测仍由 Python 承担。
type SubscribeModule struct {
	md      *masterdata.Loader
	draw    *draw.Client
	subs    *notifysub.Store
	super   map[int64]bool
	newCard *NewCardSubscriptionSource
}

// NewSubscribeModule 创建订阅模块。subs 可为 nil（无数据目录时禁用开关指令）。
func NewSubscribeModule(md *masterdata.Loader, d *draw.Client, subs *notifysub.Store, supers []int64, newCards ...*NewCardSubscriptionSource) *SubscribeModule {
	set := make(map[int64]bool, len(supers))
	for _, s := range supers {
		set[s] = true
	}
	var newCard *NewCardSubscriptionSource
	if len(newCards) > 0 {
		newCard = newCards[0]
	}
	return &SubscribeModule{md: md, draw: d, subs: subs, super: set, newCard: newCard}
}

// Register 注册订阅相关指令。
func (m *SubscribeModule) Register(r *router.Router) {
	r.Register("虚拟live", []string{"vlive", "pjsklive列表"}, m.handleVlive)
	// 手动新卡情报不依赖订阅库。
	r.Register("新卡速递", []string{"新卡情报", "新卡", "leak"}, m.handleNewCard)
	if m.subs == nil {
		return
	}
	// 群订阅开关（管理员）
	r.Register("pjsk开启新曲通知", []string{"pjsk新曲通知开启"}, m.groupSubHandler(notifysub.KindMusic, true))
	r.Register("pjsk关闭新曲通知", []string{"pjsk新曲通知关闭"}, m.groupSubHandler(notifysub.KindMusic, false))
	r.Register("pjsk开启live通知", []string{"pjsk开启Live通知"}, m.groupSubHandler(notifysub.KindVLive, true))
	r.Register("pjsk关闭live通知", []string{"pjsk关闭Live通知"}, m.groupSubHandler(notifysub.KindVLive, false))
	// 日服新卡群订阅与手动速递。
	r.Register("pjsk开启新卡通知", []string{"pjsk新卡通知开启"}, m.newCardGroupHandler(true))
	r.Register("pjsk关闭新卡通知", []string{"pjsk新卡通知关闭"}, m.newCardGroupHandler(false))
	// 个人 @ 提醒
	r.Register("pjsk新曲提醒", nil, m.userSubHandler(notifysub.KindMusic, true))
	r.Register("pjsk取消新曲提醒", nil, m.userSubHandler(notifysub.KindMusic, false))
	r.Register("pjsklive提醒", nil, m.userSubHandler(notifysub.KindVLive, true))
	r.Register("pjsk取消live提醒", nil, m.userSubHandler(notifysub.KindVLive, false))
	// 订阅状态
	r.Register("pjsk订阅状态", nil, m.handleStatus)
}

// serverShort 把 pjsk_type 映射到 jp/cn/tw（订阅库 server 字段）。
func serverShort(serverType int) string {
	switch serverType {
	case 1:
		return "tw"
	case 2:
		return "cn"
	default:
		return "jp"
	}
}

// isAdmin 判定发送者是否群管理员/群主/超级用户。
func (m *SubscribeModule) isAdmin(e onebot.MessageEvent) bool {
	if m.super[e.UserID] {
		return true
	}
	return e.Sender.Role == "admin" || e.Sender.Role == "owner"
}

// groupSubHandler 生成群订阅开/关处理器（管理员权限）。
func (m *SubscribeModule) groupSubHandler(kind string, on bool) router.Handler {
	return func(ctx context.Context, req router.Request) *onebot.ActionRequest {
		if !req.Event.IsGroup() {
			return nil
		}
		if !m.isAdmin(req.Event) {
			return nil
		}
		server := serverShort(int(req.Server))
		name := serverNameCN[int(req.Server)]
		groupID := itoa64(req.Event.GroupID)
		label := kindLabel[kind]
		if on {
			added, err := m.subs.Add(ctx, groupID, "", server, kind)
			if err != nil {
				return onebot.ReplyText(req.Event, errBug, false)
			}
			if added {
				return onebot.ReplyText(req.Event, "✅ 已开启本群"+label+"（"+name+"）", false)
			}
			return onebot.ReplyText(req.Event, "本群已开启"+label+"（"+name+"），无需重复操作", false)
		}
		removed, err := m.subs.RemoveGroup(ctx, groupID, server, kind)
		if err != nil {
			return onebot.ReplyText(req.Event, errBug, false)
		}
		if removed > 0 {
			if removed > 1 {
				return onebot.ReplyText(req.Event,
					"✅ 已关闭本群"+label+"（"+name+"），并清理了 "+itoa64(int64(removed-1))+" 条个人提醒", false)
			}
			return onebot.ReplyText(req.Event, "✅ 已关闭本群"+label+"（"+name+"）", false)
		}
		return onebot.ReplyText(req.Event, "本群没有开启"+label+"（"+name+"）", false)
	}
}

// newCardGroupHandler 生成仅日服的新卡群订阅开关。
func (m *SubscribeModule) newCardGroupHandler(on bool) router.Handler {
	return func(ctx context.Context, req router.Request) *onebot.ActionRequest {
		if int(req.Server) != 0 {
			return onebot.ReplyText(req.Event, "新卡速递订阅仅支持日服", false)
		}
		if !req.Event.IsGroup() || !m.isAdmin(req.Event) {
			return nil
		}
		groupID := itoa64(req.Event.GroupID)
		if on {
			added, err := m.subs.Add(ctx, groupID, "", "jp", notifysub.KindNewCard)
			if err != nil {
				return onebot.ReplyText(req.Event, errBug, false)
			}
			if added {
				return onebot.ReplyText(req.Event, "✅ 已开启本群新卡速递（日服）", false)
			}
			return onebot.ReplyText(req.Event, "本群已开启新卡速递（日服），无需重复操作", false)
		}
		removed, err := m.subs.RemoveGroup(ctx, groupID, "jp", notifysub.KindNewCard)
		if err != nil {
			return onebot.ReplyText(req.Event, errBug, false)
		}
		if removed > 0 {
			return onebot.ReplyText(req.Event, "✅ 已关闭本群新卡速递（日服）", false)
		}
		return onebot.ReplyText(req.Event, "本群没有开启新卡速递（日服）", false)
	}
}

// handleNewCard 手动发送最新一批日服新卡情报。
func (m *SubscribeModule) handleNewCard(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if int(req.Server) != 0 {
		return onebot.ReplyText(req.Event, "新卡速递仅支持日服", false)
	}
	if m.newCard == nil {
		return onebot.ReplyText(req.Event, "新卡速递暂不可用，请稍后再试", false)
	}
	item, ok, err := m.newCard.BuildLatest(ctx, true)
	if err != nil {
		return onebot.ReplyText(req.Event, "获取新卡情报失败："+err.Error(), false)
	}
	if !ok || len(item.ForwardNodes) == 0 {
		return onebot.ReplyText(req.Event, "当前没有可用新卡情报", false)
	}
	return onebot.SendForwardAction(req.Event, item.ForwardNodes)
}

// userSubHandler 生成个人 @ 提醒订阅/取消处理器（群内任意成员）。
func (m *SubscribeModule) userSubHandler(kind string, on bool) router.Handler {
	return func(ctx context.Context, req router.Request) *onebot.ActionRequest {
		if !req.Event.IsGroup() {
			return nil
		}
		server := serverShort(int(req.Server))
		name := serverNameCN[int(req.Server)]
		groupID := itoa64(req.Event.GroupID)
		userID := itoa64(req.Event.UserID)
		label := kindLabel[kind]
		if on {
			subbed, err := m.subs.IsGroupSubbed(ctx, kind, server, groupID)
			if err != nil {
				return onebot.ReplyText(req.Event, errBug, false)
			}
			if !subbed {
				which := "新曲"
				if kind == notifysub.KindVLive {
					which = "live"
				}
				return onebot.ReplyText(req.Event,
					"本群还没有开启"+label+"（"+name+"），请先让管理员发送 pjsk开启"+which+"通知", true)
			}
			added, err := m.subs.Add(ctx, groupID, userID, server, kind)
			if err != nil {
				return onebot.ReplyText(req.Event, errBug, false)
			}
			if added {
				return onebot.ReplyText(req.Event, "✅ 已订阅"+label+"（"+name+"）的@提醒", true)
			}
			return onebot.ReplyText(req.Event, "你已经订阅过了哦", true)
		}
		removed, err := m.subs.Remove(ctx, groupID, userID, server, kind)
		if err != nil {
			return onebot.ReplyText(req.Event, errBug, false)
		}
		if removed {
			return onebot.ReplyText(req.Event, "✅ 已取消"+label+"（"+name+"）的@提醒", true)
		}
		return onebot.ReplyText(req.Event, "你没有订阅过这个提醒哦", true)
	}
}

// handleStatus 展示本群全部订阅记录。对齐 sub_status。
func (m *SubscribeModule) handleStatus(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if !req.Event.IsGroup() {
		return nil
	}
	subs, err := m.subs.GroupStatus(ctx, itoa64(req.Event.GroupID))
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	if len(subs) == 0 {
		return onebot.ReplyText(req.Event, "本群没有任何pjsk订阅。可用：pjsk开启新曲通知 / pjsk开启live通知 / pjsk开启新卡通知", false)
	}
	nameByShort := map[string]string{"jp": "日服", "cn": "国服", "tw": "台服"}
	lines := []string{"本群pjsk订阅："}
	for _, s := range subs {
		name := nameByShort[s.Server]
		if name == "" {
			name = s.Server
		}
		label := kindLabel[s.Kind]
		if label == "" {
			label = s.Kind
		}
		if s.QQID == "" {
			lines = append(lines, "· "+label+"（"+name+"）：群推送已开启")
		} else {
			lines = append(lines, "  - @提醒："+s.QQID+"（"+label+"/"+name+"）")
		}
	}
	return onebot.ReplyText(req.Event, strings.Join(lines, "\n"), false)
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
