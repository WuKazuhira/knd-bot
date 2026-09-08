package pjsk

import (
	"context"
	"strconv"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/cards"
	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

// CardInfoModule 实现卡面详情查询（cardinfo）。
//
// 解析卡面核心信息（基本字段/综合力/技能/角色/限定）并出图。技能描述的
// {{}} 数值替换、关联 event/music/gacha 信息、CN 翻译作为增强项暂缓。
type CardInfoModule struct {
	md   *masterdata.Loader
	draw *draw.Client
}

// NewCardInfoModule 创建卡面详情模块。
func NewCardInfoModule(md *masterdata.Loader, d *draw.Client) *CardInfoModule {
	return &CardInfoModule{md: md, draw: d}
}

// Register 注册卡面详情指令。
func (m *CardInfoModule) Register(r *router.Router) {
	r.Register("cardinfo", nil, m.handle)
}

// paramTypeMap 归一化综合力字段名，对齐 getinfo 的 mapping。
var paramTypeMap = map[string]string{
	"performance": "param1",
	"vocal":       "param2",
	"technical":   "param2", "technique": "param2",
	"visual": "param3", "stamina": "param3",
}

func (m *CardInfoModule) handle(ctx context.Context, req router.Request) *onebot.ActionRequest {
	cardID, err := strconv.Atoi(strings.TrimSpace(req.Arg))
	if err != nil {
		return nil // 非数字：不响应（对齐 Python return）
	}
	server := int(req.Server)
	allcards, err := m.md.Load("cards.json", server)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}

	var card map[string]any
	for _, c := range allcards {
		if intField(c, "id") == cardID {
			card = c
			break
		}
	}
	if card == nil {
		return onebot.ReplyText(req.Event, "没有此id的卡面", false)
	}

	payload := m.buildPayload(card, server)
	img, err := m.draw.Render(ctx, "cardinfo", payload)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}

// buildPayload 构造 cardinfo 出图载荷，对齐 CardInfoView._FIELDS。
func (m *CardInfoModule) buildPayload(card map[string]any, server int) map[string]any {
	cardID := intField(card, "id")
	characterID := intField(card, "characterId")
	skillID := intField(card, "skillId")

	unit := "none"
	if su := strField(card, "supportUnit"); su != "none" && su != "" {
		unit = su
	}

	// 综合力
	cardParameters := parseCardParameters(card["cardParameters"])

	// 发布时间
	releaseAt := ""
	if ts := intField(card, "releaseAt"); ts > 0 {
		releaseAt = time.Unix(int64(ts)/1000, 0).Format("2006/01/02 15:04:05")
	}

	// 招募语/技能名（JP）
	gachaPhrase := map[string]any{}
	if gp := strField(card, "gachaPhrase"); gp != "-" && gp != "" {
		gachaPhrase["JP"] = gp
	}
	cardSkillName := map[string]any{"JP": strField(card, "cardSkillName")}

	// 技能描述（JP，原文，不做数值替换）
	cardSkillDes := map[string]any{}
	if skills, err := m.md.Load("skills.json", server); err == nil {
		for _, s := range skills {
			if intField(s, "id") == skillID {
				cardSkillDes["JP"] = strField(s, "description")
				break
			}
		}
	}

	// 角色名 + 团体
	charaName := ""
	if chars, err := m.md.Load("gameCharacters.json", server); err == nil {
		for _, c := range chars {
			if intField(c, "id") == characterID {
				charaName = strings.TrimSpace(strField(c, "firstName") + " " + strField(c, "givenName"))
				if unit == "none" {
					if u := strField(c, "unit"); u != "" {
						unit = u
					}
				}
				break
			}
		}
	}

	// 限定判定（hair 服装关联）
	cardCostume3ds, _ := m.md.Load("cardCostume3ds.json", server)
	costume3ds, _ := m.md.Load("costume3ds.json", server)
	isLimited := cards.CardType(cardID, cardCostume3ds, costume3ds) == 1

	return map[string]any{
		"config":         map[string]any{"event": false, "music": false, "gacha": false},
		"pjsk_type":      server,
		"id":             cardID,
		"characterId":    characterID,
		"costume3dId":    0,
		"skillId":        skillID,
		"unit":           unit,
		"cardRarityType": strField(card, "cardRarityType"),
		"attr":           strField(card, "attr"),
		"isLimited":      isLimited,
		"cardParameters": cardParameters,
		"releaseAt":      releaseAt,
		"charaName":      charaName,
		"prefix":         strField(card, "prefix"),
		"gachaPhrase":    gachaPhrase,
		"cardSkillName":  cardSkillName,
		"cardSkillDes":   cardSkillDes,
		"assets":         map[string]any{"card": strField(card, "assetbundleName"), "costume": map[string]any{}},
		"event":          map[string]any{},
		"music":          map[string]any{},
		"gacha":          map[string]any{},
	}
}

// parseCardParameters 解析综合力，兼容 JP(list) 与 CN(dict) 两种格式，取每类最大值。
func parseCardParameters(raw any) map[string]any {
	out := map[string]any{}
	switch v := raw.(type) {
	case map[string]any: // CN: {param: [powers...]}
		for ptype, powers := range v {
			out[ptype] = maxOfAny(powers)
		}
	case []any: // JP: [{cardParameterType, power}, ...]
		for _, item := range v {
			im, ok := item.(map[string]any)
			if !ok {
				continue
			}
			ptype := strings.ToLower(strField(im, "cardParameterType"))
			if ptype == "" {
				continue
			}
			norm := ptype
			if mapped, ok := paramTypeMap[ptype]; ok {
				norm = mapped
			}
			power := intField(im, "power")
			if cur, ok := out[norm].(int); !ok || power > cur {
				out[norm] = power
			}
		}
	}
	return out
}

func maxOfAny(v any) int {
	switch p := v.(type) {
	case []any:
		best := 0
		for _, x := range p {
			if f, ok := x.(float64); ok && int(f) > best {
				best = int(f)
			}
		}
		return best
	case float64:
		return int(p)
	}
	return 0
}
