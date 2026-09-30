package pjsk

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	_ "image/jpeg"
	_ "image/png"
	"io"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/notifysub"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/serverconfig"
	"github.com/kazuhira/go-pjsk-bot/internal/subscription"
)

const (
	newCardAutoLookback = 24 * time.Hour
	newCardAutoAhead    = 7 * 24 * time.Hour
	newCardNodeName     = "KNDBOT 新卡速递"
	newCardAssetMaxSize = 64 << 20
)

var ErrLocalNewCardAssetsPending = errors.New("本地新卡资源尚未就绪，请等待 8082 镜像同步")

// NewCardSourceOptions 配置本地快照驱动的自动新卡源。
type NewCardSourceOptions struct {
	SnapshotPath string
	AssetBaseURL string
	State        *notifysub.Store
	HTTPClient   *http.Client
	RetryBase    time.Duration
	RetryMax     time.Duration
	Now          func() time.Time
	Logf         func(string, ...any)
}

// NewCardSubscriptionSource 从日服主数据生成新卡合并转发。
type NewCardSubscriptionSource struct {
	md          *masterdata.Loader
	draw        *draw.Client
	cardAssets  *CardAssetModule
	eventModule *EventModule
	now         func() time.Time

	localSnapshotPath string
	localAssetBaseURL string
	state             *notifysub.Store
	http              *http.Client
	retryBase         time.Duration
	retryMax          time.Duration
	logf              func(string, ...any)
}

// NewNewCardSubscriptionSource 创建保留旧手动行为的数据源。
func NewNewCardSubscriptionSource(md *masterdata.Loader, d *draw.Client, servers *serverconfig.Config, dataDir string) *NewCardSubscriptionSource {
	return newCardSubscriptionSource(md, d, servers, dataDir, NewCardSourceOptions{})
}

// NewNewCardSubscriptionSourceWithOptions 创建本地快照驱动的数据源。配置 SnapshotPath、
// AssetBaseURL 和 State 后，自动通知只会读取该快照并请求该本地资产源。
func NewNewCardSubscriptionSourceWithOptions(md *masterdata.Loader, d *draw.Client, servers *serverconfig.Config, dataDir string, opts NewCardSourceOptions) *NewCardSubscriptionSource {
	return newCardSubscriptionSource(md, d, servers, dataDir, opts)
}

func newCardSubscriptionSource(md *masterdata.Loader, d *draw.Client, servers *serverconfig.Config, dataDir string, opts NewCardSourceOptions) *NewCardSubscriptionSource {
	now := opts.Now
	if now == nil {
		now = time.Now
	}
	httpClient := opts.HTTPClient
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 30 * time.Second}
	}
	// 自动新卡不允许由本地地址重定向到公网资产源。
	clientCopy := *httpClient
	clientCopy.CheckRedirect = func(req *http.Request, via []*http.Request) error { return http.ErrUseLastResponse }
	httpClient = &clientCopy
	retryBase := opts.RetryBase
	if retryBase <= 0 {
		retryBase = 30 * time.Second
	}
	retryMax := opts.RetryMax
	if retryMax < retryBase {
		retryMax = 15 * time.Minute
	}
	logf := opts.Logf
	if logf == nil {
		logf = func(string, ...any) {}
	}
	return &NewCardSubscriptionSource{
		md:                md,
		draw:              d,
		cardAssets:        NewCardAssetModule(md, servers, dataDir),
		eventModule:       NewEventModule(md, d, nil),
		now:               now,
		localSnapshotPath: strings.TrimSpace(opts.SnapshotPath),
		localAssetBaseURL: strings.TrimRight(strings.TrimSpace(opts.AssetBaseURL), "/"),
		state:             opts.State,
		http:              httpClient,
		retryBase:         retryBase,
		retryMax:          retryMax,
		logf:              logf,
	}
}

type localNewCardSnapshot struct {
	Revision   json.RawMessage `json:"revision"`
	Cards      json.RawMessage `json:"cards"`
	Events     json.RawMessage `json:"events"`
	EventCards json.RawMessage `json:"eventCards"`
}

type decodedNewCardSnapshot struct {
	revision   string
	cards      []map[string]any
	events     []map[string]any
	eventCards []map[string]any
}

