package pjsk

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"fmt"
	"math/big"
	"sort"
	"strconv"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/notifysub"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/subscription"
)

const costumeNamespace = "new_costume/jp"
const cardCostumeNamespace = "card_costume/jp"

type costumeOutfit struct {
	Key       string           `json:"key"`
	Name      string           `json:"name"`
	IDs       []int            `json:"ids"`
	Models    []CostumeCapture `json:"models,omitempty"`
	Preferred int              `json:"preferred,omitempty"`
	CardID    int              `json:"cardId,omitempty"`
	CostumeID int              `json:"costumeId,omitempty"`
}
type costumeIndex struct {
	outfits map[string]costumeOutfit
	byID    map[int]map[string]any
	keyByID map[int]string
	genders map[int]string
}

func indexCostumes(snapshot decodedNewCardSnapshot) (costumeIndex, error) {
	idx := costumeIndex{outfits: map[string]costumeOutfit{}, byID: map[int]map[string]any{}, keyByID: map[int]string{}, genders: map[int]string{}}
	if snapshot.costume3ds == nil || snapshot.cardCostume3ds == nil || len(snapshot.character3ds) == 0 || len(snapshot.gameCharacters) == 0 {
		return idx, fmt.Errorf("服装快照缺少 costume3ds/cardCostume3ds/character3ds/gameCharacters 一致发布字段")
	}
	for _, ch := range snapshot.gameCharacters {
		idx.genders[intField(ch, "id")] = strField(ch, "gender")
	}
	groups := map[int][]string{}
	// A shared body asset identity, not groupId/characterId, represents a logical outfit.
	for _, row := range snapshot.costume3ds {
		if strField(row, "partType") != "body" {
			continue
		}
		id := intField(row, "id")
		bundle := strField(row, "assetbundleName")
		if id <= 0 || bundle == "" {
			return idx, fmt.Errorf("invalid body costume %d assetbundleName", id)
		}
		key := "body:" + bundle
		groupID := intField(row, "costume3dGroupId")
		present := false
		for _, previous := range groups[groupID] {
			if previous == key {
				present = true
			}
		}
		if !present {
			groups[groupID] = append(groups[groupID], key)
			sort.Strings(groups[groupID])
		}
		out := idx.outfits[key]
		out.Key = key
		out.Name = strField(row, "name")
		idx.outfits[key] = out
	}
	for _, row := range snapshot.costume3ds {
		id := intField(row, "id")
		if id <= 0 {
			return idx, fmt.Errorf("invalid costume id")
		}
		idx.byID[id] = row
		keys := groups[intField(row, "costume3dGroupId")]
		if strField(row, "partType") == "body" {
			keys = []string{"body:" + strField(row, "assetbundleName")}
		}
		for _, key := range keys {
			out := idx.outfits[key]
			out.IDs = append(out.IDs, id)
			idx.outfits[key] = out
		}
		if len(keys) > 0 {
			idx.keyByID[id] = keys[0]
		}
	}
	cards := map[int]map[string]any{}
	for _, card := range snapshot.cards {
		cards[intField(card, "id")] = card
	}
	links := append([]map[string]any(nil), snapshot.cardCostume3ds...)
	sort.Slice(links, func(i, j int) bool { return intField(links[i], "cardId") < intField(links[j], "cardId") })
	for _, link := range links {
		key := idx.keyByID[intField(link, "costume3dId")]
		if key == "" {
			continue
		}
		card := cards[intField(link, "cardId")]
		if card == nil {
			continue
		}
		out := idx.outfits[key]
		if out.Preferred == 0 {
			out.Preferred = intField(card, "characterId")
			out.CardID = intField(card, "id")
		}
		idx.outfits[key] = out
	}
	for key, out := range idx.outfits {
		sort.Ints(out.IDs)
		idx.outfits[key] = out
	}
	return idx, nil
}
func (idx costumeIndex) firstBodies(out costumeOutfit) []map[string]any {
	rows := []map[string]any{}
	minimum := int(^uint(0) >> 1)
	for _, id := range out.IDs {
		row := idx.byID[id]
		if strField(row, "partType") == "body" && intField(row, "colorId") < minimum {
			minimum = intField(row, "colorId")
		}
	}
	for _, id := range out.IDs {
		row := idx.byID[id]
		if strField(row, "partType") == "body" && intField(row, "colorId") == minimum {
			rows = append(rows, row)
		}
	}
	sort.Slice(rows, func(i, j int) bool { return intField(rows[i], "id") < intField(rows[j], "id") })
	return rows
}
func (idx costumeIndex) parts(body map[string]any) []map[string]any {
	out := []map[string]any{}
	chosen := map[string]bool{}
	ids := []int{}
	for id := range idx.byID {
		ids = append(ids, id)
	}
	sort.Ints(ids)
	for _, id := range ids {
		row := idx.byID[id]
		typ := strField(row, "partType")
		if typ == "body" || chosen[typ] {
			continue
		}
		if intField(row, "costume3dGroupId") == intField(body, "costume3dGroupId") && intField(row, "colorId") == intField(body, "colorId") && intField(row, "characterId") == intField(body, "characterId") {
			chosen[typ] = true
			out = append(out, row)
		}
	}
	return out
}
func (idx costumeIndex) choose(out costumeOutfit, r *CostumeRenderer) ([]CostumeCapture, error) {
	candidates := map[int]CostumeCapture{}
	var last error
	for _, body := range idx.firstBodies(out) {
		character := intField(body, "characterId")
		if _, exists := candidates[character]; exists {
			continue
		}
		if out.Preferred > 0 && character != out.Preferred {
			continue
		}
		selection, err := r.Selection(character, intField(body, "id"), idx.parts(body))
		if err != nil {
			last = err
			continue
		}
		candidates[character] = selection
	}
	if out.Preferred > 0 {
		if c, ok := candidates[out.Preferred]; ok {
			return []CostumeCapture{c}, nil
		}
		return nil, fmt.Errorf("卡角色 %d 服装资源暂缺: %v", out.Preferred, last)
	}
	compatibleCharacters := map[int]bool{}
	for _, body := range idx.firstBodies(out) {
		compatibleCharacters[intField(body, "characterId")] = true
	}
	if len(compatibleCharacters) == 1 {
		for ch := range compatibleCharacters {
			if c, ok := candidates[ch]; ok {
				return []CostumeCapture{c}, nil
			}
		}
		return nil, fmt.Errorf("固定角色服装资源暂缺: %v", last)
	}
	selections := []CostumeCapture{}
	for _, gender := range []string{"male", "female"} {
		ids := []int{}
		expected := false
		for ch := range compatibleCharacters {
			if idx.genders[ch] == gender {
				expected = true
			}
		}
		for ch := range candidates {
			if idx.genders[ch] == gender {
				ids = append(ids, ch)
			}
		}
		sort.Ints(ids)
		if len(ids) == 0 {
			if expected {
				return nil, fmt.Errorf("%s 兼容角色资源暂缺: %v", gender, last)
			}
			continue
		}
		n, err := rand.Int(rand.Reader, big.NewInt(int64(len(ids))))
		if err != nil {
			return nil, err
		}
		selections = append(selections, candidates[ids[n.Int64()]])
	}
	if len(selections) == 0 {
		return nil, fmt.Errorf("兼容角色性别/资源无法确定: %v", last)
	}
	return selections, nil
}

