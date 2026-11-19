package pjsk

import (
	"context"
	"encoding/base64"
	"fmt"
	"sort"
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
		liveID := intField(live, "id")
		if liveID <= 0 || strField(live, "virtualLiveType") == "beginner" {
			continue
		}
		start := int64(intField(live, "startAt"))
		end := int64(intField(live, "endAt"))
		if end <= now.UnixMilli() || end-start >= int64(30*24*time.Hour/time.Millisecond) {
			continue
		}
		for _, reminder := range vliveReminders(live, now) {
			if s.draw == nil {
				return nil, fmt.Errorf("draw client is nil")
			}
			label := "开始场"
			if reminder.kind == "end" {
				label = "末场"
			}
			deliveryKind := reminder.kind
			if reminder.kind == "slot" {
				deliveryKind = fmt.Sprintf("slot/%d", reminder.at)
			}
			img, err := s.draw.Render(ctx, "vlive_cards", map[string]any{
				"title":     fmt.Sprintf("虚拟Live%s提醒（%s）", label, serverNameCN[serverType]),
				"vlives":    []map[string]any{live},
				"footer":    "KNDBOT · 虚拟Live通知",
				"pjsk_type": serverType,
			})
			if err != nil {
				return nil, err
			}
			items = append(items, subscription.FeedItem{
				ID:         fmt.Sprintf("vlive/%s/%s/%d", server, deliveryKind, liveID),
				BatchKey:   fmt.Sprintf("vlive/%s/%s/%d", server, reminder.kind, reminder.at),
				BatchOrder: int64(liveID),
				Message:    onebot.Message{onebot.ImageBytes(base64.StdEncoding.EncodeToString(img))},
			})
		}
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

// 固定东八区，不依赖进程TZ，也不把日服当地12点误当作东八区12点。
var vliveReminderLocation = time.FixedZone("UTC+8", 8*60*60)

type vliveReminder struct {
	kind string
	at   int64
}

// vliveReminders 保留首末场，并补充东八区12点、18点的中间场次。
func vliveReminders(live map[string]any, now time.Time) []vliveReminder {
	bounds := scheduleTimes(live)
	if bounds[0] <= 0 {
		return nil
	}
	candidates := []vliveReminder{{kind: "start", at: bounds[0]}}
	if bounds[1] != bounds[0] {
		candidates = append(candidates, vliveReminder{kind: "end", at: bounds[1]})
	}
	// 首末场已有独立提醒和历史投递键；重合场次不能再生成slot通知。
	seen := map[int64]bool{bounds[0]: true, bounds[1]: true}
	if raw, ok := live["virtualLiveSchedules"].([]any); ok {
		for _, item := range raw {
			schedule, ok := item.(map[string]any)
			if !ok {
				continue
			}
			start := int64(intField(schedule, "startAt"))
			if start <= 0 || seen[start] {
				continue
			}
			seen[start] = true
			local := time.UnixMilli(start).In(vliveReminderLocation)
			if (local.Hour() == 12 || local.Hour() == 18) && local.Minute() == 0 && local.Second() == 0 && local.Nanosecond() == 0 {
				candidates = append(candidates, vliveReminder{kind: "slot", at: start})
			}
		}
	}
	reminders := make([]vliveReminder, 0, len(candidates))
	for _, reminder := range candidates {
		start := time.UnixMilli(reminder.at)
		if start.After(now) && start.Sub(now) <= vliveNotifyBefore {
			reminders = append(reminders, reminder)
		}
	}
	sort.SliceStable(reminders, func(i, j int) bool { return reminders[i].at < reminders[j].at })
	return reminders
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