type newCardCandidate struct {
	event      map[string]any
	eventID    int
	cardIDs    []int
	maxRelease int64
}

func (s *NewCardSubscriptionSource) localEnabled() bool {
	return s != nil && s.localSnapshotPath != "" && s.localAssetBaseURL != "" && s.state != nil
}

// FetchNewCard 返回持久 pending 中资产已就绪的批次。本地模式首次快照只建立全部卡 ID 基线。
func (s *NewCardSubscriptionSource) FetchNewCard(ctx context.Context, server string) ([]subscription.FeedItem, error) {
	if server != "jp" {
		return nil, nil
	}
	if s.localEnabled() {
		return s.fetchLocalNewCards(ctx)
	}
	return s.fetchLegacyNewCards(ctx)
}

func (s *NewCardSubscriptionSource) fetchLocalNewCards(ctx context.Context) ([]subscription.FeedItem, error) {
	snapshot, err := s.loadLocalSnapshot()
	if err != nil {
		return nil, err
	}
	observations := snapshotObservations(snapshot.cards, snapshot.events, snapshot.eventCards)
	baseline, newCount, err := s.state.ReconcileNewCardSnapshot(ctx, "jp", snapshot.revision, observations, s.currentTime())
	if err != nil {
		return nil, err
	}
	if baseline {
		s.logf("[subscription] new_card/jp 本地快照已建立基线 revision=%s cards=%d", snapshot.revision, len(observations))
		return nil, nil
	}
	if newCount > 0 {
		s.logf("[subscription] new_card/jp 检测到新增卡 revision=%s cards=%d", snapshot.revision, newCount)
	}

	now := s.currentTime()
	batches, err := s.state.ListReadyNewCardBatches(ctx, "jp", now)
	if err != nil {
		return nil, err
	}
	items := make([]subscription.FeedItem, 0, len(batches))
	for _, batch := range batches {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		item, err := s.buildLocalBatch(ctx, snapshot, batch)
		if err != nil {
			attempts := batch.Attempts + 1
			nextRetry := now.Add(s.retryDelay(attempts))
			if markErr := s.state.MarkNewCardBatchRetry(ctx, "jp", batch.ID, attempts, nextRetry, err); markErr != nil {
				s.logf("[subscription] new_card/jp 批次重试状态写入失败 batch=%s: %v", batch.ID, markErr)
			}
			s.logf("[subscription] new_card/jp 批次等待本地资源 batch=%s revision=%s retry_at=%s: %v", batch.ID, batch.Revision, nextRetry.Format(time.RFC3339), err)
			continue
		}
		items = append(items, item)
	}
	return items, nil
}

