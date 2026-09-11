// Package deckopts 解析组卡命令并构建 deck-service options。
package deckopts

import (
	"regexp"
	"strconv"
	"strings"
)

var charaAliases = map[string]int{
	"ick": 1, "ichika": 1, "saki": 2, "hnm": 3, "honami": 3, "shiho": 4,
	"mnr": 5, "minori": 5, "hrk": 6, "haruka": 6, "airi": 7, "szk": 8, "shizuku": 8,
	"khn": 9, "kohane": 9, "an": 10, "akt": 11, "akito": 11, "toya": 12,
	"tks": 13, "tsukasa": 13, "emu": 14, "nene": 15, "rui": 16, "knd": 17,
	"kanade": 17, "mfy": 18, "mafuyu": 18, "ena": 19, "mzk": 20, "mizuki": 20,
	"miku": 21, "rin": 22, "len": 23, "luka": 24, "meiko": 25, "kaito": 26,
}

var units = map[string]string{"ln": "light_sound", "leo": "light_sound", "leoneed": "light_sound", "mmj": "idol", "moremorejump": "idol", "vbs": "street", "vivid": "street", "ws": "theme_park", "wonderlands": "theme_park", "25h": "school_refusal", "25时": "school_refusal", "vs": "piapro", "virtual": "piapro"}
var attrs = map[string]string{"蓝": "cool", "cool": "cool", "粉": "cute", "橙": "cute", "cute": "cute", "橘": "happy", "黄": "happy", "happy": "happy", "紫": "mysterious", "mysterious": "mysterious", "绿": "pure", "pure": "pure"}
var eventRe = regexp.MustCompile(`(?i)(?:活动|event)\s*(\d+)`)
var numberRe = regexp.MustCompile(`(?:^|\s)(\d{1,5})(?:\s|$)`)
var musicRe = regexp.MustCompile(`(?:歌曲|music)\s*(\d+)`)

func config12() map[string]any {
	return map[string]any{"disable": false, "level_max": true, "episode_read": true, "master_max": true, "skill_max": true, "canvas": false}
}
func config34() map[string]any {
	return map[string]any{"disable": false, "level_max": true, "episode_read": false, "master_max": false, "skill_max": false, "canvas": false}
}
func configNone() map[string]any {
	return map[string]any{"disable": false, "level_max": false, "episode_read": false, "master_max": false, "skill_max": false, "canvas": false}
}

func baseOptions() map[string]any {
	return map[string]any{"rarity_1_config": config12(), "rarity_2_config": config12(), "rarity_3_config": config34(), "rarity_4_config": config34(), "rarity_birthday_config": config34(), "target": "score", "live_type": "multi", "algorithm": "all"}
}

