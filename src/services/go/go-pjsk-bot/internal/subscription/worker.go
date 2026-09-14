// Package subscription 提供 Go 侧后台订阅轮询与主动推送骨架。
//
// 该包只负责共享 SQLite 订阅遍历、去重、推送和状态回写；具体的新曲、
// 虚拟 Live、MSR、SK 数据抓取通过可注入 Source 接口提供。未配置 Source
// 时会明确返回 ErrSourceUnavailable，不会伪造“推送成功”。
package subscription

import (
	"context"
	"errors"
	"fmt"
	"log"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/msrsub"
	"github.com/kazuhira/go-pjsk-bot/internal/notifysub"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/sksub"
)

const (
	maxSeenKeys       = 5000
	newCardBaselineID = "__baseline__"
)

var (
	// ErrSourceUnavailable 表示该类订阅的数据源尚未接入或当前不可用。
	ErrSourceUnavailable = errors.New("subscription data source unavailable")
	// ErrInvalidSubscriptionTarget 表示 SQLite 中的群号/QQ 号无法转换为 OneBot ID。
	ErrInvalidSubscriptionTarget = errors.New("invalid subscription target")
)

// Messenger 是 NotifyWorker 所需的最小主动发送能力，便于单元测试替换 OneBot 客户端。
type Messenger interface {
	SendGroupMessage(groupID int64, msg onebot.Message) error
	SendPrivateMessage(userID int64, msg onebot.Message) error
}

// ForwardMessenger 是可选的 OneBot 合并转发能力。
type ForwardMessenger interface {
	SendGroupForwardMessageContext(ctx context.Context, groupID int64, nodes []onebot.ForwardNode) error
	SendPrivateForwardMessageContext(ctx context.Context, userID int64, nodes []onebot.ForwardNode) error
}

// FeedItem 是新曲/虚拟 Live/MSR/SK/新卡统一使用的可去重消息。
type FeedItem struct {
	// ID 必须在同一类订阅源内稳定；为空时 Worker 会跳过，避免每轮重复推送。
	ID string
	// Message 用于普通 send_group_msg；ForwardNodes 非空时优先使用合并转发。
	Message      onebot.Message
	ForwardNodes []onebot.ForwardNode
}

// MusicSource 提供新曲订阅数据。
type MusicSource interface {
	FetchMusic(context.Context, string) ([]FeedItem, error)
}

// VLiveSource 提供虚拟 Live 订阅数据。
type VLiveSource interface {
	FetchVLive(context.Context, string) ([]FeedItem, error)
}

// NewCardSource 提供日服新卡订阅数据。
type NewCardSource interface {
	FetchNewCard(context.Context, string) ([]FeedItem, error)
}

type MsrSource interface {
	FetchMSR(context.Context, msrsub.Subscription) (FeedItem, bool, error)
}

// MsrBatchItem 是一次批量 MSR 查询返回的单条结果。
type MsrBatchItem struct {
	SubscriptionID int64
	FeedItem       FeedItem
	Ready          bool
}

// MsrBatchSource 可在一轮内按服务器批量获取 upload_time，避免每条订阅重复请求。
type MsrBatchSource interface {
	FetchMSRBatch(context.Context, []msrsub.Subscription) ([]MsrBatchItem, error)
}

// SKUpdate 是单条 SK 订阅的一轮观测结果。Changed=false 时只回写状态，不发送消息。
type SKUpdate struct {
	FeedItem
	Score   int64
	Rank    int
	Changed bool
}

// SKSource 提供单条 SK 订阅的最新分数/排名观测。
type SKSource interface {
	FetchSK(context.Context, sksub.Subscription) (SKUpdate, error)
}

// MusicSourceFunc 等函数适配器让调用方可直接注入闭包。
type MusicSourceFunc func(context.Context, string) ([]FeedItem, error)

func (f MusicSourceFunc) FetchMusic(ctx context.Context, server string) ([]FeedItem, error) {
	return f(ctx, server)
}

