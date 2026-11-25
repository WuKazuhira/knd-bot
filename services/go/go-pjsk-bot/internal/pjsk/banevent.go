package pjsk

import (
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
)

func banEventIDSet(md *masterdata.Loader, server int) map[int]bool {
	events, _ := md.Load("events.json", server)
	eventMusics, _ := md.Load("eventMusics.json", server)

	musicEventIDs := map[int]bool{}
	for _, em := range eventMusics {
		if eid := intField(em, "eventId"); eid != 0 {
			musicEventIDs[eid] = true
		}
	}
	out := map[int]bool{}
	for _, ev := range events {
		et := strField(ev, "eventType")
		id := intField(ev, "id")
		if (et == "marathon" || et == "cheerful_carnival") && musicEventIDs[id] {
			out[id] = true
		}
	}
	out[74] = true // SDL3 特判
	return out
}

func eventCardIDSet(md *masterdata.Loader, server, eventID int) map[int]bool {
	eventCards, _ := md.Load("eventCards.json", server)
	out := map[int]bool{}
	for _, ec := range eventCards {
		if intField(ec, "eventId") == eventID {
			if cid := intField(ec, "cardId"); cid != 0 {
				out[cid] = true
			}
		}
	}
	return out
}

func eventMusicIDList(md *masterdata.Loader, server, eventID int) []int {
	eventMusics, _ := md.Load("eventMusics.json", server)
	type em struct {
		musicID, seq int
	}
	var matched []em
	for _, e := range eventMusics {
		if intField(e, "eventId") == eventID {
			if mid := intField(e, "musicId"); mid != 0 {
				matched = append(matched, em{mid, intField(e, "seq")})
			}
		}
	}
	sort.SliceStable(matched, func(i, j int) bool { return matched[i].seq < matched[j].seq })
	out := make([]int, 0, len(matched))
	for _, e := range matched {
		out = append(out, e.musicID)
	}
	return out
}

func eventBannerCharaID(md *masterdata.Loader, server, eventID int) int {
	cards, _ := md.Load("cards.json", server)
	cardByID := make(map[int]map[string]any, len(cards))
	for _, c := range cards {
		if id := intField(c, "id"); id != 0 {
			cardByID[id] = c
		}
	}
	supplies, _ := md.Load("cardSupplies.json", server)
	supplyType := map[int]string{}
	for _, cs := range supplies {
		if id := intField(cs, "id"); id != 0 {
			supplyType[id] = strField(cs, "cardSupplyType")
		}
	}

	minID := 0
	for cid := range eventCardIDSet(md, server, eventID) {
		card, ok := cardByID[cid]
		if !ok {
			continue
		}
		if st := supplyType[intField(card, "cardSupplyId")]; strings.Contains(st, "festival_limited") {
			continue
		}
		if minID == 0 || cid < minID {
			minID = cid
		}
	}
	if minID == 0 {
		return 0
	}
	return intField(cardByID[minID], "characterId")
}

type banEvent struct {
	ID       int
	Name     string
	Raw      map[string]any
	BanIndex int
}