func applyCommon(args string, o map[string]any, resolve func(string) int) string {
	args = strings.TrimSpace(strings.ToLower(args))
	if strings.Contains(args, "顶配") || strings.Contains(args, "满配") || strings.Contains(args, "次顶配") || strings.Contains(args, "次满配") || strings.Contains(args, "中配") {
		setConfigs(o, "skill_max")
		setConfigs(o, "master_max")
		setConfigs(o, "episode_read")
		for _, k := range []string{"顶配", "满配", "次顶配", "次满配", "中配"} {
			args = strings.Replace(args, k, "", 1)
		}
	}
	if strings.Contains(args, "当前") || strings.Contains(args, "目前") {
		o["use_current_deck"] = true
		args = strings.Replace(strings.Replace(args, "当前", "", 1), "目前", "", 1)
	}
	if strings.Contains(args, "单人") {
		o["live_type"] = "solo"
		args = strings.Replace(args, "单人", "", 1)
	}
	if strings.Contains(args, "自动") || strings.Contains(args, "auto") {
		o["live_type"] = "auto"
		args = strings.ReplaceAll(strings.ReplaceAll(args, "自动", ""), "auto", "")
	}
	if strings.Contains(args, "综合力") || strings.Contains(args, "综合") || strings.Contains(args, "power") {
		o["target"] = "power"
		args = strings.Replace(args, "综合力", "", 1)
		args = strings.Replace(args, "综合", "", 1)
		args = strings.Replace(args, "power", "", 1)
	}
	if strings.Contains(args, "倍率") || strings.Contains(args, "实效") || strings.Contains(args, "skill") || strings.Contains(args, "时效") {
		o["target"] = "skill"
		for _, k := range []string{"倍率", "实效", "skill", "时效"} {
			args = strings.Replace(args, k, "", 1)
		}
	}
	if strings.Contains(args, "满技能") || strings.Contains(args, "满技") || strings.Contains(args, "skillmax") || strings.Contains(args, "slv4") {
		setConfigs(o, "skill_max")
		for _, k := range []string{"满技能", "满技", "skillmax", "slv4"} {
			args = strings.Replace(args, k, "", 1)
		}
	}
	if strings.Contains(args, "满突破") || strings.Contains(args, "满破") || strings.Contains(args, "rankmax") || strings.Contains(args, "5破") {
		setConfigs(o, "master_max")
		for _, k := range []string{"满突破", "满破", "rankmax", "5破"} {
			args = strings.Replace(args, k, "", 1)
		}
	}
	if strings.Contains(args, "已读") || strings.Contains(args, "满剧情") {
		setConfigs(o, "episode_read")
		for _, k := range []string{"已读", "满剧情"} {
			args = strings.Replace(args, k, "", 1)
		}
	}
	if strings.Contains(args, "画布") || strings.Contains(args, "画板") {
		setConfigs(o, "canvas")
		args = strings.ReplaceAll(strings.ReplaceAll(args, "画布", ""), "画板", "")
	}
	if strings.Contains(args, "dfs") {
		o["algorithm"] = "dfs"
		args = strings.Replace(args, "dfs", "", 1)
	}
	if strings.Contains(args, "ga") {
		o["algorithm"] = "ga"
		args = strings.Replace(args, "ga", "", 1)
	}
	if strings.Contains(args, "#") {
		parts := strings.SplitN(strings.ReplaceAll(args, "＃", "#"), "#", 2)
		args = strings.TrimSpace(parts[0])
		var cards []int
		var chars []int
		for _, p := range strings.Fields(parts[1]) {
			if n, err := strconv.Atoi(p); err == nil {
				cards = append(cards, n)
			} else if resolve != nil {
				if id := resolve(p); id != 0 {
					chars = append(chars, id)
				}
			} else if id := charaAliases[p]; id != 0 {
				chars = append(chars, id)
			}
		}
		if len(cards) > 0 {
			if len(cards) > 5 {
				cards = cards[:5]
			}
			o["fixed_cards"] = cards
		}
		if len(chars) > 0 {
			if len(chars) > 5 {
				chars = chars[:5]
			}
			o["fixed_characters"] = chars
			o["forced_leader_character_id"] = chars[0]
		}
	}
	for _, p := range strings.Fields(args) {
		if strings.HasPrefix(p, "-") {
			if n, err := strconv.Atoi(strings.TrimPrefix(p, "-")); err == nil && n > 0 && n < 5000 {
				addExcluded(o, n)
				args = strings.Replace(args, p, "", 1)
			}
		}
	}
	return strings.Join(strings.Fields(args), " ")
}

func setConfigs(o map[string]any, field string) {
	for _, k := range []string{"rarity_1_config", "rarity_2_config", "rarity_3_config", "rarity_4_config", "rarity_birthday_config"} {
		if c, ok := o[k].(map[string]any); ok {
			c[field] = true
		}
	}
}
func addExcluded(o map[string]any, id int) {
	list, _ := o["single_card_configs"].([]map[string]any)
	o["single_card_configs"] = append(list, map[string]any{"card_id": id, "disable": true})
}

func parseEventMusic(args string, o map[string]any, currentEvent int64) string {
	if m := eventRe.FindStringSubmatch(args); len(m) > 1 {
		o["event_id"], _ = strconv.ParseInt(m[1], 10, 64)
		args = strings.Replace(args, m[0], "", 1)
	} else if currentEvent > 0 {
		o["event_id"] = currentEvent
	}
	if m := musicRe.FindStringSubmatch(args); len(m) > 1 {
		o["music_id"], _ = strconv.ParseInt(m[1], 10, 64)
		args = strings.Replace(args, m[0], "", 1)
	} else if m := numberRe.FindStringSubmatch(args); len(m) > 1 && o["event_id"] != nil {
		if n, _ := strconv.ParseInt(m[1], 10, 64); n >= 10000 {
			o["music_id"] = n
			args = strings.Replace(args, m[0], "", 1)
		}
	}
	if o["music_id"] == nil {
		o["music_id"] = int64(10000)
	}
	o["music_diff"] = "master"
	for _, d := range []string{"easy", "normal", "hard", "expert", "master", "append"} {
		if strings.Contains(strings.ToLower(args), d) {
			o["music_diff"] = d
			args = strings.Replace(strings.ToLower(args), d, "", 1)
			break
		}
	}
	return strings.Join(strings.Fields(args), " ")
}

