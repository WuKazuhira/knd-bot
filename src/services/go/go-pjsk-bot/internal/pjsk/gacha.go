package pjsk

import (
	"context"
	"encoding/base64"
	"fmt"
	"math/rand"
	"strings"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

// gachaRegex 对齐 old-python on_regex 的抽卡触发正则。
const gachaRegex = `^(cn|tw|jp)? *(?:pjsk|sekai) *(反向?)? *(抽卡|十连抽?|[0-9]+连抽?) *([0-9]+)?$`

// GachaModule 实现假抽卡模拟：抽卡算法 + 十连出图（走 pjsk-draw "gacha"）。
type GachaModule struct {
	md   *masterdata.Loader
	draw *draw.Client
}

// NewGachaModule 创建 gacha 模块。
func NewGachaModule(md *masterdata.Loader, d *draw.Client) *GachaModule {
	return &GachaModule{md: md, draw: d}
}

// Register 注册抽卡指令（正则触发）。
func (m *GachaModule) Register(r *router.Router) {
	r.RegisterRegex("pjsk抽卡", gachaRegex, m.handle)
}

type gachaCard struct {
	id      int
	prefix  string
	charaID int
	weight  int
}

func (m *GachaModule) handle(ctx context.Context, req router.Request) *onebot.ActionRequest {
	g := req.RegexGroups
	// g[1]=prefix g[2]=reverse g[3]=次数词 g[4]=卡池id
	serverType := 0
	switch g[1] {
	case "cn":
		serverType = 2
	case "tw":
		serverType = 1
	}
	isReverse := g[2] != ""
	cardNum := 10
	if n := digitsOnly(g[3]); n != "" {
		cardNum = atoiDefault(n, 10)
	}
	if cardNum > 300 {
		return onebot.ReplyText(req.Event, "一次至多指定一井300抽哦", true)
	}

	gachas, err := m.md.Load("gachas.json", serverType)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}

	var gachaID int
	if idStr := digitsOnly(g[4]); idStr != "" {
		gachaID = atoiDefault(idStr, 0)
	} else {
		cur := currentGacha(gachas, nowMSDefault())
		if cur == nil {
			return onebot.ReplyText(req.Event, "当前没有进行中的卡池", true)
		}
		gachaID = intField(cur, "id")
	}

	text, cardIDs, gachaName, ok := m.simulate(gachas, gachaID, cardNum, isReverse, serverType)
	if !ok {
		return onebot.ReplyText(req.Event, fmt.Sprintf("找不到编号为%d的卡池，命令可加卡池id", gachaID), true)
	}

	if cardNum == 10 {
		img, err := m.draw.Render(ctx, "gacha", map[string]any{"card_ids": cardIDs, "pjsk_type": serverType})
		if err == nil {
			head := fmt.Sprintf("id:%d [%s]\n", gachaID, gachaName)
			return onebot.SendMessageAction(req.Event, onebot.Message{
				onebot.Text(head),
				onebot.ImageBytes(base64.StdEncoding.EncodeToString(img)),
			})
		}
		// 出图失败退化为文本
	}
	return onebot.ReplyText(req.Event, text, false)
}

// currentGacha 返回最新进行中的卡池（从后往前找），对齐 getcurrentgacha。
func currentGacha(gachas []map[string]any, nowMS int64) map[string]any {
	for i := len(gachas) - 1; i >= 0; i-- {
		start := int64(intField(gachas[i], "startAt"))
		end := int64(intField(gachas[i], "endAt"))
		if start < nowMS && nowMS < end {
			return gachas[i]
		}
	}
	return nil
}