func (s *NewCardSubscriptionSource) fetchLegacyNewCards(ctx context.Context) ([]subscription.FeedItem, error) {
	events, eventCards, cards, err := s.loadData(0)
	if err != nil {
		return nil, err
	}
	candidates := selectNewCardCandidates(events, eventCards, cards, s.currentTime(), false)
	items := make([]subscription.FeedItem, 0, len(candidates))
	for _, candidate := range candidates {
		item, err := s.buildLegacyItem(ctx, events, cards, candidate, false)
		if err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	return items, nil
}

// AcknowledgeNewCard 在所有应接收群均成功记账后移除持久 pending。
func (s *NewCardSubscriptionSource) UsesPersistentNewCardState() bool { return s.localEnabled() }

func (s *NewCardSubscriptionSource) AcknowledgeNewCard(ctx context.Context, server, batchID string) error {
	if !s.localEnabled() || server != "jp" {
		return nil
	}
	return s.state.AcknowledgeNewCardBatch(ctx, server, batchID)
}

// BuildLatest 构造手动入口使用的最新一批；本地模式不回退外部资产源。
func (s *NewCardSubscriptionSource) BuildLatest(ctx context.Context, force bool) (subscription.FeedItem, bool, error) {
	if s.localEnabled() {
		snapshot, err := s.loadLocalSnapshot()
		if err != nil {
			return subscription.FeedItem{}, false, fmt.Errorf("本地新卡快照尚未就绪: %w", err)
		}
		candidate, ok := latestLocalCandidate(snapshot)
		if !ok {
			return subscription.FeedItem{}, false, nil
		}
		batch := notifysub.NewCardBatch{
			ID:           manualCandidateID(candidate),
			Revision:     snapshot.revision,
			EventID:      candidate.eventID,
			CardIDs:      candidate.cardIDs,
			DiscoveredAt: s.currentTime(),
		}
		item, err := s.buildLocalBatch(ctx, snapshot, batch)
		if err != nil {
			return subscription.FeedItem{}, false, fmt.Errorf("%w: %v", ErrLocalNewCardAssetsPending, err)
		}
		return item, true, nil
	}

	events, eventCards, cards, err := s.loadData(0)
	if err != nil {
		return subscription.FeedItem{}, false, err
	}
	candidates := selectNewCardCandidates(events, eventCards, cards, s.currentTime(), true)
	if len(candidates) == 0 {
		return subscription.FeedItem{}, false, nil
	}
	item, err := s.buildLegacyItem(ctx, events, cards, candidates[0], force)
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

func (s *NewCardSubscriptionSource) retryDelay(attempts int) time.Duration {
	delay := s.retryBase
	for i := 1; i < attempts && delay < s.retryMax; i++ {
		if delay > s.retryMax/2 {
			return s.retryMax
		}
		delay *= 2
	}
	if delay > s.retryMax {
		return s.retryMax
	}
	return delay
}

func (s *NewCardSubscriptionSource) loadLocalSnapshot() (decodedNewCardSnapshot, error) {
	data, err := os.ReadFile(s.localSnapshotPath)
	if err != nil {
		return decodedNewCardSnapshot{}, err
	}
	var raw localNewCardSnapshot
	if err := json.Unmarshal(data, &raw); err != nil {
		return decodedNewCardSnapshot{}, fmt.Errorf("decode local new card snapshot: %w", err)
	}
	revision, err := normalizeSnapshotRevision(raw.Revision)
	if err != nil {
		return decodedNewCardSnapshot{}, err
	}
	var snapshot decodedNewCardSnapshot
	snapshot.revision = revision
	for name, target := range map[string]*[]map[string]any{
		"cards": &snapshot.cards, "events": &snapshot.events, "eventCards": &snapshot.eventCards,
	} {
		var source json.RawMessage
		switch name {
		case "cards":
			source = raw.Cards
		case "events":
			source = raw.Events
		default:
			source = raw.EventCards
		}
		if len(source) == 0 || bytes.Equal(bytes.TrimSpace(source), []byte("null")) {
			return decodedNewCardSnapshot{}, fmt.Errorf("local new card snapshot missing %s", name)
		}
		if err := json.Unmarshal(source, target); err != nil {
			return decodedNewCardSnapshot{}, fmt.Errorf("decode local new card snapshot %s: %w", name, err)
		}
	}
	if len(snapshot.cards) == 0 {
		return decodedNewCardSnapshot{}, fmt.Errorf("local new card snapshot contains no cards")
	}
	return snapshot, nil
}

func normalizeSnapshotRevision(raw json.RawMessage) (string, error) {
	raw = bytes.TrimSpace(raw)
	if len(raw) == 0 || bytes.Equal(raw, []byte("null")) {
		return "", fmt.Errorf("local new card snapshot missing revision")
	}
	var text string
	if err := json.Unmarshal(raw, &text); err == nil {
		text = strings.TrimSpace(text)
		if text == "" {
			return "", fmt.Errorf("local new card snapshot has empty revision")
		}
		return text, nil
	}
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		return "", fmt.Errorf("decode local new card snapshot revision: %w", err)
	}
	canonical, err := json.Marshal(value)
	if err != nil {
		return "", err
	}
	return string(canonical), nil
}

func snapshotObservations(cards, events, eventCards []map[string]any) []notifysub.NewCardObservation {
	cardSet := make(map[int]struct{}, len(cards))
	for _, card := range cards {
		if cardID := intField(card, "id"); cardID > 0 {
			cardSet[cardID] = struct{}{}
		}
	}
	eventSet := make(map[int]struct{}, len(events))
	for _, event := range events {
		if eventID := intField(event, "id"); eventID > 0 {
			eventSet[eventID] = struct{}{}
		}
	}
	eventByCard := make(map[int]int)
	for _, link := range eventCards {
		cardID := intField(link, "cardId")
		eventID := intField(link, "eventId")
		if _, ok := cardSet[cardID]; !ok {
			continue
		}
		if _, ok := eventSet[eventID]; !ok {
			continue
		}
		if previous := eventByCard[cardID]; previous == 0 || eventID < previous {
			eventByCard[cardID] = eventID
		}
	}
	out := make([]notifysub.NewCardObservation, 0, len(cardSet))
	for cardID := range cardSet {
		out = append(out, notifysub.NewCardObservation{CardID: cardID, EventID: eventByCard[cardID]})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].CardID < out[j].CardID })
	return out
}