type CostumeSubscriptionSource struct {
	cards    *NewCardSubscriptionSource
	renderer *CostumeRenderer
}

func NewCostumeSubscriptionSource(cards *NewCardSubscriptionSource, renderer *CostumeRenderer) *CostumeSubscriptionSource {
	return &CostumeSubscriptionSource{cards: cards, renderer: renderer}
}
func (s *CostumeSubscriptionSource) enabled() bool {
	return s != nil && s.cards != nil && s.cards.localEnabled() && s.renderer != nil && s.renderer.URL != ""
}
func (s *CostumeSubscriptionSource) FetchCostumes(ctx context.Context) ([]subscription.FeedItem, error) {
	if !s.enabled() {
		return nil, nil
	}
	snapshot, err := s.cards.loadLocalSnapshot()
	if err != nil {
		return nil, err
	}
	idx, err := indexCostumes(snapshot)
	if err != nil {
		return nil, err
	}
	observations := []notifysub.NewsObservation{}
	for id, key := range idx.keyByID {
		encoded, _ := json.Marshal(idx.outfits[key])
		observations = append(observations, notifysub.NewsObservation{ID: id, Key: key, Payload: encoded})
	}
	sort.Slice(observations, func(i, j int) bool { return observations[i].ID < observations[j].ID })
	if err = s.cards.state.ReconcileNews(ctx, costumeNamespace, observations, s.cards.currentTime()); err != nil {
		return nil, err
	}
	return s.pending(ctx, costumeNamespace, idx)
}
func (s *CostumeSubscriptionSource) FetchCardCostumes(ctx context.Context) ([]subscription.FeedItem, error) {
	if !s.enabled() {
		return nil, nil
	}
	snapshot, err := s.cards.loadLocalSnapshot()
	if err != nil {
		return nil, err
	}
	idx, err := indexCostumes(snapshot)
	if err != nil {
		return nil, err
	}
	parseTasks, err := s.cards.state.ListNews(ctx, "card_costume_parse/jp", s.cards.currentTime())
	if err != nil {
		return nil, err
	}
	for _, task := range parseTasks {
		var batch notifysub.NewCardBatch
		if err = json.Unmarshal(task.Payload, &batch); err != nil {
			return nil, err
		}
		if err = s.enqueueCards(ctx, snapshot, batch); err != nil {
			return nil, err
		}
		if err = s.cards.state.AckNews(ctx, "card_costume_parse/jp", task.Key); err != nil {
			return nil, err
		}
	}
	return s.pending(ctx, cardCostumeNamespace, idx)
}
func (s *CostumeSubscriptionSource) pending(ctx context.Context, ns string, idx costumeIndex) ([]subscription.FeedItem, error) {
	batches, err := s.cards.state.ListNews(ctx, ns, s.cards.currentTime())
	if err != nil {
		return nil, err
	}
	items := []subscription.FeedItem{}
	for _, batch := range batches {
		var out costumeOutfit
		err = json.Unmarshal(batch.Payload, &out)
		if err == nil && len(out.Models) == 0 {
			if current, ok := idx.outfits[out.Key]; ok {
				current.Preferred = out.Preferred
				current.CardID = out.CardID
				out = current
			}
			out.Models, err = idx.choose(out, s.renderer)
			if err == nil {
				encoded, _ := json.Marshal(out)
				err = s.cards.state.SaveNewsPayload(ctx, ns, batch.Key, encoded)
			}
		}
		var item subscription.FeedItem
		if err == nil {
			item, err = s.build(ctx, out)
		}
		if err != nil {
			if retryErr := s.cards.state.RetryNews(ctx, ns, batch.Key, batch.Attempts+1, s.cards.currentTime().Add(s.cards.retryDelay(batch.Attempts+1)), err); retryErr != nil {
				return nil, retryErr
			}
			s.cards.logf("[subscription] %s pending %s: %v", ns, batch.Key, err)
			continue
		}
		item.ID = batch.Key
		item.CreatedAt = batch.CreatedAt
		items = append(items, item)
	}
	return items, nil
}
func (s *CostumeSubscriptionSource) build(ctx context.Context, out costumeOutfit) (subscription.FeedItem, error) {
	nodes := []onebot.ForwardNode{onebot.NewForwardNode("KNDBOT 新服装速报", 0, onebot.Message{onebot.Text("PJSK 日服新服装速报\n" + out.Name + "\n" + out.Key)})}
	for _, selection := range out.Models {
		character, _ := strconv.Atoi(stringsBeforeColon(selection.RoleID))
		validated, err := s.renderer.Selection(character, selection.Body, selection.Parts)
		if err != nil {
			return subscription.FeedItem{}, err
		}
		if validated.RoleID != selection.RoleID || validated.Head != selection.Head || validated.Hair != selection.Hair || (validated.Optional == nil) != (selection.Optional == nil) || (validated.Optional != nil && *validated.Optional != *selection.Optional) {
			return subscription.FeedItem{}, fmt.Errorf("persisted role runtime changed")
		}
		image, err := s.renderer.Capture(ctx, selection)
		if err != nil {
			return subscription.FeedItem{}, err
		}
		nodes = append(nodes, onebot.NewForwardNode("KNDBOT 新服装速报", 0, onebot.Message{onebot.Text(fmt.Sprintf("角色 %s · 首配色 · 来源卡 %d", selection.RoleID, out.CardID)), onebot.ImageBytes(base64Encode(image))}))
	}
	return subscription.FeedItem{ForwardNodes: nodes}, nil
}
func stringsBeforeColon(s string) string {
	for i, c := range s {
		if c == ':' {
			return s[:i]
		}
	}
	return s
}
func (s *CostumeSubscriptionSource) AckCostume(ctx context.Context, id string) error {
	return s.cards.state.AckNews(ctx, costumeNamespace, id)
}
func (s *CostumeSubscriptionSource) AckCardCostume(ctx context.Context, id string) error {
	return s.cards.state.AckNews(ctx, cardCostumeNamespace, id)
}
func (s *CostumeSubscriptionSource) BuildLatest(ctx context.Context) (subscription.FeedItem, error) {
	if !s.enabled() {
		return subscription.FeedItem{}, fmt.Errorf("新服装功能未启用或本地快照不可用")
	}
	snapshot, err := s.cards.loadLocalSnapshot()
	if err != nil {
		return subscription.FeedItem{}, err
	}
	idx, err := indexCostumes(snapshot)
	if err != nil {
		return subscription.FeedItem{}, err
	}
	var out costumeOutfit
	maxID := 0
	for id, key := range idx.keyByID {
		if id > maxID {
			maxID = id
			out = idx.outfits[key]
		}
	}
	out.Models, err = idx.choose(out, s.renderer)
	if err != nil {
		return subscription.FeedItem{}, err
	}
	return s.build(ctx, out)
}
func (s *CostumeSubscriptionSource) BuildCardPreviews(ctx context.Context, snapshot decodedNewCardSnapshot, batch notifysub.NewCardBatch) (map[string][]onebot.ForwardNode, error) {
	idx, err := indexCostumes(snapshot)
	if err != nil {
		return nil, err
	}
	wanted := map[int]bool{}
	for _, id := range batch.CardIDs {
		wanted[id] = true
	}
	cards := map[int]map[string]any{}
	for _, card := range snapshot.cards {
		cards[intField(card, "id")] = card
	}
	previews := map[string][]onebot.ForwardNode{}
	for _, link := range snapshot.cardCostume3ds {
		cardID := intField(link, "cardId")
		if !wanted[cardID] {
			continue
		}
		key := idx.keyByID[intField(link, "costume3dId")]
		if key == "" {
			continue
		}
		out := idx.outfits[key]
		out.Preferred = intField(cards[cardID], "characterId")
		out.CardID = cardID
		pendingKey := fmt.Sprintf("%s/card-%d/%s", batch.ID, cardID, key)
		pending, e := s.cards.state.ListNews(ctx, cardCostumeNamespace, s.cards.currentTime().Add(365*24*time.Hour))
		if e != nil {
			return previews, e
		}
		for _, saved := range pending {
			if saved.Key == pendingKey {
				if e = json.Unmarshal(saved.Payload, &out); e != nil {
					return previews, e
				}
			}
		}
		if len(out.Models) == 0 {
			models, e := idx.choose(out, s.renderer)
			if e != nil {
				return previews, e
			}
			out.Models = models
			encoded, _ := json.Marshal(out)
			if e = s.cards.state.SaveNewsPayload(ctx, cardCostumeNamespace, pendingKey, encoded); e != nil {
				return previews, e
			}
		}
		item, e := s.build(ctx, out)
		if e != nil {
			return previews, e
		}
		previews[fmt.Sprintf("%s/card-%d/%s", batch.ID, cardID, key)] = item.ForwardNodes
	}
	return previews, nil
}