// simulate 执行抽卡模拟，返回 (文本, 十连卡片id, 卡池名, 是否找到卡池)。
func (m *GachaModule) simulate(gachas []map[string]any, gachaID, num int, isReverse bool, serverType int) (string, []int, string, bool) {
	var gacha map[string]any
	for _, g := range gachas {
		if intField(g, "id") == gachaID {
			gacha = g
			break
		}
	}
	if gacha == nil {
		return "", nil, "", false
	}
	gachaName := strField(gacha, "name")

	// 稀有度概率
	var rate4, rate3 float64
	birthday := false
	for _, ri := range sliceOfMap(gacha["gachaCardRarityRates"]) {
		switch strField(ri, "cardRarityType") {
		case "rarity_4":
			rate4 = floatField(ri, "rate")
		case "rarity_birthday":
			rate4 = floatField(ri, "rate")
			birthday = true
		}
		if rate4 != 0 {
			break
		}
	}
	for _, ri := range sliceOfMap(gacha["gachaCardRarityRates"]) {
		if strField(ri, "cardRarityType") == "rarity_3" {
			rate3 = floatField(ri, "rate")
		}
	}
	if isReverse {
		rate4 = 100 - rate4 - rate3
	}

	cards, _ := m.md.Load("cards.json", serverType)
	cardByID := masterdata.ByID(cards)
	chars, _ := m.md.Load("gameCharacters.json", serverType)
	charName := makeCharNamer(chars)

	var r2, r3, r4 []gachaCard
	allWeight := 0
	for _, detail := range sliceOfMap(gacha["gachaDetails"]) {
		cid := intField(detail, "cardId")
		card, ok := cardByID[int64(cid)]
		if !ok {
			continue
		}
		gc := gachaCard{id: cid, prefix: strField(card, "prefix"), charaID: intField(card, "characterId")}
		switch strField(card, "cardRarityType") {
		case "rarity_2":
			r2 = append(r2, gc)
		case "rarity_3":
			r3 = append(r3, gc)
		default:
			gc.weight = intField(detail, "weight")
			allWeight += gc.weight
			r4 = append(r4, gc)
		}
	}

	var allText, keyText strings.Builder
	baodi := true
	var count4, count3, count2 int
	result := make([]int, 0, num)

	for i := 1; i <= num; i++ {
		var rannum float64
		if i%10 == 0 && baodi && !isReverse {
			baodi = false
			rannum = float64(rand.Intn(int((rate4+rate3)*2)+1)) / 2
		} else {
			rannum = float64(rand.Intn(101))
		}

		switch {
		case rannum < rate4 && len(r4) > 0: // 四星
			count4++
			baodi = false
			pick := pickByWeight(r4, allWeight)
			if birthday {
				allText.WriteString("🎀")
				keyText.WriteString("🎀")
			} else {
				allText.WriteString("★★★★[当期]")
				keyText.WriteString("★★★★[当期]")
			}
			line := fmt.Sprintf("%s - %s", pick.prefix, charName(pick.charaID))
			allText.WriteString(line + "\n")
			keyText.WriteString(fmt.Sprintf("%s(第%d抽)\n", line, i))
			result = append(result, pick.id)
		case rannum < rate4+rate3 && len(r3) > 0: // 三星
			count3++
			pick := r3[rand.Intn(len(r3))]
			allText.WriteString(fmt.Sprintf("★★★%s - %s\n", pick.prefix, charName(pick.charaID)))
			result = append(result, pick.id)
		case len(r2) > 0: // 二星
			count2++
			pick := r2[rand.Intn(len(r2))]
			allText.WriteString(fmt.Sprintf("★★%s - %s\n", pick.prefix, charName(pick.charaID)))
			result = append(result, pick.id)
		}
	}

	head := fmt.Sprintf("id:%d[%s]\n", gachaID, gachaName)
	switch {
	case num == 10:
		return head + allText.String(), result, gachaName, true
	case num < 10:
		return head + allText.String(), result, gachaName, true
	default:
		star := "四星"
		if birthday {
			star = "生日卡"
		}
		return fmt.Sprintf("%s%d抽模拟抽卡，只显示抽到的四星如下:\n%s\n%s：%d 三星：%d 二星：%d",
			head, num, keyText.String(), star, count4, count3, count2), result, gachaName, true
	}
}

// pickByWeight 按权重随机选一张四星，对齐 Python 的累加权重法。
func pickByWeight(cards []gachaCard, allWeight int) gachaCard {
	if allWeight <= 0 {
		return cards[rand.Intn(len(cards))]
	}
	target := rand.Intn(allWeight)
	acc := 0
	for _, c := range cards {
		acc += c.weight
		if acc >= target {
			return c
		}
	}
	return cards[len(cards)-1]
}

// makeCharNamer 返回 characterId -> 角色名 的查询函数，对齐 getcharaname。
func makeCharNamer(chars []map[string]any) func(int) string {
	byID := make(map[int]map[string]any, len(chars))
	for _, c := range chars {
		byID[intField(c, "id")] = c
	}
	return func(id int) string {
		c, ok := byID[id]
		if !ok {
			return ""
		}
		return strField(c, "firstName") + strField(c, "givenName")
	}
}
