package pjsk

import (
	"context"
	"encoding/base64"
	"encoding/json"

	"github.com/kazuhira/go-pjsk-bot/internal/cards"
	"github.com/kazuhira/go-pjsk-bot/internal/deckopts"
	"github.com/kazuhira/go-pjsk-bot/internal/deckservice"
	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/mysekaidata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/store"
)

// DeckModule 实现挑战组卡（挑战组卡）：取 suite → 构建 options → 调 deck-service
// 组卡 → 走 pjsk-draw 出图。算法在 Rust deck-service，Go 只做参数组装与调用。
//
// 活动/长草/加成组卡、多算法合并作为后续增量。
type DeckModule struct {
	suite       *mysekaidata.Fetcher
	deck        *deckservice.Client
	store       *store.Store
	chara       *cards.CharaAliasResolver
	draw        *draw.Client
	defaultAlgs []string
	timeoutMS   int
	returnNum   int
}

// NewDeckModule 创建组卡模块。
func NewDeckModule(suite *mysekaidata.Fetcher, deck *deckservice.Client, s *store.Store,
	chara *cards.CharaAliasResolver, d *draw.Client, defaultAlgs []string, timeoutSec, returnNum int) *DeckModule {
	return &DeckModule{
		suite: suite, deck: deck, store: s, chara: chara, draw: d,
		defaultAlgs: defaultAlgs, timeoutMS: timeoutSec * 1000, returnNum: returnNum,
	}
}

// Register 注册挑战组卡指令。
func (m *DeckModule) Register(r *router.Router) {
	r.Register("挑战组卡", []string{"挑战配队", "挑战卡组"}, m.handleChallenge)
}

func (m *DeckModule) handleChallenge(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	if m.store == nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	uid, isPrivate, exists, err := m.store.GetUserBind(ctx, req.Event.UserID, server)
	if err != nil || !exists {
		return onebot.ReplyText(req.Event, "你还没有绑定"+req.Server.Name()+"账号哦", true)
	}
	uidStr := itoa64(uid)

	suiteData, suiteMsg := m.suite.GetSuiteData(ctx, uidStr, server)
	if suiteData == nil {
		return onebot.ReplyText(req.Event, "组卡失败："+suiteMsg, true)
	}
	userDataBytes, err := json.Marshal(suiteData)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}

	options := deckopts.BuildChallengeOptions(req.Arg, m.charaResolve, m.timeoutMS, m.returnNum)

	// 算法：options["algorithm"]=="all" 时用 defaultAlgs 分别请求并合并；否则单算法。
	algs := []string{"dfs"}
	if a, ok := options["algorithm"].(string); ok && a != "all" {
		algs = []string{a}
	} else if len(m.defaultAlgs) > 0 {
		algs = m.defaultAlgs
	}

	var allDecks []map[string]any
	var resultAlgs []string
	seen := map[string]bool{}
	for _, alg := range algs {
		decks, err := m.deck.Recommend(ctx, deckservice.RecommendParams{
			Region:      serverCode(server),
			UserDataStr: string(userDataBytes),
			Options:     options,
			Algorithm:   alg,
			TimeoutMS:   m.timeoutMS,
		})
		if err != nil {
			continue
		}
		for _, d := range decks {
			key := deckKey(d)
			if seen[key] {
				continue
			}
			seen[key] = true
			allDecks = append(allDecks, d)
			resultAlgs = append(resultAlgs, alg)
		}
	}
	if len(allDecks) == 0 {
		return onebot.ReplyText(req.Event, "组卡服务未返回可用结果，请稍后再试", true)
	}

	profile := mysekaidata.ProfileFromSuiteData(uidStr, suiteData)
	img, err := m.draw.Render(ctx, "deck", map[string]any{
		"profile_data":   profile,
		"is_private":     isPrivate,
		"result_decks":   allDecks,
		"result_algs":    resultAlgs,
		"cost_times":     map[string]any{},
		"wait_times":     map[string]any{},
		"recommend_type": "challenge",
		"options":        options,
		"additional":     map[string]any{},
		"pjsk_type":      server,
	})
	if err != nil {
		return onebot.ReplyText(req.Event, "组卡图片生成失败", false)
	}
	return onebot.SendMessageAction(req.Event, onebot.Message{onebot.ImageBytes(base64.StdEncoding.EncodeToString(img))})
}

func (m *DeckModule) charaResolve(alias string) int {
	if m.chara == nil {
		return 0
	}
	return m.chara.Resolve(alias)
}

// deckKey 生成卡组去重键，对齐 _add_decks 的 score_power_firstcard。
func deckKey(d map[string]any) string {
	score := intField(d, "score")
	power := intField(d, "total_power")
	firstCard := 0
	if cardsList, ok := d["cards"].([]any); ok && len(cardsList) > 0 {
		if c0, ok := cardsList[0].(map[string]any); ok {
			firstCard = intField(c0, "card_id")
		}
	}
	return itoa64(int64(score)) + "_" + itoa64(int64(power)) + "_" + itoa64(int64(firstCard))
}
