package pjsk

import (
	"context"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/cards"
	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

// findcard 技能类型映射，对齐 SKILL_MAP。
var skillMap = map[string]string{
	"判分": "score_up", "加分": "score_up",
	"回血": "life_recovery", "回复": "life_recovery",
	"复合": "score_up_condition_life_recovery",
	"完美": "score_up_keep",
}

// eventKeywords 活动卡关键词。
var eventKeywords = map[string]bool{"活动": true, "event": true, "活动卡": true}

// findCardFilter 是 findcard 的筛选条件（比 cardbox 更全）。
type findCardFilter struct {
	rarity    string
	attr      string
	skill     string
	unit      string // 团体内部名
	limited   int    // 0=不限 1=限定 2=常驻
	fes       int    // 0=不限 1=仅fes 2=排除fes
	year      int
	eventOnly bool
	showLeak  bool
}

// FindCardModule 实现卡面查询概览（findcard）：按角色/团体+多维筛选出图。
//
// 群自定义角色昵称 DB、指定活动短写(ena7) 作为增强暂缓；覆盖内置角色缩写/
// 昵称yaml + 团体 + 稀有度/属性/技能/限定/fes/年份/活动卡/leak。
type FindCardModule struct {
	md    *masterdata.Loader
	draw  *draw.Client
	chara *cards.CharaAliasResolver
}

// NewFindCardModule 创建 findcard 模块。
func NewFindCardModule(md *masterdata.Loader, d *draw.Client, staticDir string) *FindCardModule {
	return &FindCardModule{md: md, draw: d, chara: cards.NewCharaAliasResolver(staticDir)}
}

// Register 注册卡面查询指令。
func (m *FindCardModule) Register(r *router.Router) {
	r.Register("findcard", []string{"查卡", "查询卡面"}, m.handle)
}

// parseFindArgs 解析筛选词，剩余部分作为角色别名。
func parseFindArgs(arg string) (alias string, f findCardFilter) {
	var remaining []string
	for _, w := range strings.Fields(arg) {
		lw := strings.ToLower(w)
		switch {
		case rarityMap[lw] != "":
			f.rarity = rarityMap[lw]
		case attrMap[lw] != "":
			f.attr = attrMap[lw]
		case skillMap[lw] != "":
			f.skill = skillMap[lw]
		case fesKeywords[lw]:
			f.fes = 1
			f.limited = 1
		case limitedKeywords[lw]:
			if f.fes == 0 {
				f.limited = 1
			}
		case permanentKeywords[lw]:
			f.limited = 2
		case eventKeywords[lw]:
			f.eventOnly = true
		case cards.UnitKeyToInternal[lw] != "":
			f.unit = cards.UnitKeyToInternal[lw]
		case lw == "leak":
			f.showLeak = true
		case len(lw) == 4 && isAllDigits(lw):
			f.year = atoiDefault(lw, 0)
		default:
			remaining = append(remaining, w)
		}
	}
	return strings.TrimSpace(strings.Join(remaining, " ")), f
}

func isAllDigits(s string) bool {
	for _, c := range s {
		if c < '0' || c > '9' {
			return false
		}
	}
	return s != ""
}

func (m *FindCardModule) handle(ctx context.Context, req router.Request) *onebot.ActionRequest {
	arg := strings.TrimSpace(req.Arg)
	// 纯数字 -> 转卡面详情（交给 cardinfo，findcard 这里直接提示）
	if arg != "" && isAllDigits(arg) {
		return nil // 让 cardinfo 指令处理；findcard 不重复响应数字
	}
	server := int(req.Server)
	// ena7 箱活短写：命中则限定为该活动的卡（对齐 Python findcard 的 event_id 维度）。
	var banEventOnly map[int]bool
	if ev, rest, banErr := extractBanEventArg(m.md, server, arg, m.chara.Resolve); banErr != "" {
		return onebot.ReplyText(req.Event, banErr, true)
	} else if ev != nil {
		banEventOnly = eventCardIDSet(m.md, server, ev.ID)
		arg = rest
	}
	alias, f := parseFindArgs(arg)

	// 确定角色
	charaID := 0
	if alias != "" {
		charaID = m.chara.Resolve(alias)
		if charaID == 0 {
			return onebot.ReplyText(req.Event, "找不到你说的角色哦", false)
		}
	} else if f.unit == "" {
		// 无角色无团体：必须有其它筛选条件（ena7 箱活也算一种条件）。
		if !hasAnyFilter(f) && banEventOnly == nil {
			return onebot.ReplyText(req.Event, "请输入角色名/团队名或筛选条件（如：fes、限定、四星等）", false)
		}
	}

	allcards, err := m.md.Load("cards.json", server)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	skills, _ := m.md.Load("skills.json", server)
	cardCostume3ds, _ := m.md.Load("cardCostume3ds.json", server)
	costume3ds, _ := m.md.Load("costume3ds.json", server)
	cardSupplies, _ := m.md.Load("cardSupplies.json", server)
	cardIndex := cards.NewCardIndex(cardCostume3ds, costume3ds, cardSupplies)

	skillSprite := map[int]string{}
	for _, s := range skills {
		skillSprite[intField(s, "id")] = strField(s, "descriptionSpriteName")
	}

	// 活动卡集合
	var eventCardIDs map[int]bool
	if banEventOnly != nil {
		// ena7：直接限定为该箱活的卡集合，复用 eventOnly 的筛选路径。
		eventCardIDs = banEventOnly
		f.eventOnly = true
	} else if f.eventOnly {
		if ec, err := m.md.Load("eventCards.json", server); err == nil {
			eventCardIDs = map[int]bool{}
			for _, e := range ec {
				eventCardIDs[intField(e, "cardId")] = true
			}
		}
	}

	// 团体角色集合
	var unitCharSet map[int]bool
	var orderedChars []int
	unitInternal := "all"
	if f.unit != "" {
		unitInternal = f.unit
		gcu, _ := m.md.Load("gameCharacterUnits.json", server)
		mainChars := cards.UnitMainChars[f.unit]
		vsChars := cards.UnitVsChars(f.unit, gcu)
		orderedChars = append(append([]int{}, mainChars...), vsChars...)
		unitCharSet = intSet(append(append([]int{}, mainChars...), vsChars...))
	} else if charaID != 0 {
		orderedChars = []int{charaID}
	} else {
		for i := 1; i <= 26; i++ {
			orderedChars = append(orderedChars, i)
		}
	}

	nowMS := time.Now().UnixMilli()
	var cardIDs []int
	var limitedCardIDs []int
	var fesCardIDs []int
	for _, c := range allcards {
		// 角色 / 团体范围
		cid := intField(c, "characterId")
		if charaID != 0 && cid != charaID {
			continue
		}
		if unitCharSet != nil && !unitCharSet[cid] {
			continue
		}
		if !applyCardFilterWithIndex(c, f, skillSprite, cardIndex, eventCardIDs, nowMS) {
			continue
		}
		cardID := intField(c, "id")
		cardIDs = append(cardIDs, cardID)
		isLimited := cardIndex.IsLimited(cardID) || strField(c, "cardRarityType") == "rarity_birthday"
		if isLimited {
			limitedCardIDs = append(limitedCardIDs, cardID)
			if cardIndex.IsFes(c) {
				fesCardIDs = append(fesCardIDs, cardID)
			}
		}
	}

	if len(cardIDs) == 0 {
		return onebot.ReplyText(req.Event, "没有找到符合条件的卡面哦", false)
	}
	if len(cardIDs) > 300 {
		return onebot.ReplyText(req.Event, "查询结果过多，请添加更多筛选条件缩小范围", false)
	}

	img, err := m.draw.Render(ctx, "findcard", map[string]any{
		"card_ids":         cardIDs,
		"ordered_chars":    orderedChars,
		"unit_internal":    unitInternal,
		"pjsk_type":        server,
		"limited_card_ids": limitedCardIDs,
		"fes_card_ids":     fesCardIDs,
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}

// applyCardFilter 对单张卡应用筛选，对齐 _apply_filter。
// 保留旧签名供现有测试和包内调用使用；正式 handler 使用已构建的 CardIndex。
func applyCardFilter(card map[string]any, f findCardFilter, skillSprite map[int]string,
	cardCostume3ds, costume3ds, cardSupplies []map[string]any, eventCardIDs map[int]bool, nowMS int64) bool {
	return applyCardFilterWithIndex(card, f, skillSprite,
		cards.NewCardIndex(cardCostume3ds, costume3ds, cardSupplies), eventCardIDs, nowMS)
}

func applyCardFilterWithIndex(card map[string]any, f findCardFilter, skillSprite map[int]string,
	cardIndex *cards.CardIndex, eventCardIDs map[int]bool, nowMS int64) bool {
	// 时间过滤（未发布）
	if !f.showLeak && int64(intField(card, "releaseAt")) > nowMS {
		return false
	}
	if f.rarity != "" && strField(card, "cardRarityType") != f.rarity {
		return false
	}
	if f.attr != "" && strField(card, "attr") != f.attr {
		return false
	}
	if f.skill != "" && skillSprite[intField(card, "skillId")] != f.skill {
		return false
	}
	if f.limited != 0 || f.fes != 0 {
		isLim := cardIndex.IsLimited(intField(card, "id")) ||
			strField(card, "cardRarityType") == "rarity_birthday"
		if f.fes != 0 {
			isFes := cardIndex.IsFes(card)
			if f.fes == 1 && !isFes {
				return false
			}
			if f.fes == 2 && isFes {
				return false
			}
		} else if f.limited == 1 && !isLim {
			return false
		} else if f.limited == 2 && isLim {
			return false
		}
	}
	if f.year != 0 {
		y := time.Unix(int64(intField(card, "releaseAt"))/1000, 0).UTC().Year()
		if y != f.year {
			return false
		}
	}
	if f.eventOnly && eventCardIDs != nil && !eventCardIDs[intField(card, "id")] {
		return false
	}
	return true
}

func hasAnyFilter(f findCardFilter) bool {
	return f.rarity != "" || f.attr != "" || f.skill != "" ||
		f.limited != 0 || f.fes != 0 || f.year != 0 || f.eventOnly || f.showLeak
}