type VLiveSourceFunc func(context.Context, string) ([]FeedItem, error)

func (f VLiveSourceFunc) FetchVLive(ctx context.Context, server string) ([]FeedItem, error) {
	return f(ctx, server)
}

type NewCardSourceFunc func(context.Context, string) ([]FeedItem, error)

func (f NewCardSourceFunc) FetchNewCard(ctx context.Context, server string) ([]FeedItem, error) {
	return f(ctx, server)
}

type MsrSourceFunc func(context.Context, msrsub.Subscription) (FeedItem, bool, error)

func (f MsrSourceFunc) FetchMSR(ctx context.Context, sub msrsub.Subscription) (FeedItem, bool, error) {
	return f(ctx, sub)
}

type SKSourceFunc func(context.Context, sksub.Subscription) (SKUpdate, error)

func (f SKSourceFunc) FetchSK(ctx context.Context, sub sksub.Subscription) (SKUpdate, error) {
	return f(ctx, sub)
}

// WorkerOptions 配置通知 Worker。
type WorkerOptions struct {
	Notify *notifysub.Store
	MSR    *msrsub.Store
	SK     *sksub.Store
	Sender Messenger

	Music   MusicSource
	VLive   VLiveSource
	NewCard NewCardSource
	MSRFeed MsrSource
	SKFeed  SKSource

	Logf func(string, ...any)
}

// NotifyWorker 负责一轮订阅遍历。它本身不创建后台 goroutine，由 SubscribeScheduler 调度。
type NotifyWorker struct {
	notify *notifysub.Store
	msr    *msrsub.Store
	sk     *sksub.Store
	sender Messenger

	music   MusicSource
	vlive   VLiveSource
	newCard NewCardSource
	msrFeed MsrSource
	skFeed  SKSource
	logf    func(string, ...any)

	mu        sync.Mutex
	seen      map[string]struct{}
	seenOrder []string
	warn      map[string]struct{}
}

// NewNotifyWorker 创建后台推送 Worker。Store 或 Sender 为空时，相关轮询会安全跳过并返回错误。
func NewNotifyWorker(opts WorkerOptions) *NotifyWorker {
	logf := opts.Logf
	if logf == nil {
		logf = log.Printf
	}
	return &NotifyWorker{
		notify:    opts.Notify,
		msr:       opts.MSR,
		sk:        opts.SK,
		sender:    opts.Sender,
		music:     opts.Music,
		vlive:     opts.VLive,
		newCard:   opts.NewCard,
		msrFeed:   opts.MSRFeed,
		skFeed:    opts.SKFeed,
		logf:      logf,
		seen:      make(map[string]struct{}),
		seenOrder: make([]string, 0, maxSeenKeys),
		warn:      make(map[string]struct{}),
	}
}

// PollMusic 扫描新曲订阅并按群主动推送。
func (w *NotifyWorker) PollMusic(ctx context.Context) error {
	if w.music == nil {
		return w.unavailable("music")
	}
	return w.pollNotify(ctx, notifysub.KindMusic, w.music.FetchMusic)
}

// PollVLive 扫描虚拟 Live 订阅并按群主动推送。
func (w *NotifyWorker) PollVLive(ctx context.Context) error {
	if w.vlive == nil {
		return w.unavailable("vlive")
	}
	return w.pollNotify(ctx, notifysub.KindVLive, w.vlive.FetchVLive)
}

