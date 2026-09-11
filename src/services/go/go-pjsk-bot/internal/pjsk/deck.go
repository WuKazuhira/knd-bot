package pjsk

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/cards"
	"github.com/kazuhira/go-pjsk-bot/internal/deckopts"
	"github.com/kazuhira/go-pjsk-bot/internal/deckservice"
	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/mysekaidata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/settings"
	"github.com/kazuhira/go-pjsk-bot/internal/store"
)

// DeckModule 负责活动、挑战、长草、加成四类组卡入口。计算仍由 deck-service 执行，
// Go 只负责绑定/Suite 数据、参数契约、算法合并和 pjsk-draw 出图。
type DeckModule struct {
	suite    *mysekaidata.Fetcher
	deck     *deckservice.Client
	store    *store.Store
	chara    *cards.CharaAliasResolver
	draw     *draw.Client
	md       *masterdata.Loader
	settings *settings.Settings
}

func NewDeckModule(suite *mysekaidata.Fetcher, deck *deckservice.Client, s *store.Store, chara *cards.CharaAliasResolver, d *draw.Client, md *masterdata.Loader, set *settings.Settings) *DeckModule {
	return &DeckModule{suite: suite, deck: deck, store: s, chara: chara, draw: d, md: md, settings: set}
}

func (m *DeckModule) Register(r *router.Router) {
	r.Register("活动组卡", []string{"组卡", "活动卡组", "活动组队", "活动配队", "配队", "组队", "模拟组卡", "pjsk deck", "pjsk event deck"}, m.handle)
	r.Register("挑战组卡", []string{"挑战卡组", "挑战组队", "挑战配队", "pjsk challenge deck"}, m.handle)
	r.Register("长草组卡", []string{"最强卡组", "最强组卡", "长草卡组", "长草组队", "pjsk best deck", "pjsk no event deck"}, m.handle)
	r.Register("加成组卡", []string{"控分组卡", "加成卡组", "控分卡组", "pjsk bonus deck"}, m.handle)
}

func (m *DeckModule) resolve(alias string) int {
	if m.chara == nil {
		return 0
	}
	return m.chara.Resolve(alias)
}

func (m *DeckModule) handle(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if m.store == nil || m.suite == nil || m.deck == nil || m.draw == nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	uid, isPrivate, exists, err := m.store.GetUserBind(ctx, req.Event.UserID, int(req.Server))
	if err != nil {
		return onebot.ReplyText(req.Event, "组卡失败："+err.Error(), true)
	}
	if !exists {
		return onebot.ReplyText(req.Event, "你还没有绑定"+req.Server.Name()+"账号哦", true)
	}
	uidStr := itoa64(uid)
	suiteData, msg := m.suite.GetSuiteData(ctx, uidStr, int(req.Server))
	if suiteData == nil {
		return onebot.ReplyText(req.Event, "组卡失败："+msg, true)
	}
	kind := deckKind(req.Command)
	currentEvent := m.currentEvent(ctx, int(req.Server))
	resolve := func(s string) int { return m.resolve(strings.ToLower(s)) }
	set := m.settings
	var options map[string]any
	switch kind {
	case "event":
		options = deckopts.BuildEventOptions(req.Arg, currentEvent, resolve, deckTimeout(set, false, false), deckReturn(set, "event"))
	case "challenge":
		options = deckopts.BuildChallengeOptions(req.Arg, resolve, deckTimeout(set, false, false), deckReturn(set, "challenge"))
	case "no_event":
		options = deckopts.BuildNoEventOptions(req.Arg, resolve, deckTimeout(set, true, false), deckReturn(set, "event"))
	case "bonus":
		options = deckopts.BuildBonusOptions(req.Arg, currentEvent, deckTimeout(set, false, true), deckReturn(set, "bonus"))
	default:
		return onebot.ReplyText(req.Event, "未知组卡类型", true)
	}
	if useCurrent, _ := options["use_current_deck"].(bool); useCurrent {
		if kind == "challenge" && options["challenge_live_character_id"] == nil {
			return onebot.ReplyText(req.Event, "需要指定挑战角色才能使用\"当前\"参数", true)
		}
		current := currentDeckCards(suiteData)
		if len(current) < 5 {
			return onebot.ReplyText(req.Event, "无法获取当前卡组，请先更新 Suite 抓包数据", true)
		}
		options["fixed_cards"] = current
		delete(options, "fixed_characters")
		delete(options, "forced_leader_character_id")
		options["algorithm"] = "dfs"
	}
	delete(options, "use_current_deck")
	userData, err := json.Marshal(suiteData)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	decks, algs, costs, waits := m.recommend(ctx, req.Server, kind, options, string(userData), set)
	if len(decks) == 0 {
		return onebot.ReplyText(req.Event, "组卡服务未返回可用结果，请稍后再试", true)
	}
	profile := mysekaidata.ProfileFromSuiteData(uidStr, suiteData)
	img, err := m.draw.Render(ctx, "deck", map[string]any{"profile_data": profile, "is_private": isPrivate, "result_decks": decks, "result_algs": algs, "cost_times": costs, "wait_times": waits, "recommend_type": kind, "options": options, "additional": map[string]any{}, "pjsk_type": int(req.Server)})
	if err != nil {
		return onebot.ReplyText(req.Event, "组卡图片生成失败："+err.Error(), false)
	}
	return onebot.SendMessageAction(req.Event, onebot.Message{onebot.ImageBytes(base64.StdEncoding.EncodeToString(img))})
}