func (s *CostumeSubscriptionSource) enqueueCards(ctx context.Context, snapshot decodedNewCardSnapshot, batch notifysub.NewCardBatch) error {
	idx, err := indexCostumes(snapshot)
	if err != nil {
		encoded, _ := json.Marshal(batch)
		return s.cards.state.EnqueueNews(ctx, "card_costume_parse/jp", batch.ID, encoded, batch.DiscoveredAt)
	}
	wanted := map[int]bool{}
	for _, id := range batch.CardIDs {
		wanted[id] = true
	}
	cards := map[int]map[string]any{}
	for _, card := range snapshot.cards {
		cards[intField(card, "id")] = card
	}
	for _, link := range snapshot.cardCostume3ds {
		cardID := intField(link, "cardId")
		if !wanted[cardID] {
			continue
		}
		key := idx.keyByID[intField(link, "costume3dId")]
		if key == "" {
			continue
		}
		out := idx.outfits[key]
		out.Preferred = intField(cards[cardID], "characterId")
		out.CardID = cardID
		out.CostumeID = intField(link, "costume3dId")
		encoded, _ := json.Marshal(out)
		pendingKey := fmt.Sprintf("%s/card-%d/%s", batch.ID, cardID, key)
		if err = s.cards.state.EnqueueNews(ctx, cardCostumeNamespace, pendingKey, encoded, batch.DiscoveredAt); err != nil {
			return err
		}
	}
	return nil
}

var _ = time.Second