func (s *NewCardSubscriptionSource) buildLocalBatch(ctx context.Context, snapshot decodedNewCardSnapshot, batch notifysub.NewCardBatch) (subscription.FeedItem, error) {
	cardByID := masterdata.ByID(snapshot.cards)
	eventByID := masterdata.ByID(snapshot.events)
	nodes := make([]onebot.ForwardNode, 0, 2+len(batch.CardIDs)*3)
	if batch.EventID > 0 {
		event := eventByID[int64(batch.EventID)]
		name := strField(event, "name")
		if name == "" {
			name = "未命名活动"
		}
		if s.draw != nil && s.eventModule != nil {
			payload, ok := s.eventModule.buildEventPayload(snapshot.events, batch.EventID, 0)
			if !ok {
				return subscription.FeedItem{}, fmt.Errorf("event %d payload unavailable", batch.EventID)
			}
			// 只展示本次新增卡，渲染请求不允许走公网资产回退。
			payload["cards"] = batch.CardIDs
			img, err := s.draw.Render(ctx, "event_info", map[string]any{"event": payload, "pjsk_type": 0, "asset_source_policy": "local_only"})
			if err != nil {
				return subscription.FeedItem{}, fmt.Errorf("%w: event %d: %v", ErrLocalNewCardAssetsPending, batch.EventID, err)
			}
			nodes = append(nodes, onebot.NewForwardNode(newCardNodeName, 0, onebot.Message{onebot.ImageBytes(base64Encode(img))}))
		}
		nodes = append(nodes, onebot.NewForwardNode(newCardNodeName, 0, onebot.Message{onebot.Text(fmt.Sprintf("活动 %d：%s\n本批仅包含本次快照新增卡牌", batch.EventID, name))}))
	} else {
		nodes = append(nodes, onebot.NewForwardNode(newCardNodeName, 0, onebot.Message{onebot.Text("非活动新增卡牌（含生日卡等）")}))
	}
	for _, cardID := range batch.CardIDs {
		card := cardByID[int64(cardID)]
		if card == nil {
			return subscription.FeedItem{}, fmt.Errorf("snapshot card %d not found", cardID)
		}
		bundle := strings.TrimSpace(strField(card, "assetbundleName"))
		if bundle == "" || strings.Contains(bundle, "/") || strings.Contains(bundle, "\\") || strings.Contains(bundle, "..") {
			return subscription.FeedItem{}, fmt.Errorf("card %d has invalid assetbundleName", cardID)
		}
		title := strField(card, "prefix")
		if title == "" {
			title = "卡面"
		}
		nodes = append(nodes, onebot.NewForwardNode(newCardNodeName, 0, onebot.Message{onebot.Text(fmt.Sprintf("【%d】%s", cardID, title))}))
		for _, name := range requiredLocalCardFiles(card) {
			imageBytes, err := s.fetchLocalImage(ctx, bundle, name)
			if err != nil {
				return subscription.FeedItem{}, fmt.Errorf("%w: card=%d path=startapp/character/member/%s/%s: %v", ErrLocalNewCardAssetsPending, cardID, bundle, name, err)
			}
			nodes = append(nodes, onebot.NewForwardNode(newCardNodeName, 0, onebot.Message{onebot.ImageBytes(base64Encode(imageBytes))}))
		}
	}
	return subscription.FeedItem{ID: batch.ID, CreatedAt: batch.DiscoveredAt, ForwardNodes: nodes}, nil
}

