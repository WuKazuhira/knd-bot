package pjsk

import (
	"context"
	"encoding/base64"
	"fmt"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/subscription"
)

const (
	musicNotifyLookback = 6 * time.Hour
	musicNotifyAhead    = time.Minute
	vliveNotifyBefore   = 3 * time.Minute
)

// MasterdataSubscriptionSource 从共享主数据生成新曲/虚拟 Live 推送图片。
type MasterdataSubscriptionSource struct {
	md   *masterdata.Loader
	draw *draw.Client
	now  func() time.Time
}

func NewMasterdataSubscriptionSource(md *masterdata.Loader, d *draw.Client) *MasterdataSubscriptionSource {
	return &MasterdataSubscriptionSource{md: md, draw: d, now: time.Now}
}

func (s *MasterdataSubscriptionSource) FetchMusic(ctx context.Context, server string) ([]subscription.FeedItem, error) {
	serverType := serverTypeFromShort(server)
	musics, err := s.md.Load("musics.json", serverType)
	if err != nil {
		return nil, err
	}
	now := s.now()
	pending := make([]map[string]any, 0)
	for _, music := range musics {
		published := int64(intField(music, "publishedAt"))
		if published == 0 {
			continue
		}
		publishedAt := time.UnixMilli(published)
		if now.Sub(publishedAt) > musicNotifyLookback || publishedAt.Sub(now) > musicNotifyAhead {
			continue
		}
		pending = append(pending, music)
	}
	if len(pending) == 0 {
		return nil, nil
	}
	if s.draw == nil {
		return nil, fmt.Errorf("draw client is nil")
	}
	rows := make([][]string, 0, len(pending))
	items := make([]subscription.FeedItem, 0, len(pending))
	for _, music := range pending {
		id := intField(music, "id")
		published := int64(intField(music, "publishedAt"))
		rows = append(rows, []string{
			fmt.Sprintf("【%d】%s", id, strField(music, "title")),
			fmt.Sprintf("作曲: %s  作词: %s", fallbackString(strField(music, "composer"), "-"), fallbackString(strField(music, "lyricist"), "-")),
			"上线时间: " + time.UnixMilli(published).Format("01-02 15:04"),
		})
	}
	img, err := s.draw.Render(ctx, "notify_rows", map[string]any{
		"title":  fmt.Sprintf("%s新曲上线 - %d首", serverNameCN[serverType], len(pending)),
		"rows":   rows,
		"footer": "KNDBOT · 新曲通知",
	})
	if err != nil {
		return nil, err
	}
	message := onebot.Message{onebot.ImageBytes(base64.StdEncoding.EncodeToString(img))}
	for _, music := range pending {
		id := intField(music, "id")
		items = append(items, subscription.FeedItem{ID: fmt.Sprintf("music/%s/%d", server, id), Message: message})
	}
	return items, nil
}

func (s *MasterdataSubscriptionSource) FetchVLive(ctx context.Context, server string) ([]subscription.FeedItem, error) {
	serverType := serverTypeFromShort(server)
	vlives, err := s.md.Load("virtualLives.json", serverType)
	if err != nil {
		return nil, err
	}
	now := s.now()
	items := make([]subscription.FeedItem, 0)
	for _, live := range vlives {
		if strField(live, "virtualLiveType") == "beginner" {
			continue
		}
		start := int64(intField(live, "startAt"))
		end := int64(intField(live, "endAt"))
		if end <= now.UnixMilli() || end-start >= int64(30*24*time.Hour/time.Millisecond) {
			continue
		}
		schedules := scheduleTimes(live)
		if schedules[0] == 0 {
			continue
		}
		kind, notifyAt := "start", schedules[0]
		if now.UnixMilli() < notifyAt && time.Until(time.UnixMilli(notifyAt)) <= vliveNotifyBefore {
			kind = "start"
		} else if schedules[1] != schedules[0] && now.UnixMilli() < schedules[1] && time.Until(time.UnixMilli(schedules[1])) <= vliveNotifyBefore {
			kind, notifyAt = "end", schedules[1]
		} else {
			continue
		}
		if s.draw == nil {
			return nil, fmt.Errorf("draw client is nil")
		}
		img, err := s.draw.Render(ctx, "vlive_cards", map[string]any{
			"title":     fmt.Sprintf("虚拟Live%s场提醒（%s）", map[string]string{"start": "开始", "end": "末"}[kind], serverNameCN[serverType]),
			"vlives":    []map[string]any{live},
			"footer":    "KNDBOT · 虚拟Live通知",
			"pjsk_type": serverType,
		})
		if err != nil {
			return nil, err
		}
		items = append(items, subscription.FeedItem{
			ID:      fmt.Sprintf("vlive/%s/%s/%d", server, kind, intField(live, "id")),
			Message: onebot.Message{onebot.ImageBytes(base64.StdEncoding.EncodeToString(img))},
		})
		_ = notifyAt
	}
	return items, nil
}

func serverTypeFromShort(server string) int {
	switch server {
	case "tw":
		return 1
	case "cn":
		return 2
	default:
		return 0
	}
}

func fallbackString(value, fallback string) string {
	if value == "" {
		return fallback
	}
	return value
}

func scheduleTimes(live map[string]any) [2]int64 {
	starts := make([]int64, 0)
	if raw, ok := live["virtualLiveSchedules"].([]any); ok {
		for _, item := range raw {
			if schedule, ok := item.(map[string]any); ok {
				if start := int64(intField(schedule, "startAt")); start > 0 {
					starts = append(starts, start)
				}
			}
		}
	}
	if len(starts) == 0 {
		start := int64(intField(live, "startAt"))
		return [2]int64{start, start}
	}
	min, max := starts[0], starts[0]
	for _, start := range starts[1:] {
		if start < min {
			min = start
		}
		if start > max {
			max = start
		}
	}
	return [2]int64{min, max}
}

var _ subscription.MusicSource = (*MasterdataSubscriptionSource)(nil)
var _ subscription.VLiveSource = (*MasterdataSubscriptionSource)(nil)
