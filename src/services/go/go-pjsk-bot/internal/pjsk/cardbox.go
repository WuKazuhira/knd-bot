package pjsk

import (
	"context"
	"encoding/base64"
	"strings"

	"github.com/kazuhira/go-pjsk-bot/internal/cards"
	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
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
}

// CardBoxModule 实现卡牌一览（cardbox）：按团体/稀有度/属性/限定筛选卡面并出图，
// 支持 box 持卡模式（仅显示已拥有的卡，需绑定 + suite）。
//
// 年份 / 活动卡（event_only）/ 剧透（show_leak）筛选维度作为增强项暂缓（findcard
// 已实现同类维度，可参考移植）；本模块覆盖团体 + 稀有度 + 属性 + 限定/fes + box。
type CardBoxModule struct {
	md   *masterdata.Loader
	draw *draw.Client
}

// NewCardBoxModule 创建 cardbox 模块。
func NewCardBoxModule(md *masterdata.Loader, d *draw.Client) *CardBoxModule {
	return &CardBoxModule{md: md, draw: d}
}

// Register 注册卡牌一览指令。
func (m *CardBoxModule) Register(r *router.Router) {
	r.Register("卡牌一览", []string{"cardbox", "卡面一览", "卡一览"}, m.handle)
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

	// 确定基础卡池与角色顺序
	var baseCards []map[string]any
	var orderedChars []int
	if f.unit != "" {
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
	var cardIDs []int
	for _, c := range baseCards {
		if f.rarity != "" && strField(c, "cardRarityType") != f.rarity {
			continue
		}
		if f.attr != "" && strField(c, "attr") != f.attr {
			continue
		}
		if f.fes && !cards.IsFes(c, cardSupplies) {
			continue
		}
		if f.hasLimited {
			isLimited := cards.CardType(intField(c, "id"), cardCostume3ds, costume3ds) == 1
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
		cardIDs = append(cardIDs, intField(c, "id"))
	}

	if len(cardIDs) == 0 {
		return onebot.ReplyText(req.Event, "没有找到符合条件的卡面哦", false)
	}

	img, err := m.draw.Render(ctx, "cardbox", map[string]any{
		"card_ids":      cardIDs,
		"ordered_chars": orderedChars,
		"user_cards":    nil,
		"profile":       nil,
		"show_box":      false,
		"pjsk_type":     server,
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64.StdEncoding.EncodeToString(img))
}

func intSet(a []int) map[int]bool {
	s := make(map[int]bool, len(a))
	for _, v := range a {
		s[v] = true
	}
	return s
}