// PollNewCard 扫描日服新卡群订阅，并在首次轮询时建立不推送历史的基线。
func (w *NotifyWorker) PollNewCard(ctx context.Context) error {
	if w.newCard == nil {
		return w.unavailable("new_card")
	}
	if w.notify == nil {
		return errors.New("notify subscription store is nil")
	}
	if w.sender == nil {
		return errors.New("notification sender is nil")
	}
	groups, err := w.notify.ListGroups(ctx, notifysub.KindNewCard, "jp")
	if err != nil {
		return err
	}
	byGroup := make(map[string]struct{}, len(groups))
	for _, group := range groups {
		if _, err := strconv.ParseInt(group.GroupID, 10, 64); err != nil {
			w.logf("[subscription] 忽略无效群号 kind=%s group=%q: %v", notifysub.KindNewCard, group.GroupID, err)
			continue
		}
		byGroup[group.GroupID] = struct{}{}
	}
	if len(byGroup) == 0 {
		return nil
	}
	items, err := w.newCard.FetchNewCard(ctx, "jp")
	if err != nil {
		w.logf("[subscription] new_card/jp 数据获取失败: %v", err)
		return nil
	}
	baseline, err := w.notify.WasSent(ctx, notifysub.KindNewCard, "jp", newCardBaselineID)
	if err != nil {
		return err
	}
	if !baseline {
		baselineIDs := []string{newCardBaselineID}
		for _, item := range items {
			if item.ID == "" {
				w.logf("[subscription] new_card/jp 数据缺少稳定 ID，跳过")
				continue
			}
			for groupID := range byGroup {
				baselineIDs = append(baselineIDs, item.ID+"/"+groupID)
			}
		}
		if err := w.notify.MarkSentBatch(ctx, notifysub.KindNewCard, "jp", baselineIDs, time.Now()); err != nil {
			return fmt.Errorf("new_card/jp 建立基线失败: %w", err)
		}
		w.logf("[subscription] new_card/jp 已建立 %d 个候选群的历史基线", len(byGroup))
		return nil
	}
	for _, item := range items {
		if err := ctx.Err(); err != nil {
			return err
		}
		if item.ID == "" {
			w.logf("[subscription] new_card/jp 数据缺少稳定 ID，跳过")
			continue
		}
		for groupID := range byGroup {
			stateID := item.ID + "/" + groupID
			memoryKey := "new_card/jp/" + item.ID + "/" + groupID
			if w.notifyWasSent(ctx, notifysub.KindNewCard, "jp", stateID, memoryKey) {
				continue
			}
			if err := w.sendFeed(ctx, groupID, item, nil); err != nil {
				w.logf("[subscription] new_card/jp 推送到群 %s 失败: %v", groupID, err)
				continue
			}
			if err := w.notify.MarkSent(ctx, notifysub.KindNewCard, "jp", stateID, time.Now()); err != nil {
				w.logf("[subscription] new_card/jp 去重状态回写失败 group=%s: %v", groupID, err)
				continue
			}
			w.markSeen(memoryKey)
		}
	}
	return nil
}