func BuildEventOptions(args string, currentEvent int64, resolve func(string) int, timeoutMS, returnNum int) map[string]any {
	o := baseOptions()
	args = applyCommon(args, o, resolve)
	// 团体+属性组合表示模拟活动（如 ln蓝），不应附带当前 event_id。
	var simulatedUnit, simulatedAttr bool
	for k, v := range units {
		if strings.Contains(args, k) {
			o["event_unit"] = v
			args = strings.Replace(args, k, "", 1)
			simulatedUnit = true
			break
		}
	}
	for k, v := range attrs {
		if strings.Contains(args, k) {
			o["event_attr"] = v
			args = strings.Replace(args, k, "", 1)
			simulatedAttr = true
			break
		}
	}
	if simulatedUnit && simulatedAttr {
		currentEvent = 0
	}
	if strings.Contains(args, "纯") {
		for k, v := range units {
			if strings.Contains(args, "纯"+k) {
				o["unit_filter"] = v
				break
			}
		}
		for k, v := range attrs {
			if strings.Contains(args, "纯"+k) {
				o["attr_filter"] = v
				break
			}
		}
	}
	args = parseEventMusic(args, o, currentEvent)
	o["timeout_ms"] = timeoutMS
	o["limit"] = returnNum
	o["multi_live_teammate_power"] = 250000
	o["multi_live_teammate_score_up"] = 200
	return o
}

func BuildNoEventOptions(args string, resolve func(string) int, timeoutMS, returnNum int) map[string]any {
	o := baseOptions()
	args = applyCommon(args, o, resolve)
	args = parseEventMusic(args, o, 0)
	delete(o, "event_id")
	o["music_id"] = int64(10000)
	o["music_diff"] = "master"
	o["timeout_ms"] = timeoutMS
	o["limit"] = returnNum
	return o
}

func BuildChallengeOptions(args string, resolve func(string) int, timeoutMS, returnNum int) map[string]any {
	o := baseOptions()
	o["live_type"] = "challenge"
	args = applyCommon(args, o, resolve)
	if o["live_type"] == "auto" {
		o["live_type"] = "challenge_auto"
	}
	for _, p := range strings.Fields(args) {
		if resolve != nil {
			if id := resolve(p); id != 0 {
				o["challenge_live_character_id"] = id
				break
			}
		} else if id := charaAliases[p]; id != 0 {
			o["challenge_live_character_id"] = id
			break
		}
	}
	o["music_id"] = int64(10000)
	o["music_diff"] = "master"
	o["timeout_ms"] = timeoutMS
	o["limit"] = returnNum
	return o
}

func BuildBonusOptions(args string, currentEvent int64, timeoutMS, returnNum int) map[string]any {
	o := map[string]any{"algorithm": "dfs", "target": "bonus", "live_type": "solo", "music_id": int64(10000), "music_diff": "master", "timeout_ms": timeoutMS, "limit": returnNum}
	for _, k := range []string{"rarity_1_config", "rarity_2_config", "rarity_3_config", "rarity_4_config", "rarity_birthday_config"} {
		o[k] = configNone()
	}
	if m := eventRe.FindStringSubmatch(args); len(m) > 1 {
		o["event_id"], _ = strconv.ParseInt(m[1], 10, 64)
		args = strings.Replace(args, m[0], "", 1)
	} else if currentEvent > 0 {
		o["event_id"] = currentEvent
	}
	nums := strings.Fields(args)
	var bonuses []int
	for _, p := range nums {
		if n, err := strconv.Atoi(p); err == nil && n >= 0 {
			bonuses = append(bonuses, n)
		}
	}
	if len(bonuses) > 0 {
		o["target_bonus_list"] = bonuses
	}
	return o
}
