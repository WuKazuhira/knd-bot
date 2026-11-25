package pjsk

import (
	"context"
	"regexp"
	"strconv"
	"strings"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

type eventArgParams struct {
	EventType    string
	EventAttr    string
	UnitsName    []string
	CharasID     []any
	IsEqualUnits bool
	IsContainAll bool
	IsLegal      bool
	IsTeamEvent  *bool
}

var (
	eventUnitDict = map[string]string{
		"ln": "light_sound", "mmj": "idol", "vbs": "street", "ws": "theme_park", "25h": "school_refusal",
	}
	eventTeamDict = map[string]bool{
		"箱活": true, "团队活": true, "混活": false, "团外活": false,
	}
	eventTypeDict = map[string]string{
		"普活": "marathon", "马拉松": "marathon", "marathon": "marathon",
		"5v5": "cheerful_carnival", "cheerful_carnival": "cheerful_carnival",
		"wl": "world_bloom", "worldlink": "world_bloom", "world_bloom": "world_bloom", "世界链接": "world_bloom",
	}
	eventAttrDict = map[string]string{
		"蓝星": "cool", "紫月": "mysterious", "橙心": "happy", "黄心": "happy", "粉花": "cute", "绿草": "pure",
		"蓝": "cool", "紫": "mysterious", "橙": "happy", "黄": "happy", "粉": "cute", "绿": "pure",
		"星": "cool", "月": "mysterious", "心": "happy", "花": "cute", "草": "pure",
		"cool": "cool", "mysterious": "mysterious", "happy": "happy", "cute": "cute", "pure": "pure",
	}
	eventCharaDict = map[string]int{
		"ick": 1, "saki": 2, "hnm": 3, "shiho": 4,
		"mnr": 5, "hrk": 6, "airi": 7, "szk": 8,
		"khn": 9, "an": 10, "akt": 11, "toya": 12,
		"tks": 13, "emu": 14, "nene": 15, "rui": 16,
		"knd": 17, "mfy": 18, "ena": 19, "mzk": 20,
		"miku": 21, "rin": 22, "len": 23, "luka": 24, "meiko": 25, "kaito": 26,
	}
	eventChara2Unit = map[string][]int{
		"light_sound":    {1, 2, 3, 4},
		"idol":           {5, 6, 7, 8},
		"street":         {9, 10, 11, 12},
		"theme_park":     {13, 14, 15, 16},
		"school_refusal": {17, 18, 19, 20},
	}
)

func (m *EventModule) resolveCharaAlias(alias string) int {
	alias = strings.ToLower(strings.TrimSpace(alias))
	if cid, ok := eventCharaDict[alias]; ok {
		return cid
	}
	if m.chara != nil {
		return m.chara.Resolve(alias)
	}
	return 0
}

func isVSChara(cid int) bool { return cid > 20 }

var (
	reEventUnitMix   = regexp.MustCompile(`^(ln|mmj|vbs|ws|25h)(?:混|加成)$`)
	reEventUnitMid   = regexp.MustCompile(`^(ln|mmj|vbs|ws|25h)混(ln|mmj|vbs|ws|25h).*$`)
	reEventUnitChara = regexp.MustCompile(`^(ln|mmj|vbs|ws|25h)(.+)`)
)

func (m *EventModule) eventArgParse(args []string) eventArgParams {
	p := eventArgParams{IsEqualUnits: true, IsContainAll: true, IsLegal: true}

	for _, arg := range args {
		if v, ok := eventTeamDict[arg]; ok {
			p.IsTeamEvent = &v
			continue
		}
		if v, ok := eventTypeDict[arg]; ok {
			if p.EventType != "" {
				p.IsLegal = false
				return p
			}
			p.EventType = v
			continue
		}
		if v, ok := eventAttrDict[arg]; ok {
			if p.EventAttr != "" {
				p.IsLegal = false
				return p
			}
			p.EventAttr = v
			continue
		}
		if v, ok := eventUnitDict[arg]; ok {
			p.UnitsName = append(p.UnitsName, v)
			continue
		}
		if mm := reEventUnitMix.FindStringSubmatch(arg); mm != nil {
			p.UnitsName = append(p.UnitsName, eventUnitDict[mm[1]])
			p.IsEqualUnits = false
			continue
		}
		if reEventUnitMid.MatchString(arg) {
			ok := true
			var toAdd []string
			for _, part := range strings.Split(arg, "混") {
				matched := ""
				for k := range eventUnitDict {
					if strings.HasPrefix(part, k) {
						matched = eventUnitDict[k]
						break
					}
				}
				if matched == "" {
					ok = false
					break
				}
				toAdd = append(toAdd, matched)
			}
			if !ok {
				p.IsLegal = false
				return p
			}
			p.UnitsName = append(p.UnitsName, toAdd...)
			continue
		}
		if mm := reEventUnitChara.FindStringSubmatch(arg); mm != nil {
			unit := mm[1]
			alias := mm[2]
			cid := m.resolveCharaAlias(alias)
			if cid != 0 && isVSChara(cid) {
				p.CharasID = append(p.CharasID, [2]any{cid, eventUnitDict[unit]})
				continue
			}
			p.IsLegal = false
			return p
		}
		cid := m.resolveCharaAlias(arg)
		if cid != 0 {
			p.CharasID = append(p.CharasID, cid)
			continue
		}
		p.IsLegal = false
		return p
	}

	for _, item := range p.CharasID {
		var unit string
		switch v := item.(type) {
		case [2]any:
			unit, _ = v[1].(string)
		case int:
			if v <= 20 {
				for u, ids := range eventChara2Unit {
					if intSliceContains(ids, v) {
						unit = u
						break
					}
				}
			} else {
				continue
			}
		}
		if unit == "" {
			continue
		}
		if len(p.UnitsName) != 0 && !strSliceContains(p.UnitsName, unit) {
			p.UnitsName = append(p.UnitsName, unit)
			p.IsEqualUnits = false
		}
	}

	if p.IsTeamEvent != nil && (len(p.UnitsName) > 0 || len(p.CharasID) > 0) {
		p.IsLegal = false
	}

	p.UnitsName = dedupStrings(p.UnitsName)
	p.CharasID = dedupCharas(p.CharasID)
	return p
}

func (p eventArgParams) toCharasPayload() []any {
	out := make([]any, 0, len(p.CharasID))
	for _, item := range p.CharasID {
		if t, ok := item.([2]any); ok {
			out = append(out, []any{t[0], t[1]})
		} else {
			out = append(out, item)
		}
	}
	return out
}

func (m *EventModule) handleFindEvent(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	opts := parseQueryOptions(req.Arg)
	raw := strings.TrimSpace(opts.Arg)
	if opts.Refresh && m.refresh != nil {
		if err := m.refresh.Refresh(ctx, server, QueryRefreshEvents); err != nil {
			return onebot.ReplyText(req.Event, "刷新活动数据失败："+err.Error(), false)
		}
	}
	events, err := m.md.Load("events.json", server)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}

	if ev, _, banErr := extractBanEventArg(m.md, server, raw, m.resolveCharaAlias); banErr != "" {
		return onebot.ReplyText(req.Event, banErr, true)
	} else if ev != nil {
		return m.renderEventInfo(ctx, req, events, ev.ID, server)
	}

	if requested, ok := parseIntToken(raw); ok {
		event, found := orderedMasterItem(events, requested, "startAt")
		if !found {
			return onebot.ReplyText(req.Event, "未找到活动", false)
		}
		eventID := intField(event, "id")
		if eventID == 0 {
			return onebot.ReplyText(req.Event, "未找到活动", false)
		}
		return m.renderEventInfo(ctx, req, events, eventID, server)
	}

	fields := strings.Fields(raw)
	// Router 会把别名归一化成 findevent，这里要认规范名，避免“活动列表”无参数退化成单活动查询
	isCatalogCmd := isEventCatalogCommand(req.Command)

	isAllList := isCatalogCmd && len(fields) == 1 && strings.ToLower(fields[0]) == "all"
	if isAllList {
		fields = nil
	}
	var displayLimit any = nil
	if isCatalogCmd && len(fields) == 0 && !isAllList {
		displayLimit = 50
	}

	if len(fields) == 0 && !isCatalogCmd {
		return m.handle(ctx, req)
	}

	params := m.eventArgParse(fields)
	if !params.IsLegal {
		return onebot.ReplyText(req.Event, "参数无法识别，请检查活动类型/属性/组合/角色的写法", true)
	}

	payload := map[string]any{
		"pjsk_type":            server,
		"display_limit":        displayLimit,
		"event_type":           nilIfEmpty(params.EventType),
		"event_attr":           nilIfEmpty(params.EventAttr),
		"event_units_name":     params.UnitsName,
		"event_charas_id":      params.toCharasPayload(),
		"isEqualAllUnits":      params.IsEqualUnits,
		"isContainAllCharasId": params.IsContainAll,
		"isTeamEvent":          teamEventValue(params.IsTeamEvent),
	}
	img, err := m.draw.Render(ctx, "event_catalog", payload)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	if len(img) == 0 {
		return onebot.ReplyText(req.Event, "没有符合条件的活动", false)
	}
	msg := ""
	if displayLimit != nil {
		msg = "如果想要查询所有活动请输入 活动列表all\n"
	}
	if msg != "" {
		return onebot.SendMessageAction(req.Event, onebot.Message{
			onebot.Text(msg),
			onebot.ImageBytes(base64Encode(img)),
		})
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}

func isEventCatalogCommand(command string) bool {
	switch command {
	case "findevent", "活动列表", "活动图鉴", "活动总览", "活动手册":
		return true
	default:
		return false
	}
}

func nilIfEmpty(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func teamEventValue(b *bool) any {
	if b == nil {
		return nil
	}
	return *b
}

func intSliceContains(s []int, v int) bool {
	for _, x := range s {
		if x == v {
			return true
		}
	}
	return false
}

func strSliceContains(s []string, v string) bool {
	for _, x := range s {
		if x == v {
			return true
		}
	}
	return false
}

func dedupStrings(s []string) []string {
	seen := map[string]bool{}
	out := make([]string, 0, len(s))
	for _, x := range s {
		if !seen[x] {
			seen[x] = true
			out = append(out, x)
		}
	}
	return out
}

func dedupCharas(s []any) []any {
	seen := map[string]bool{}
	out := make([]any, 0, len(s))
	for _, x := range s {
		key := ""
		if t, ok := x.([2]any); ok {
			key = "t:" + itoaAny(t[0]) + ":" + toStr(t[1])
		} else {
			key = "i:" + itoaAny(x)
		}
		if !seen[key] {
			seen[key] = true
			out = append(out, x)
		}
	}
	return out
}

func itoaAny(v any) string {
	if n, ok := v.(int); ok {
		return strconv.Itoa(n)
	}
	return toStr(v)
}

func toStr(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}