// PollMSR 扫描 MSR 订阅；成功发送后才推进 last_push_time。
func (w *NotifyWorker) PollMSR(ctx context.Context) error {
	if w.msrFeed == nil {
		return w.unavailable("msr")
	}
	if w.msr == nil {
		return errors.New("msr subscription store is nil")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	subs, err := w.msr.List(ctx, "")
	if err != nil {
		return err
	}
	if batch, ok := w.msrFeed.(MsrBatchSource); ok {
		results, err := batch.FetchMSRBatch(ctx, subs)
		if err != nil {
			w.logf("[subscription] MSR 批量数据获取失败: %v", err)
			return nil
		}
		byID := make(map[int64]MsrBatchItem, len(results))
		for _, result := range results {
			if result.SubscriptionID > 0 {
				byID[result.SubscriptionID] = result
			}
		}
		for _, sub := range subs {
			if err := ctx.Err(); err != nil {
				return err
			}
			result, exists := byID[sub.ID]
			if !exists || !result.Ready {
				continue
			}
			w.deliverMSR(ctx, sub, result.FeedItem)
		}
		return nil
	}
	for _, sub := range subs {
		if err := ctx.Err(); err != nil {
			return err
		}
		item, ready, err := w.msrFeed.FetchMSR(ctx, sub)
		if err != nil {
			w.logf("[subscription] MSR 数据获取失败 id=%d server=%s: %v", sub.ID, sub.Server, err)
			continue
		}
		if ready {
			w.deliverMSR(ctx, sub, item)
		}
	}
	return nil
}

func (w *NotifyWorker) deliverMSR(ctx context.Context, sub msrsub.Subscription, item FeedItem) {
	if item.ID == "" {
		w.logf("[subscription] MSR 数据缺少稳定 ID，跳过 id=%d", sub.ID)
		return
	}
	key := fmt.Sprintf("msr/%d/%s", sub.ID, item.ID)
	if w.wasSeen(key) {
		return
	}
	msg := withAt(item.Message, sub.QQID)
	if err := w.sendSubscription(ctx, sub.GroupID, sub.QQID, msg); err != nil {
		w.logf("[subscription] MSR 推送失败 id=%d: %v", sub.ID, err)
		return
	}
	if ok, err := w.msr.UpdateLastPushNow(ctx, sub.ID); err != nil {
		w.logf("[subscription] MSR 状态回写失败 id=%d: %v", sub.ID, err)
		return
	} else if !ok {
		w.logf("[subscription] MSR 状态回写未命中 id=%d", sub.ID)
		return
	}
	w.markSeen(key)
}

// PollSK 扫描 SK 订阅。Source 可只回写观测状态，也可在 Changed=true 时发送通知。
func (w *NotifyWorker) PollSK(ctx context.Context) error {
	if w.skFeed == nil {
		return w.unavailable("sk")
	}
	if w.sk == nil {
		return errors.New("sk subscription store is nil")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	subs, err := w.sk.List(ctx, "", 0)
	if err != nil {
		return err
	}
	for _, sub := range subs {
		if err := ctx.Err(); err != nil {
			return err
		}
		update, err := w.skFeed.FetchSK(ctx, sub)
		if err != nil {
			reason, business := SourceStatusReasonOf(err)
			if business {
				switch reason {
				case ActivityClosed:
					deleted, removeErr := w.sk.RemoveByEvent(ctx, sub.Server, sub.EventID)
					if removeErr != nil {
						w.logf("[subscription] SK 活动订阅清理失败 server=%s event=%d: %v", sub.Server, sub.EventID, removeErr)
					} else if deleted > 0 {
						w.logf("[subscription] SK 活动已结束，删除 %d 个订阅 server=%s event=%d", deleted, sub.Server, sub.EventID)
					}
				case PlayerNoRanking:
					removed, removeErr := w.sk.RemoveByID(ctx, sub.ID)
					if removeErr != nil {
						w.logf("[subscription] SK 无榜线订阅清理失败 id=%d: %v", sub.ID, removeErr)
						continue
					}
					if removed {
						w.sendSKCancellation(ctx, sub)
					}
				default:
					w.logf("[subscription] SK 未知业务状态 id=%d: %v", sub.ID, err)
				}
				continue
			}
			w.logf("[subscription] SK 数据获取失败 id=%d server=%s event=%d: %v", sub.ID, sub.Server, sub.EventID, err)
			continue
		}
		key := fmt.Sprintf("sk/%d/%s", sub.ID, update.ID)
		if update.Changed {
			if update.ID == "" {
				w.logf("[subscription] SK 变更缺少稳定 ID，跳过 id=%d", sub.ID)
				continue
			}
			if !w.wasSeen(key) {
				msg := withAt(update.Message, sub.QQID)
				if err := w.sendSubscription(ctx, sub.GroupID, sub.QQID, msg); err != nil {
					w.logf("[subscription] SK 推送失败 id=%d: %v", sub.ID, err)
					continue
				}
			}
		}
		if ok, err := w.sk.UpdateStatus(ctx, sub.ID, update.Score, update.Rank, time.Now()); err != nil {
			w.logf("[subscription] SK 状态回写失败 id=%d: %v", sub.ID, err)
			continue
		} else if !ok {
			w.logf("[subscription] SK 状态回写未命中 id=%d", sub.ID)
			continue
		}
		if update.Changed {
			w.markSeen(key)
		}
	}
	return nil
}

// pollNotify 共享新曲与虚拟 Live 的群订阅遍历和去重逻辑。
func (w *NotifyWorker) pollNotify(ctx context.Context, kind string, fetch func(context.Context, string) ([]FeedItem, error)) error {
	if w.notify == nil {
		return errors.New("notify subscription store is nil")
	}
	if w.sender == nil {
		return errors.New("notification sender is nil")
	}
	groups, err := w.notify.ListGroups(ctx, kind, "")
	if err != nil {
		return err
	}
	byServer := make(map[string]map[string][]string)
	for _, sub := range groups {
		if _, err := strconv.ParseInt(sub.GroupID, 10, 64); err != nil {
			w.logf("[subscription] 忽略无效群号 kind=%s group=%q: %v", kind, sub.GroupID, err)
			continue
		}
		if byServer[sub.Server] == nil {
			byServer[sub.Server] = make(map[string][]string)
		}
		byServer[sub.Server][sub.GroupID] = nil
	}
	if len(byServer) == 0 {
		return nil
	}
	// 个人提醒必须跟随同群的群级订阅，保持 Python 侧语义。
	all, err := w.notify.List(ctx, kind, "")
	if err != nil {
		return err
	}
	for _, sub := range all {
		if sub.QQID == "" {
			continue
		}
		if groupsForServer := byServer[sub.Server]; groupsForServer != nil {
			if _, ok := groupsForServer[sub.GroupID]; ok {
				groupsForServer[sub.GroupID] = append(groupsForServer[sub.GroupID], sub.QQID)
			}
		}
	}
	for server, targets := range byServer {
		if err := ctx.Err(); err != nil {
			return err
		}
		items, err := fetch(ctx, server)
		if err != nil {
			w.logf("[subscription] %s/%s 数据获取失败: %v", kind, server, err)
			continue
		}
		for _, item := range items {
			if item.ID == "" {
				w.logf("[subscription] %s/%s 数据缺少稳定 ID，跳过", kind, server)
				continue
			}
			for groupID, users := range targets {
				key := fmt.Sprintf("%s/%s/%s/%s", kind, server, item.ID, groupID)
				stateID := item.ID + "/" + groupID
				if w.notifyWasSent(ctx, kind, server, stateID, key) {
					continue
				}
				if err := w.sendFeed(ctx, groupID, item, users); err != nil {
					w.logf("[subscription] %s/%s 推送到群 %s 失败: %v", kind, server, groupID, err)
					continue
				}
				w.markSeen(key)
				if err := w.notify.MarkSent(ctx, kind, server, stateID, time.Now()); err != nil {
					w.logf("[subscription] %s/%s 去重状态回写失败 group=%s: %v", kind, server, groupID, err)
				}
			}
		}
	}
	return nil
}

func (w *NotifyWorker) sendSKCancellation(ctx context.Context, sub sksub.Subscription) {
	if w.sender == nil {
		w.logf("[subscription] SK 无榜线取消通知跳过：sender 为空 id=%d", sub.ID)
		return
	}
	groupID, err := strconv.ParseInt(sub.GroupID, 10, 64)
	if err != nil || groupID <= 0 {
		w.logf("[subscription] SK 无榜线取消通知跳过无效群号 id=%d group=%q", sub.ID, sub.GroupID)
		return
	}
	text := fmt.Sprintf("\n由于无法查询到你的排名数据（可能未进入记录范围），已自动取消%s服活动%d的订阅", strings.ToUpper(sub.Server), sub.EventID)
	msg := onebot.Message{onebot.Text(text)}
	if qq, err := strconv.ParseInt(sub.QQID, 10, 64); err == nil && qq > 0 {
		msg = onebot.Message{onebot.At(qq), onebot.Text(text)}
	}
	if err := w.sender.SendGroupMessage(groupID, msg); err != nil {
		w.logf("[subscription] SK 无榜线取消通知发送失败 id=%d: %v", sub.ID, err)
	}
}

func (w *NotifyWorker) sendFeed(ctx context.Context, groupID string, item FeedItem, users []string) error {
	if len(item.ForwardNodes) > 0 {
		forward, ok := w.sender.(ForwardMessenger)
		if !ok {
			return errors.New("notification sender does not support group forward")
		}
		id, err := strconv.ParseInt(groupID, 10, 64)
		if err != nil || id <= 0 {
			return fmt.Errorf("%w: group_id=%q", ErrInvalidSubscriptionTarget, groupID)
		}
		nodes := append([]onebot.ForwardNode(nil), item.ForwardNodes...)
		if len(users) > 0 && len(nodes) > 0 {
			content := withAtUsers(nodes[0].Data.Content, users)
			nodes[0].Data.Content = content
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		return forward.SendGroupForwardMessageContext(ctx, id, nodes)
	}
	return w.sendGroup(ctx, groupID, withAtUsers(item.Message, users))
}

func (w *NotifyWorker) sendGroup(ctx context.Context, groupID string, msg onebot.Message) error {
	if w.sender == nil {
		return errors.New("notification sender is nil")
	}
	id, err := strconv.ParseInt(groupID, 10, 64)
	if err != nil || id <= 0 {
		return fmt.Errorf("%w: group_id=%q", ErrInvalidSubscriptionTarget, groupID)
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	return w.sender.SendGroupMessage(id, msg)
}

func (w *NotifyWorker) sendSubscription(ctx context.Context, groupID, qqID string, msg onebot.Message) error {
	if w.sender == nil {
		return errors.New("notification sender is nil")
	}
	if groupID != "" {
		return w.sendGroup(ctx, groupID, msg)
	}
	id, err := strconv.ParseInt(qqID, 10, 64)
	if err != nil || id <= 0 {
		return fmt.Errorf("%w: qq_id=%q", ErrInvalidSubscriptionTarget, qqID)
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	return w.sender.SendPrivateMessage(id, msg)
}

func withAtUsers(msg onebot.Message, users []string) onebot.Message {
	if len(users) == 0 {
		return append(onebot.Message(nil), msg...)
	}
	out := make(onebot.Message, 0, len(users)+len(msg))
	for _, qq := range users {
		id, err := strconv.ParseInt(qq, 10, 64)
		if err == nil && id > 0 {
			out = append(out, onebot.At(id))
		}
	}
	out = append(out, msg...)
	return out
}

func withAt(msg onebot.Message, qq string) onebot.Message {
	return withAtUsers(msg, []string{qq})
}

func (w *NotifyWorker) wasSeen(key string) bool {
	w.mu.Lock()
	defer w.mu.Unlock()
	_, ok := w.seen[key]
	return ok
}

func (w *NotifyWorker) notifyWasSent(ctx context.Context, kind, server, stateID, memoryKey string) bool {
	if w.wasSeen(memoryKey) {
		return true
	}
	if w.notify == nil {
		return false
	}
	sent, err := w.notify.WasSent(ctx, kind, server, stateID)
	if err != nil {
		w.logf("[subscription] %s/%s 读取去重状态失败: %v", kind, server, err)
		return false
	}
	if sent {
		w.markSeen(memoryKey)
	}
	return sent
}

func (w *NotifyWorker) markSeen(key string) {
	w.mu.Lock()
	defer w.mu.Unlock()
	if _, exists := w.seen[key]; exists {
		return
	}
	w.seen[key] = struct{}{}
	w.seenOrder = append(w.seenOrder, key)
	if len(w.seenOrder) > maxSeenKeys {
		oldest := w.seenOrder[0]
		delete(w.seen, oldest)
		w.seenOrder = w.seenOrder[1:]
	}
}

func (w *NotifyWorker) unavailable(kind string) error {
	w.mu.Lock()
	_, warned := w.warn[kind]
	if !warned {
		w.warn[kind] = struct{}{}
	}
	w.mu.Unlock()
	if !warned {
		w.logf("[subscription] %s 数据源未接入，跳过本轮轮询", kind)
	}
	return fmt.Errorf("%w: %s", ErrSourceUnavailable, kind)
}
