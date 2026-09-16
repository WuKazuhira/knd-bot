package pjsk

import (
	"context"
	"encoding/base64"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/cards"
	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/mysekaidata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/store"
)

// 筛选映射表，对齐 old-python cardbox。
var (
	rarityMap = map[string]string{
		"一星": "rarity_1", "1星": "rarity_1", "1": "rarity_1",
		"二星": "rarity_2", "2星": "rarity_2", "2": "rarity_2",
		"三星": "rarity_3", "3星": "rarity_3", "3": "rarity_3",
		"四星": "rarity_4", "4星": "rarity_4", "4": "rarity_4",
		"生日": "rarity_birthday", "birthday": "rarity_birthday",
	}
	attrMap = map[string]string{
		"cool": "cool", "cute": "cute", "happy": "happy", "mysterious": "mysterious", "pure": "pure",
		"酷": "cool", "可爱": "cute", "快乐": "happy", "神秘": "mysterious", "纯洁": "pure",
		"蓝": "cool", "蓝星": "cool",
		"橙": "happy", "橙心": "happy", "黄": "happy",
		"紫": "mysterious", "紫月": "mysterious",
		"粉": "cute", "粉花": "cute",
		"绿": "pure", "绿草": "pure",
	}
	limitedKeywords   = map[string]bool{"限定": true, "limited": true}
	permanentKeywords = map[string]bool{"常驻": true, "permanent": true}
	fesKeywords       = map[string]bool{"fes": true, "colorful": true, "cf": true}
)

// cardFilter 是解析后的筛选条件。
type cardFilter struct {
	rarity     string
	attr       string
	unit       string // 团体内部名
	limited    bool
	permanent  bool
	fes        bool
	hasLimited bool // 是否指定了限定/常驻筛选
	year       int  // 发布年份筛选（0=不限），对齐 findcard/Python
	eventOnly  bool // 仅活动卡
	showLeak   bool // 显示未发布（剧透）卡面
	showBox    bool // 仅显示持有卡
}

// CardBoxModule 实现卡牌一览（cardbox）：按团体/稀有度/属性/限定筛选卡面并出图，
// 支持 box 持卡模式（仅显示已拥有的卡，需绑定 + suite）。
//
// 支持年份、活动卡（event/event_only）和剧透（leak/show_leak）筛选；普通模式也会
// 读取玩家 Suite 数据，以便将未持有卡面显示为灰黑色缩略图。
type CardBoxModule struct {
	md    *masterdata.Loader
	draw  *draw.Client
	suite *mysekaidata.Fetcher
	store *store.Store
	chara *cards.CharaAliasResolver
}

// NewCardBoxModule 创建 cardbox 模块。suite/store 用于 box 持卡模式；可为 nil，
// 此时普通筛选仍可工作，但 box 会返回明确错误。
func NewCardBoxModule(md *masterdata.Loader, d *draw.Client, suite *mysekaidata.Fetcher, s *store.Store, resolver *cards.CharaAliasResolver) *CardBoxModule {
	return &CardBoxModule{md: md, draw: d, suite: suite, store: s, chara: resolver}
}

// Register 注册卡牌一览指令。
func (m *CardBoxModule) Register(r *router.Router) {
	r.RegisterNumericSuffix("卡牌一览", []string{"cardbox", "卡面一览", "卡一览"}, m.handle)
}

// parseFilter 解析筛选参数（空格分词，逐词匹配各维度）。
func parseFilter(arg string) cardFilter {
	var f cardFilter
	for _, w := range strings.Fields(arg) {
		lw := strings.ToLower(w)
		if v, ok := rarityMap[lw]; ok {
			f.rarity = v
			continue
		}
		if v, ok := attrMap[lw]; ok {
			f.attr = v
			continue
		}
		if v, ok := cards.UnitKeyToInternal[lw]; ok {
			f.unit = v
			continue
		}
		if limitedKeywords[lw] {
			f.limited = true
			f.hasLimited = true
			continue
		}
		if permanentKeywords[lw] {
			f.permanent = true
			f.hasLimited = true
			continue
		}
		if fesKeywords[lw] {
			f.fes = true
			continue
		}
		if eventKeywords[lw] {
			f.eventOnly = true
			continue
		}
		if lw == "leak" {
			f.showLeak = true
			continue
		}
		if lw == "box" {
			f.showBox = true
			continue
		}
		if len(lw) == 4 && isAllDigits(lw) {
			f.year = atoiDefault(lw, 0)
			continue
		}
	}
	return f
}