func requiredLocalCardFiles(card map[string]any) []string {
	files := []string{"card_normal.png"}
	rarity := strField(card, "cardRarityType")
	if rarity == "rarity_3" || rarity == "rarity_4" {
		files = append(files, "card_after_training.png")
	}
	return files
}

func (s *NewCardSubscriptionSource) fetchLocalImage(ctx context.Context, bundle, name string) ([]byte, error) {
	assetURL := s.localAssetBaseURL + "/startapp/character/member/" + url.PathEscape(bundle) + "/" + name
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, assetURL, nil)
	if err != nil {
		return nil, err
	}
	resp, err := s.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 1024))
		return nil, fmt.Errorf("local asset returned HTTP %d", resp.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, newCardAssetMaxSize+1))
	if err != nil {
		return nil, err
	}
	if len(data) == 0 || len(data) > newCardAssetMaxSize {
		return nil, fmt.Errorf("invalid local asset size %d", len(data))
	}
	if _, _, err := image.Decode(bytes.NewReader(data)); err != nil {
		return nil, fmt.Errorf("decode local asset: %w", err)
	}
	return data, nil
}

func latestLocalCandidate(snapshot decodedNewCardSnapshot) (newCardCandidate, bool) {
	cardByID := masterdata.ByID(snapshot.cards)
	cardsByEvent := make(map[int][]int)
	linked := make(map[int]struct{})
	for _, link := range snapshot.eventCards {
		eventID := intField(link, "eventId")
		cardID := intField(link, "cardId")
		if eventID <= 0 || cardByID[int64(cardID)] == nil {
			continue
		}
		cardsByEvent[eventID] = append(cardsByEvent[eventID], cardID)
		linked[cardID] = struct{}{}
	}
	eventByID := masterdata.ByID(snapshot.events)
	var candidates []newCardCandidate
	for eventID, cardIDs := range cardsByEvent {
		cardIDs = dedupInts(cardIDs)
		candidate := newCardCandidate{eventID: eventID, event: eventByID[int64(eventID)], cardIDs: cardIDs}
		for _, cardID := range cardIDs {
			if release := int64(intField(cardByID[int64(cardID)], "releaseAt")); release > candidate.maxRelease {
				candidate.maxRelease = release
			}
		}
		candidates = append(candidates, candidate)
	}
	var standalone []int
	var standaloneRelease int64
	for _, card := range snapshot.cards {
		cardID := intField(card, "id")
		if _, ok := linked[cardID]; ok || cardID <= 0 {
			continue
		}
		release := int64(intField(card, "releaseAt"))
		if release > standaloneRelease {
			standaloneRelease = release
			standalone = []int{cardID}
		} else if release == standaloneRelease {
			standalone = append(standalone, cardID)
		}
	}
	if len(standalone) > 0 {
		candidates = append(candidates, newCardCandidate{cardIDs: standalone, maxRelease: standaloneRelease})
	}
	if len(candidates) == 0 {
		return newCardCandidate{}, false
	}
	for i := range candidates {
		sort.Ints(candidates[i].cardIDs)
	}
	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].maxRelease != candidates[j].maxRelease {
			return candidates[i].maxRelease > candidates[j].maxRelease
		}
		return candidates[i].eventID > candidates[j].eventID
	})
	return candidates[0], true
}

func manualCandidateID(candidate newCardCandidate) string {
	ids := make([]string, 0, len(candidate.cardIDs))
	for _, cardID := range candidate.cardIDs {
		ids = append(ids, strconv.Itoa(cardID))
	}
	return fmt.Sprintf("manual/%d/%s", candidate.eventID, strings.Join(ids, ","))
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
		} else if start > nowMS+aheadMS || (end > 0 && end < nowMS-lookbackMS) {
			continue
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

func (s *NewCardSubscriptionSource) buildLegacyItem(ctx context.Context, events, cards []map[string]any, candidate newCardCandidate, force bool) (subscription.FeedItem, error) {
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
		"event": payload, "pjsk_type": 0,
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
	return subscription.FeedItem{ID: fmt.Sprintf("event/%d/cards/%s", candidate.eventID, strings.Join(ids, ",")), ForwardNodes: nodes}, nil
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
var _ subscription.PersistentNewCardSource = (*NewCardSubscriptionSource)(nil)
