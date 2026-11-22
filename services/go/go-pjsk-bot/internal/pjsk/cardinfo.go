package pjsk

import (
	"context"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

// CardInfoModule 实现卡面详情查询（cardinfo）。
//
// 解析卡面核心信息（基本字段/综合力/技能/角色/限定/技能数值模板替换）并出图。
// 关联 event/music/gacha 信息作为后续增强项。
type CardInfoModule struct {
	md      *masterdata.Loader
	draw    *draw.Client
	refresh *QueryRefresher
}

// NewCardInfoModule 创建卡面详情模块。
// refresh 可选；未注入时不注册显式远端刷新能力。
func NewCardInfoModule(md *masterdata.Loader, d *draw.Client, refreshers ...*QueryRefresher) *CardInfoModule {
	var refresh *QueryRefresher
	if len(refreshers) > 0 {
		refresh = refreshers[0]
	}
	return &CardInfoModule{md: md, draw: d, refresh: refresh}
}

// Register 注册卡面详情指令。
func (m *CardInfoModule) Register(r *router.Router) {
	r.RegisterNumericSuffix("cardinfo", []string{"cn_cardinfo", "tw_cardinfo"}, m.handle)
}

// paramTypeMap 归一化综合力字段名，对齐 getinfo 的 mapping。
var paramTypeMap = map[string]string{
	"performance": "param1",
	"vocal":       "param2",
	"technical":   "param2", "technique": "param2",
	"visual": "param3", "stamina": "param3",
}

func (m *CardInfoModule) handle(ctx context.Context, req router.Request) *onebot.ActionRequest {
	opts := parseQueryOptions(req.Arg)
	return m.handleArg(ctx, req, opts.Arg, opts.Refresh)
}

func (m *CardInfoModule) handleArg(ctx context.Context, req router.Request, arg string, refresh bool) *onebot.ActionRequest {
	cardID, ok := parseIntToken(strings.TrimSpace(arg))
	if !ok {
		return nil // 非数字：不响应（对齐 Python return）
	}
	server := int(req.Server)
	if refresh && m.refresh != nil {
		if err := m.refresh.Refresh(ctx, server, QueryRefreshCards); err != nil {
			return onebot.ReplyText(req.Event, "刷新卡面数据失败："+err.Error(), false)
		}
	}
	allcards, err := m.md.Load("cards.json", server)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}

	card, found := orderedMasterItem(allcards, cardID, "releaseAt")
	if !found {
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

	// 招募语/技能名
	srcLang := serverLangCode(server)
	gachaPhrase := map[string]any{}
	if gp := strField(card, "gachaPhrase"); gp != "-" && gp != "" {
		gachaPhrase[srcLang] = gp
	}
	cardSkillName := map[string]any{srcLang: strField(card, "cardSkillName")}

	// 技能描述：用 skillEffects 替换 {{id;kind}} 占位符；非 CN 服附带国服官方中文技能描述（数值仍按当前服计算）。
	cardSkillDes := map[string]any{}
	var targetSkill map[string]any
	if skills, err := m.md.Load("skills.json", server); err == nil {
		targetSkill = findByIntID(skills, skillID)
		if targetSkill != nil {
			if desc := formatSkillDescription(targetSkill, strField(targetSkill, "description"), charaName); desc != "" {
				cardSkillDes[srcLang] = desc
			}
		}
	}
	if server != 2 && targetSkill != nil {
		if cnSkills, err := m.md.Load("skills.json", 2); err == nil {
			if cnSkill := findByIntID(cnSkills, skillID); cnSkill != nil {
				if cnDesc := formatSkillDescription(targetSkill, strField(cnSkill, "description"), charaName); cnDesc != "" {
					cardSkillDes["CN"] = cnDesc
				}
			}
		}
	}

	// 限定判定（hair 服装关联）
	isLimited := cachedCardIndex(m.md, server).IsLimited(cardID)

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

var skillPlaceholderRe = regexp.MustCompile(`\{\{([^}]+)\}\}`)

func serverLangCode(server int) string {
	switch server {
	case 1:
		return "TW"
	case 2:
		return "CN"
	default:
		return "JP"
	}
}

func findByIntID(items []map[string]any, targetID int) map[string]any {
	for _, it := range items {
		if intField(it, "id") == targetID {
			return it
		}
	}
	return nil
}

func formatSkillNum(v float64) string {
	return strconv.FormatFloat(v, 'f', -1, 64)
}

func collapseLevels(vals []string) string {
	if len(vals) == 0 {
		return ""
	}
	first := vals[0]
	for _, v := range vals[1:] {
		if v != first {
			return strings.Join(vals, "/")
		}
	}
	return first
}

func skillDetails(effect map[string]any) []map[string]any {
	if effect == nil {
		return nil
	}
	return sliceOfMap(effect["skillEffectDetails"])
}

func skillEnhanceValue(effect map[string]any) float64 {
	if effect == nil {
		return 0
	}
	enh, _ := effect["skillEnhance"].(map[string]any)
	return floatField(enh, "activateEffectValue")
}

// formatSkillDescription 将技能描述中的 {{effectIds;kind}} 占位符替换为具体数值序列。
func formatSkillDescription(skill map[string]any, rawDesc, charaName string) string {
	if rawDesc == "" {
		return ""
	}
	effects := map[int]map[string]any{}
	for _, ef := range sliceOfMap(skill["skillEffects"]) {
		effects[intField(ef, "id")] = ef
	}
	out := skillPlaceholderRe.ReplaceAllStringFunc(rawDesc, func(match string) string {
		inner := match[2 : len(match)-2]
		parts := strings.SplitN(inner, ";", 2)
		if len(parts) < 2 {
			return ""
		}
		var ids []int
		for _, rawID := range strings.Split(parts[0], ",") {
			if id, ok := parseIntToken(strings.TrimSpace(rawID)); ok {
				ids = append(ids, id)
			}
		}
		if len(ids) == 0 {
			return ""
		}
		kind := strings.TrimSpace(parts[1])
		return resolveSkillPlaceholder(effects, ids, kind, charaName)
	})
	return strings.ReplaceAll(out, "  ", " ")
}

func resolveSkillPlaceholder(effects map[int]map[string]any, ids []int, kind, charaName string) string {
	switch kind {
	case "c":
		if charaName != "" {
			return charaName
		}
		return "?"
	case "e":
		for _, id := range ids {
			if ef := effects[id]; ef != nil {
				return formatSkillNum(skillEnhanceValue(ef))
			}
		}
		return "0"
	case "r":
		if len(ids) >= 2 && effects[ids[0]] != nil && effects[ids[1]] != nil {
			d0 := skillDetails(effects[ids[0]])
			d1 := skillDetails(effects[ids[1]])
			if len(d0) > 0 && len(d1) > 0 {
				v0 := formatSkillNum(floatField(d0[0], "activateEffectValue"))
				v1 := formatSkillNum(floatField(d1[len(d1)-1], "activateEffectValue"))
				if v0 == v1 {
					return v0
				}
				return v0 + "~" + v1
			}
		}
	case "o":
		if len(ids) >= 2 && effects[ids[0]] != nil && effects[ids[1]] != nil {
			d0 := skillDetails(effects[ids[0]])
			d1 := skillDetails(effects[ids[1]])
			vals := make([]string, 0, len(d0))
			for i := 0; i < len(d0) && i < len(d1); i++ {
				v2 := floatField(d1[i], "activateEffectValue2")
				if v2 == 0 {
					v2 = floatField(d1[i], "activateEffectValue")
				}
				vals = append(vals, formatSkillNum(floatField(d0[i], "activateEffectValue")+v2))
			}
			return collapseLevels(vals)
		}
	case "u":
		if len(ids) >= 2 && effects[ids[0]] != nil && effects[ids[1]] != nil {
			d0 := skillDetails(effects[ids[0]])
			d1 := skillDetails(effects[ids[1]])
			vals := make([]string, 0, len(d0))
			for i := 0; i < len(d0) && i < len(d1); i++ {
				vals = append(vals, formatSkillNum(floatField(d0[i], "activateEffectValue")+floatField(d1[i], "activateEffectValue")))
			}
			return collapseLevels(vals)
		}
	case "v":
		if len(ids) == 2 && effects[ids[0]] != nil && effects[ids[1]] != nil {
			d0 := skillDetails(effects[ids[0]])
			d1 := skillDetails(effects[ids[1]])
			if len(d0) == len(d1) && len(d0) > 0 {
				vals := make([]string, 0, len(d0))
				for i := range d0 {
					vals = append(vals, formatSkillNum(floatField(d0[i], "activateEffectValue")+floatField(d1[i], "activateEffectValue")))
				}
				return collapseLevels(vals)
			}
		}
	}

	var vals []string
	for _, id := range ids {
		ef := effects[id]
		if ef == nil {
			continue
		}
		enh := skillEnhanceValue(ef)
		for _, item := range skillDetails(ef) {
			switch kind {
			case "d":
				vals = append(vals, formatSkillNum(floatField(item, "activateEffectDuration")))
			case "m":
				vals = append(vals, formatSkillNum(floatField(item, "activateEffectValue")+5*enh))
			default:
				vals = append(vals, formatSkillNum(floatField(item, "activateEffectValue")))
			}
		}
		if kind == "s" && len(vals) > 0 {
			break
		}
	}
	return collapseLevels(vals)
}
