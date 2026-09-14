package pjsk

import (
	"context"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/serverconfig"
	"github.com/kazuhira/go-pjsk-bot/internal/subscription"
)

const (
	newCardAutoLookback = 24 * time.Hour
	newCardAutoAhead    = 7 * 24 * time.Hour
	newCardNodeName     = "KNDBOT 新卡速递"
)

// NewCardSubscriptionSource 从日服主数据生成新卡合并转发。
type NewCardSubscriptionSource struct {
	md          *masterdata.Loader
	draw        *draw.Client
	cardAssets  *CardAssetModule
	eventModule *EventModule
	now         func() time.Time
}

// NewNewCardSubscriptionSource 创建新卡订阅数据源。
func NewNewCardSubscriptionSource(md *masterdata.Loader, d *draw.Client, servers *serverconfig.Config, dataDir string) *NewCardSubscriptionSource {
	return &NewCardSubscriptionSource{
		md:          md,
		draw:        d,
		cardAssets:  NewCardAssetModule(md, servers, dataDir),
		eventModule: NewEventModule(md, d, nil),
		now:         time.Now,
	}
}

type newCardCandidate struct {
	event      map[string]any
	eventID    int
	cardIDs    []int
	maxRelease int64
}

// FetchNewCard 返回自动通知窗口内的所有活动批次。
func (s *NewCardSubscriptionSource) FetchNewCard(ctx context.Context, server string) ([]subscription.FeedItem, error) {
	if server != "jp" {
		return nil, nil
	}
	events, eventCards, cards, err := s.loadData(0)
	if err != nil {
		return nil, err
	}
	candidates := selectNewCardCandidates(events, eventCards, cards, s.currentTime(), false)
	items := make([]subscription.FeedItem, 0, len(candidates))
	for _, candidate := range candidates {
		item, err := s.buildItem(ctx, events, cards, candidate, false)
		if err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	return items, nil
}

// BuildLatest 构造手动入口使用的最新一批；force 为 true 时强刷卡面原图。
func (s *NewCardSubscriptionSource) BuildLatest(ctx context.Context, force bool) (subscription.FeedItem, bool, error) {
	events, eventCards, cards, err := s.loadData(0)
	if err != nil {
		return subscription.FeedItem{}, false, err
	}
	candidates := selectNewCardCandidates(events, eventCards, cards, s.currentTime(), true)
	if len(candidates) == 0 {
		return subscription.FeedItem{}, false, nil
	}
	item, err := s.buildItem(ctx, events, cards, candidates[0], force)
	if err != nil {
		return subscription.FeedItem{}, false, err
	}
	return item, true, nil
}

func (s *NewCardSubscriptionSource) currentTime() time.Time {
	if s != nil && s.now != nil {
		return s.now()
	}
	return time.Now()
}

func (s *NewCardSubscriptionSource) loadData(server int) ([]map[string]any, []map[string]any, []map[string]any, error) {
	if s == nil || s.md == nil {
		return nil, nil, nil, fmt.Errorf("masterdata loader is nil")
	}
	events, err := s.md.Load("events.json", server)
	if err != nil {
		return nil, nil, nil, err
	}
	eventCards, err := s.md.Load("eventCards.json", server)
	if err != nil {
		return nil, nil, nil, err
	}
	cards, err := s.md.Load("cards.json", server)
	if err != nil {
		return nil, nil, nil, err
	}
	return events, eventCards, cards, nil
}

func selectNewCardCandidates(events, eventCards, cards []map[string]any, now time.Time, manual bool) []newCardCandidate {
	cardByID := masterdata.ByID(cards)
	cardsByEvent := make(map[int][]int)
	for _, link := range eventCards {
		eventID := intField(link, "eventId")
		cardID := intField(link, "cardId")
		if eventID <= 0 || cardID <= 0 {
			continue
		}
		if _, ok := cardByID[int64(cardID)]; !ok {
			continue
		}
		cardsByEvent[eventID] = append(cardsByEvent[eventID], cardID)
	}

	nowMS := now.UnixMilli()
	lookbackMS := int64(newCardAutoLookback / time.Millisecond)
	aheadMS := int64(newCardAutoAhead / time.Millisecond)
	out := make([]newCardCandidate, 0)
	for _, event := range events {
		eventID := intField(event, "id")
		linked := dedupInts(cardsByEvent[eventID])
		if eventID <= 0 || len(linked) == 0 {
			continue
		}
		start := int64(intField(event, "startAt"))
		end := eventEndAt(event)
		if end == 0 {
			end = start
		}
		if manual {
			if end > 0 && end <= nowMS {
				continue
			}
		} else {
			if start > nowMS+aheadMS || (end > 0 && end < nowMS-lookbackMS) {
				continue
			}
		}

		maxRelease := int64(0)
		hasCandidate := false
		for _, cardID := range linked {
			release := int64(intField(cardByID[int64(cardID)], "releaseAt"))
			if release <= 0 {
				continue
			}
			if manual {
				if release > nowMS {
					hasCandidate = true
				}
			} else if release >= nowMS-lookbackMS && release <= nowMS+aheadMS {
				hasCandidate = true
			}
			if release > maxRelease {
				maxRelease = release
			}
		}
		if !hasCandidate {
			continue
		}
		sort.Slice(linked, func(i, j int) bool {
			left := int64(intField(cardByID[int64(linked[i])], "releaseAt"))
			right := int64(intField(cardByID[int64(linked[j])], "releaseAt"))
			if left != right {
				return left < right
			}
			return linked[i] < linked[j]
		})
		out = append(out, newCardCandidate{event: event, eventID: eventID, cardIDs: linked, maxRelease: maxRelease})
	}
	sort.Slice(out, func(i, j int) bool {
		leftStart := int64(intField(out[i].event, "startAt"))
		rightStart := int64(intField(out[j].event, "startAt"))
		if leftStart != rightStart {
			return leftStart > rightStart
		}
		if out[i].maxRelease != out[j].maxRelease {
			return out[i].maxRelease > out[j].maxRelease
		}
		return out[i].eventID > out[j].eventID
	})
	return out
}

func (s *NewCardSubscriptionSource) buildItem(ctx context.Context, events, cards []map[string]any, candidate newCardCandidate, force bool) (subscription.FeedItem, error) {
	if s == nil || s.draw == nil {
		return subscription.FeedItem{}, fmt.Errorf("draw client is nil")
	}
	if s.eventModule == nil {
		return subscription.FeedItem{}, fmt.Errorf("event module is nil")
	}
	payload, ok := s.eventModule.buildEventPayload(events, candidate.eventID, 0)
	if !ok {
		return subscription.FeedItem{}, fmt.Errorf("event %d not found", candidate.eventID)
	}
	eventImage, err := s.draw.Render(ctx, "event_info", map[string]any{
		"event":     payload,
		"pjsk_type": 0,
	})
	if err != nil {
		return subscription.FeedItem{}, err
	}

	cardByID := masterdata.ByID(cards)
	nodes := []onebot.ForwardNode{
		onebot.NewForwardNode(newCardNodeName, 0, onebot.Message{onebot.ImageBytes(base64Encode(eventImage))}),
		onebot.NewForwardNode(newCardNodeName, 0, onebot.Message{onebot.Text(fmt.Sprintf("活动 %d：%s", candidate.eventID, strField(candidate.event, "name")))}),
	}
	for _, cardID := range candidate.cardIDs {
		card := cardByID[int64(cardID)]
		if card == nil {
			return subscription.FeedItem{}, fmt.Errorf("card %d not found", cardID)
		}
		if s.cardAssets == nil {
			return subscription.FeedItem{}, fmt.Errorf("card asset module is nil")
		}
		images, err := s.cardAssets.loadCardImages(ctx, cardID, 0, force)
		if err != nil {
			return subscription.FeedItem{}, fmt.Errorf("load card %d assets: %w", cardID, err)
		}
		title := strField(card, "prefix")
		if title == "" {
			title = "卡面"
		}
		nodes = append(nodes, onebot.NewForwardNode(newCardNodeName, 0, onebot.Message{onebot.Text(fmt.Sprintf("【%d】%s", cardID, title))}))
		for _, imageBytes := range images {
			nodes = append(nodes, onebot.NewForwardNode(newCardNodeName, 0, onebot.Message{onebot.ImageBytes(base64Encode(imageBytes))}))
		}
	}
	ids := make([]string, 0, len(candidate.cardIDs))
	for _, id := range candidate.cardIDs {
		ids = append(ids, strconv.Itoa(id))
	}
	return subscription.FeedItem{
		ID:           fmt.Sprintf("event/%d/cards/%s", candidate.eventID, strings.Join(ids, ",")),
		ForwardNodes: nodes,
	}, nil
}

func dedupInts(values []int) []int {
	seen := make(map[int]struct{}, len(values))
	out := make([]int, 0, len(values))
	for _, value := range values {
		if value <= 0 {
			continue
		}
		if _, ok := seen[value]; ok {
			continue
		}
		seen[value] = struct{}{}
		out = append(out, value)
	}
	return out
}

var _ subscription.NewCardSource = (*NewCardSubscriptionSource)(nil)
