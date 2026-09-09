package pjsk

import (
	"sort"
	"strings"

	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
)

// banEventIDSet 返回箱活活动 ID 集合，对齐 get_ban_events_id_set：
// eventType 为 marathon/cheerful_carnival 且 id 出现在 eventMusics（已上线有歌），
// 外加 id=74 的 SDL3 特判。
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
	out[74] = true // SDL3 特判，对齐 Python
	return out
}

// eventCardIDs 返回活动卡牌 id 集合，对齐 get_event_card_ids。
func eventCardIDs(md *masterdata.Loader, server, eventID int) map[int]bool {
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

// eventBannerCharaID 通过活动新卡中「非 FES 的最小 cardId」推定箱活主角 characterId，
// 对齐 get_event_banner_chara_id。无候选返回 0。
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
	for cid := range eventCardIDs(md, server, eventID) {
		card, ok := cardByID[cid]
		if !ok {
			continue
		}
		// 跳过 FES 限定卡（cardSupplyType 含 festival_limited）。
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

// banEvent 是一个箱活条目（含 1-based 序号）。
type banEvent struct {
	ID       int
	Name     string
	Raw      map[string]any
	BanIndex int
}

// charaBanEvents 返回某角色的全部箱活（按 startAt 升序，附 1-based ban_index），
// 对齐 get_chara_ban_events。
func charaBanEvents(md *masterdata.Loader, server, charaID int) []banEvent {
	if charaID == 0 {
		return nil
	}
	events, _ := md.Load("events.json", server)
	banIDs := banEventIDSet(md, server)
	var result []banEvent
	for _, ev := range events {
		id := intField(ev, "id")
		if !banIDs[id] {
			continue
		}
		if eventBannerCharaID(md, server, id) == charaID {
			result = append(result, banEvent{ID: id, Name: strField(ev, "name"), Raw: ev})
		}
	}
	sort.SliceStable(result, func(i, j int) bool {
		return intField(result[i].Raw, "startAt") < intField(result[j].Raw, "startAt")
	})
	for i := range result {
		result[i].BanIndex = i + 1
	}
	return result
}