func charaBanEvents(md *masterdata.Loader, server, charaID int) []banEvent {
	if md == nil || charaID == 0 {
		return nil
	}
	val, err := md.Derived("ban_events_by_chara", server, []string{
		"events.json", "eventMusics.json", "eventCards.json", "cards.json", "cardSupplies.json",
	}, func() (any, error) {
		events, _ := md.Load("events.json", server)
		eventMusics, _ := md.Load("eventMusics.json", server)
		eventCards, _ := md.Load("eventCards.json", server)
		allCards, _ := md.Load("cards.json", server)
		supplies, _ := md.Load("cardSupplies.json", server)

		musicEventIDs := make(map[int]bool, len(eventMusics))
		for _, em := range eventMusics {
			if eid := intField(em, "eventId"); eid != 0 {
				musicEventIDs[eid] = true
			}
		}
		cardByID := make(map[int]map[string]any, len(allCards))
		for _, c := range allCards {
			if id := intField(c, "id"); id != 0 {
				cardByID[id] = c
			}
		}
		supplyType := make(map[int]string, len(supplies))
		for _, cs := range supplies {
			if id := intField(cs, "id"); id != 0 {
				supplyType[id] = strField(cs, "cardSupplyType")
			}
		}
		minNonFesCard := make(map[int]int)
		for _, ec := range eventCards {
			eid := intField(ec, "eventId")
			cid := intField(ec, "cardId")
			if eid == 0 || cid == 0 {
				continue
			}
			card, ok := cardByID[cid]
			if !ok {
				continue
			}
			if st := supplyType[intField(card, "cardSupplyId")]; strings.Contains(st, "festival_limited") {
				continue
			}
			if cur := minNonFesCard[eid]; cur == 0 || cid < cur {
				minNonFesCard[eid] = cid
			}
		}

		byChara := make(map[int][]banEvent)
		for _, ev := range events {
			id := intField(ev, "id")
			et := strField(ev, "eventType")
			if id != 74 && !((et == "marathon" || et == "cheerful_carnival") && musicEventIDs[id]) {
				continue
			}
			minID := minNonFesCard[id]
			if minID == 0 {
				continue
			}
			cid := intField(cardByID[minID], "characterId")
			if cid == 0 {
				continue
			}
			byChara[cid] = append(byChara[cid], banEvent{ID: id, Name: strField(ev, "name"), Raw: ev})
		}
		for cid, list := range byChara {
			sort.SliceStable(list, func(i, j int) bool {
				return intField(list[i].Raw, "startAt") < intField(list[j].Raw, "startAt")
			})
			for i := range list {
				list[i].BanIndex = i + 1
			}
			byChara[cid] = list
		}
		return byChara, nil
	})
	if err != nil {
		return nil
	}
	byChara := val.(map[int][]banEvent)
	return byChara[charaID]
}

// Go regexp 不支持 lookaround，这里先宽松匹配再手动判前后 \w 边界
var reBanEventToken = regexp.MustCompile(`([\p{L}\p{N}\x{3040}-\x{30ff}\x{3400}-\x{9fff}]+?)(\d+)`)

func isWordChar(b byte) bool {
	return b == '_' || (b >= '0' && b <= '9') || (b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z')
}

func extractBanEventArg(md *masterdata.Loader, server int, text string, resolveChara func(string) int) (*banEvent, string, string) {
	raw := text
	if _, ok := parseIntToken(strings.TrimSpace(raw)); ok {
		return nil, raw, ""
	}
	locs := reBanEventToken.FindAllStringSubmatchIndex(raw, -1)
	for _, loc := range locs {
		start, end := loc[0], loc[1]
		aliasStart, aliasEnd := loc[2], loc[3]
		numStart, numEnd := loc[4], loc[5]
		if start > 0 && isWordChar(raw[start-1]) {
			continue
		}
		if end < len(raw) && isWordChar(raw[end]) {
			continue
		}
		alias := strings.ToLower(strings.TrimSpace(raw[aliasStart:aliasEnd]))
		seq := atoiDefault(raw[numStart:numEnd], 0)
		if seq <= 0 {
			continue
		}
		charaID := resolveChara(alias)
		if charaID == 0 {
			continue
		}
		banEvents := charaBanEvents(md, server, charaID)
		if seq > len(banEvents) {
			return nil, raw, "角色" + alias + "只有" + itoaInt(len(banEvents)) + "次箱活"
		}
		ev := banEvents[seq-1]
		rest := strings.TrimSpace(raw[:start] + raw[end:])
		rest = collapseSpaces(rest)
		return &ev, rest, ""
	}
	return nil, raw, ""
}

func collapseSpaces(s string) string {
	return strings.Join(strings.Fields(s), " ")
}

func itoaInt(n int) string {
	return strconv.Itoa(n)
}
