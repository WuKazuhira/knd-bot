package pjsk

import (
	"regexp"
	"sort"
	"strconv"
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

// eventMusicIDList 返回活动歌曲 musicId 列表（按 seq 升序），对齐 get_event_music_ids。
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
	for cid := range eventCardIDSet(md, server, eventID) {
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

// reBanEventToken 匹配「词 + 数字」的候选（如 ena7）。Go regexp 无 lookaround，
// 词部分用非贪婪的字母/数字/假名/汉字，配合调用处的前后边界检查。
var reBanEventToken = regexp.MustCompile(`([\p{L}\p{N}\x{3040}-\x{30ff}\x{3400}-\x{9fff}]+?)(\d+)`)

// isWordChar 判断是否 \w（字母/数字/下划线），用于复刻 Python 的 (?<!\w)/(?!\w) 边界。
func isWordChar(b byte) bool {
	return b == '_' || (b >= '0' && b <= '9') || (b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z')
}

// extractBanEventArg 从文本提取「角色缩写+序号」（ena7）定位某角色第 N 次箱活。
// 返回 (命中的箱活, 去掉该 token 后的剩余文本, 错误提示)。未命中返回 (nil, 原文, "")。
// resolveChara 把角色缩写解析成 characterId（0=未识别）。对齐 extract_ban_event_arg。
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
		// 前边界：token 前一个字符不能是 \w（复刻 (?<!\w)）。
		if start > 0 && isWordChar(raw[start-1]) {
			continue
		}
		// 后边界：token 后一个字符不能是 \w（复刻 (?!\w)）。
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

// collapseSpaces 把连续空白折叠成单空格，对齐 Python re.sub(r'\s+', ' ')。
func collapseSpaces(s string) string {
	return strings.Join(strings.Fields(s), " ")
}

// itoaInt 是 strconv.Itoa 的本地别名（避免在本文件重复导入）。
func itoaInt(n int) string {
	return strconv.Itoa(n)
}
