package pjsk

import (
	"context"
	"encoding/base64"
	"fmt"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/skranking"
	"github.com/kazuhira/go-pjsk-bot/internal/skstore"
	"github.com/kazuhira/go-pjsk-bot/internal/sksub"
	"github.com/kazuhira/go-pjsk-bot/internal/subscription"
)

// SKSubscriptionSource 从本地主数据和榜线时序库生成 SK 订阅结果。
type SKSubscriptionSource struct {
	md    *masterdata.Loader
	store *skstore.Store
	draw  *draw.Client
	now   func() time.Time
}

// SkSubscriptionSource 是 SKSubscriptionSource 的兼容别名。
type SkSubscriptionSource = SKSubscriptionSource

// NewSKSubscriptionSource 创建 SK 订阅数据源。
func NewSKSubscriptionSource(md *masterdata.Loader, store *skstore.Store, d *draw.Client) *SKSubscriptionSource {
	return &SKSubscriptionSource{md: md, store: store, draw: d, now: time.Now}
}

// NewSkSubscriptionSource 是兼容常见命名的构造器别名。
func NewSkSubscriptionSource(md *masterdata.Loader, store *skstore.Store, d *draw.Client) *SKSubscriptionSource {
	return NewSKSubscriptionSource(md, store, d)
}

// FetchSK 查询订阅玩家最新榜线；Changed 仅表示分数变化，保持 Python 订阅语义。
func (s *SKSubscriptionSource) FetchSK(ctx context.Context, sub sksub.Subscription) (subscription.SKUpdate, error) {
	if s.md == nil {
		return subscription.SKUpdate{}, fmt.Errorf("masterdata loader is nil")
	}
	if s.store == nil {
		return subscription.SKUpdate{}, fmt.Errorf("sk ranking store is nil")
	}

	serverType := serverTypeFromShort(sub.Server)
	events, err := s.md.Load("events.json", serverType)
	if err != nil {
		return subscription.SKUpdate{}, err
	}
	now := time.Now()
	if s.now != nil {
		now = s.now()
	}
	if !skSubscriptionEventActive(events, sub.EventID, now.UnixMilli()) {
		return subscription.SKUpdate{}, subscription.SourceStatusError{Reason: subscription.ActivityClosed}
	}

	if sub.UID == "" || sub.UID == "0" {
		return subscription.SKUpdate{}, subscription.SourceStatusError{Reason: subscription.PlayerNoRanking}
	}
	history, err := s.store.QueryRankingByUID(ctx, sub.Server, sub.EventID, sub.UID)
	if err != nil {
		return subscription.SKUpdate{}, err
	}
	if len(history) == 0 {
		return subscription.SKUpdate{}, subscription.SourceStatusError{Reason: subscription.PlayerNoRanking}
	}
	latest := history[len(history)-1]
	update := subscription.SKUpdate{
		Score:   latest.Score,
		Rank:    latest.Rank,
		Changed: latest.Score != sub.LastScore,
	}
	if !update.Changed {
		return update, nil
	}

	stats := skranking.BuildActivityStats(history, latest, 0)
	var stopSeconds any
	if stats.StopDuration != nil {
		stopSeconds = stats.StopDuration.Seconds()
	}
	img, err := s.renderChange(ctx, latest, stats, stopSeconds)
	if err != nil {
		return subscription.SKUpdate{}, err
	}
	update.FeedItem = subscription.FeedItem{
		ID: fmt.Sprintf("%s/%d/%s/%d/%d", sub.Server, sub.EventID, sub.UID, latest.Time.UnixNano(), latest.Score),
		Message: onebot.Message{
			onebot.ImageBytes(base64.StdEncoding.EncodeToString(img)),
		},
	}
	return update, nil
}

func (s *SKSubscriptionSource) renderChange(ctx context.Context, latest skranking.Ranking, stats skranking.ActivityStats, stopSeconds any) ([]byte, error) {
	if s.draw == nil {
		return nil, fmt.Errorf("draw client is nil")
	}
	return s.draw.Render(ctx, "sk_cf", map[string]any{
		"name":             latest.Name,
		"uid":              latest.UID,
		"score":            latest.Score,
		"rank":             latest.Rank,
		"hourly_speed":     stats.HourlySpeed,
		"twenty_min_speed": stats.TwentyMinSpeed,
		"play_count":       stats.PlayCount,
		"avg_pt":           stats.AvgPt,
		"last_pt":          stats.LastPt,
		"is_playing":       stats.IsPlaying,
		"stop_seconds":     stopSeconds,
	})
}

func skSubscriptionEventActive(events []map[string]any, eventID int, nowMS int64) bool {
	var target map[string]any
	var current map[string]any
	var currentStart int64 = -1
	for _, event := range events {
		id := int(mdInt(event, "id"))
		if id == eventID {
			target = event
		}
		start := mdInt(event, "startAt")
		end := eventEndAt(event)
		if eventClosed(event) || (end > 0 && nowMS >= end) {
			continue
		}
		if start <= nowMS && (end == 0 || nowMS < end) && start >= currentStart {
			current = event
			currentStart = start
		}
	}
	if target == nil || current == nil {
		return false
	}
	return mdInt(target, "id") == mdInt(current, "id")
}

func mdInt(item map[string]any, key string) int64 {
	value, _ := masterdata.IntField(item, key)
	return value
}

func eventEndAt(event map[string]any) int64 {
	for _, field := range []string{"aggregateAt", "closedAt", "endAt"} {
		if value := mdInt(event, field); value > 0 {
			return value
		}
	}
	return 0
}

func eventClosed(event map[string]any) bool {
	switch strings.ToLower(strField(event, "status")) {
	case "closed", "close", "ended", "end":
		return true
	default:
		return false
	}
}

var _ subscription.SKSource = (*SKSubscriptionSource)(nil)