func (m *CardBoxModule) handle(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	allcards, err := m.md.Load("cards.json", server)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	f := parseFilter(req.Arg)
	alias := strings.TrimSpace(strings.Join(func() []string {
		var rest []string
		for _, word := range strings.Fields(req.Arg) {
			lw := strings.ToLower(word)
			if rarityMap[lw] == "" && attrMap[lw] == "" && cards.UnitKeyToInternal[lw] == "" &&
				!limitedKeywords[lw] && !permanentKeywords[lw] && !fesKeywords[lw] &&
				!eventKeywords[lw] && lw != "leak" && lw != "box" && !(len(lw) == 4 && isAllDigits(lw)) {
				rest = append(rest, word)
			}
		}
		return rest
	}(), " "))
	charaID := 0
	if alias != "" && m.chara != nil {
		charaID = m.chara.Resolve(alias)
		if charaID == 0 && !f.showBox {
			return onebot.ReplyText(req.Event, "找不到你说的角色或团体哦", false)
		}
	}
	// Python cardbox 在普通模式也会读取绑定账号的 Suite，用于标记未持有卡面；
	// 空持卡列表必须保持为非 nil 的 []，否则绘图层会把 nil 解释为“全部持有”。
	userCards := make([][2]int64, 0)
	var profileData any
	if m.store == nil || m.suite == nil {
		return onebot.ReplyText(req.Event, "卡牌一览暂不可用，请确认账号绑定和 Suite 服务配置", true)
	}
	uid, _, exists, err := m.store.GetUserBind(ctx, req.Event.UserID, server)
	if err != nil || !exists {
		return onebot.ReplyText(req.Event, "你还没有绑定"+req.Server.Name()+"账号哦", true)
	}
	suiteData, msg := m.suite.GetSuiteData(ctx, itoa64(uid), server)
	if suiteData == nil {
		return onebot.ReplyText(req.Event, "获取持卡数据失败："+msg, true)
	}
	userCards = extractUserCardPairs(suiteData)
	if f.showBox && len(userCards) == 0 {
		return onebot.ReplyText(req.Event, "没有获取到你的持卡数据，请确认 Suite 数据已上传或稍后再试", true)
	}
	profileData = mysekaidata.ProfileFromSuiteData(itoa64(uid), suiteData)

	// 确定基础卡池与角色顺序
	var baseCards []map[string]any
	var orderedChars []int
	if charaID != 0 {
		for _, c := range allcards {
			if intField(c, "characterId") == charaID {
				baseCards = append(baseCards, c)
			}
		}
		orderedChars = []int{charaID}
	} else if f.unit != "" {
		gcu, _ := m.md.Load("gameCharacterUnits.json", server)
		mainChars := cards.UnitMainChars[f.unit]
		vsChars := cards.UnitVsChars(f.unit, gcu)
		mainSet := intSet(mainChars)
		vsSet := intSet(vsChars)
		for _, c := range allcards {
			cid := intField(c, "characterId")
			if mainSet[cid] {
				baseCards = append(baseCards, c)
			} else if vsSet[cid] && strField(c, "supportUnit") == f.unit {
				baseCards = append(baseCards, c)
			}
		}
		orderedChars = append(append([]int{}, mainChars...), vsChars...)
	} else {
		baseCards = allcards
		for i := 1; i <= 26; i++ {
			orderedChars = append(orderedChars, i)
		}
	}

	// 精筛
	cardCostume3ds, _ := m.md.Load("cardCostume3ds.json", server)
	costume3ds, _ := m.md.Load("costume3ds.json", server)
	cardSupplies, _ := m.md.Load("cardSupplies.json", server)
	cardIndex := cards.NewCardIndex(cardCostume3ds, costume3ds, cardSupplies)
	nowMS := time.Now().UnixMilli()
	var eventCardIDs map[int]bool
	if f.eventOnly {
		if ec, err := m.md.Load("eventCards.json", server); err == nil {
			eventCardIDs = map[int]bool{}
			for _, e := range ec {
				eventCardIDs[intField(e, "cardId")] = true
			}
		}
	}
	owned := make(map[int]bool, len(userCards))
	for _, pair := range userCards {
		owned[int(pair[0])] = true
	}
	var cardIDs []int
	var limitedCardIDs []int
	var fesCardIDs []int
	for _, c := range baseCards {
		cardID := intField(c, "id")
		if f.showBox && !owned[cardID] {
			continue
		}
		// 未发布（剧透）卡：默认过滤，leak 模式显示。
		if !f.showLeak && int64(intField(c, "releaseAt")) > nowMS {
			continue
		}
		if f.rarity != "" && strField(c, "cardRarityType") != f.rarity {
			continue
		}
		if f.attr != "" && strField(c, "attr") != f.attr {
			continue
		}
		// 生日卡在旧 Python 实现中也归入限定筛选。
		isLimited := cardIndex.IsLimited(cardID) || strField(c, "cardRarityType") == "rarity_birthday"
		isFes := cardIndex.IsFes(c)
		if f.fes && !isFes {
			continue
		}
		if f.hasLimited {
			if f.limited && !isLimited {
				continue
			}
			if f.permanent && isLimited {
				continue
			}
		}
		if f.year != 0 {
			y := time.Unix(int64(intField(c, "releaseAt"))/1000, 0).UTC().Year()
			if y != f.year {
				continue
			}
		}
		if f.eventOnly && eventCardIDs != nil && !eventCardIDs[cardID] {
			continue
		}
		cardIDs = append(cardIDs, cardID)
		if isLimited {
			limitedCardIDs = append(limitedCardIDs, cardID)
			if isFes {
				fesCardIDs = append(fesCardIDs, cardID)
			}
		}
	}

	if len(cardIDs) == 0 {
		return onebot.ReplyText(req.Event, "没有找到符合条件的卡面哦", false)
	}

	img, err := m.draw.Render(ctx, "cardbox", map[string]any{
		"card_ids":         cardIDs,
		"ordered_chars":    orderedChars,
		"user_cards":       userCards,
		"profile":          profileData,
		"show_box":         f.showBox,
		"pjsk_type":        server,
		"limited_card_ids": limitedCardIDs,
		"fes_card_ids":     fesCardIDs,
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64.StdEncoding.EncodeToString(img))
}

// extractUserCardPairs 从 Suite 常见结构提取 [cardId, masterRank]，供 cardbox 绘图。
func extractUserCardPairs(data map[string]any) [][2]int64 {
	var cards []map[string]any
	candidates := []map[string]any{data}
	if nested, ok := data["userGamedata"].(map[string]any); ok {
		candidates = append(candidates, nested)
	}
	for _, candidate := range candidates {
		if found := sliceOfMap(candidate["userCards"]); len(found) > 0 {
			cards = found
			break
		}
	}
	if len(cards) == 0 {
		return nil
	}
	pairs := make([][2]int64, 0, len(cards))
	for _, card := range cards {
		cardID := intField(card, "cardId")
		if cardID == 0 {
			continue
		}
		rank := intField(card, "masterRank")
		if rank == 0 {
			rank = intField(card, "master_rank")
		}
		pairs = append(pairs, [2]int64{int64(cardID), int64(rank)})
	}
	return pairs
}

func intSet(a []int) map[int]bool {
	s := make(map[int]bool, len(a))
	for _, v := range a {
		s[v] = true
	}
	return s
}