func deckKind(command string) string {
	switch command {
	case "活动组卡":
		return "event"
	case "挑战组卡":
		return "challenge"
	case "长草组卡":
		return "no_event"
	case "加成组卡":
		return "bonus"
	default:
		return ""
	}
}
func deckTimeout(s *settings.Settings, noEvent, bonus bool) int {
	if bonus {
		return s.DeckTimeoutBonus() * 1000
	}
	if noEvent {
		return s.DeckTimeoutNoEvent() * 1000
	}
	return s.DeckTimeout() * 1000
}
func deckReturn(s *settings.Settings, kind string) int {
	if kind == "challenge" {
		return s.DeckReturnNumChallenge()
	}
	if kind == "bonus" {
		return s.DeckReturnNumBonus()
	}
	return s.DeckReturnNumMulti()
}

func (m *DeckModule) recommend(ctx context.Context, server router.ServerType, kind string, base map[string]any, userData string, set *settings.Settings) ([]map[string]any, []string, map[string]any, map[string]any) {
	requests := []map[string]any{base}
	if kind == "challenge" && base["challenge_live_character_id"] == nil {
		requests = nil
		for cid := 1; cid <= 26; cid++ {
			o := cloneOptions(base)
			o["challenge_live_character_id"] = cid
			o["limit"] = 1
			requests = append(requests, o)
		}
	}
	defaultAlgs := set.DeckDefaultAlgorithms()
	decks := make([]map[string]any, 0)
	algs := make([]string, 0)
	seen := map[string]bool{}
	costs := map[string]any{}
	waits := map[string]any{}
	for _, opt := range requests {
		algList := []string{"dfs"}
		if a, ok := opt["algorithm"].(string); ok && a != "all" {
			algList = []string{a}
		} else if len(defaultAlgs) > 0 {
			algList = defaultAlgs
		}
		for _, alg := range algList {
			start := time.Now()
			timeoutMS := intField(opt, "timeout_ms")
			if opt["algorithm"] != "all" && set != nil {
				timeoutMS = set.DeckTimeoutSingleAlgorithm() * 1000
			}
			got, err := m.deck.Recommend(ctx, deckservice.RecommendParams{Region: serverRegion(server), UserDataStr: userData, Options: opt, Algorithm: alg, TimeoutMS: timeoutMS})
			if err != nil {
				continue
			}
			costs[alg] = time.Since(start).Seconds()
			waits[alg] = 0.0
			for _, d := range got {
				key := deckKey(d)
				if seen[key] {
					continue
				}
				seen[key] = true
				decks = append(decks, d)
				algs = append(algs, alg)
			}
		}
		if kind != "challenge" && len(decks) >= intField(opt, "limit") {
			break
		}
	}
	return decks, algs, costs, waits
}

func cloneOptions(in map[string]any) map[string]any {
	out := make(map[string]any, len(in))
	for k, v := range in {
		out[k] = v
	}
	return out
}
func serverRegion(s router.ServerType) string {
	switch s {
	case router.ServerCN:
		return "cn"
	case router.ServerTW:
		return "tw"
	default:
		return "jp"
	}
}
func deckKey(d map[string]any) string {
	score := intField(d, "score")
	power := intField(d, "total_power")
	first := 0
	if cards, ok := d["cards"].([]any); ok && len(cards) > 0 {
		if c, ok := cards[0].(map[string]any); ok {
			first = intField(c, "card_id")
		}
	}
	return fmt.Sprintf("%d_%d_%d", score, power, first)
}

func currentDeckCards(data map[string]any) []int {
	decks, _ := data["userDecks"].([]any)
	if len(decks) == 0 {
		return nil
	}
	deck, _ := decks[0].(map[string]any)
	if deck == nil {
		return nil
	}
	cards := make([]int, 0, 5)
	for i := 1; i <= 5; i++ {
		if id, ok := masterdata.IntField(deck, fmt.Sprintf("member%d", i)); ok && id > 0 {
			cards = append(cards, int(id))
		}
	}
	return cards
}

func (m *DeckModule) currentEvent(_ context.Context, server int) int64 {
	if m.md == nil {
		return 0
	}
	events, err := m.md.Load("events.json", server)
	if err != nil {
		return 0
	}
	now := time.Now().UnixMilli()
	var best int64
	var bestStart int64
	for _, e := range events {
		id, ok := masterdata.IntField(e, "id")
		if !ok {
			continue
		}
		start, _ := masterdata.IntField(e, "startAt")
		end, _ := masterdata.IntField(e, "aggregateAt")
		if end == 0 {
			end, _ = masterdata.IntField(e, "closedAt")
		}
		if start <= now && (end == 0 || now <= end) && start >= bestStart {
			best, bestStart = id, start
		}
	}
	return best
}
