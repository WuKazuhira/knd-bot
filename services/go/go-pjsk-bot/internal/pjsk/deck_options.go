package pjsk

import (
	"cmp"
	"context"
	"math"
	"regexp"
	"slices"
	"strconv"
	"strings"
)

type deckOptions struct {
	options    map[string]any
	additional map[string]any
	lastArgs   string
}

type deckKeywords struct {
	value string
	words []string
}

var deckUnitFilters = []deckKeywords{
	{"light_sound", []string{"纯ln", "仅ln"}},
	{"idol", []string{"纯mmj", "仅mmj"}},
	{"street", []string{"纯vbs", "仅vbs"}},
	{"theme_park", []string{"纯ws", "仅ws"}},
	{"school_refusal", []string{"纯25h", "纯25时", "仅25h", "仅25时", "纯25", "仅25"}},
	{"piapro", []string{"纯vs", "仅vs", "纯v", "仅v"}},
}

var deckAttrFilters = []deckKeywords{
	{"cool", []string{"纯cool", "仅cool", "纯蓝", "仅蓝"}},
	{"cute", []string{"纯cute", "仅cute", "纯粉", "仅粉", "纯橙", "仅橙"}},
	{"happy", []string{"纯happy", "仅happy", "纯橘", "仅橘", "纯黄", "仅黄"}},
	{"mysterious", []string{"纯mysterious", "仅mysterious", "纯紫", "仅紫"}},
	{"pure", []string{"纯pure", "仅pure", "纯绿", "仅绿"}},
}

var deckUnits = []deckKeywords{
	{"light_sound", []string{"leoneed", "leo", "ln"}},
	{"idol", []string{"moremorejump", "mmj"}},
	{"street", []string{"vivid", "vbs"}},
	{"theme_park", []string{"wonderlands", "ws"}},
	{"school_refusal", []string{"25ji", "25h", "25时", "25"}},
	{"piapro", []string{"virtual", "vs"}},
}

var deckAttrs = []deckKeywords{
	{"cool", []string{"cool", "蓝"}},
	{"cute", []string{"cute", "粉", "橙"}},
	{"happy", []string{"happy", "橘", "黄"}},
	{"mysterious", []string{"mysterious", "紫"}},
	{"pure", []string{"pure", "绿"}},
}

var deckDifficulties = []deckKeywords{
	{"easy", []string{"easy", "ez"}},
	{"normal", []string{"normal", "nm"}},
	{"hard", []string{"hard", "hd"}},
	{"expert", []string{"expert", "exp", "ex"}},
	{"master", []string{"master", "mas", "ma"}},
	{"append", []string{"append", "apd", "ap"}},
}

const deckOmakaseMusicID = 10000

var (
	deckBoostPattern   = regexp.MustCompile(`([0-9]+)\s*(?:boost|火|体力|体)`)
	deckRoundsPattern  = regexp.MustCompile(`([0-9]+(?:\.[0-9]+)?)\s*周回`)
	deckEventPattern   = regexp.MustCompile(`(?:活动|event)\s*([0-9]+)`)
	deckWLPattern      = regexp.MustCompile(`(?:^|\s)wl\s*(?:第)?([0-9]+)(?:章)?(?:\s|$)`)
	deckMusicIDPattern = regexp.MustCompile(`\b(?:10000|[0-9]{1,4})\b`)
)

var deckNumericOptions = []struct {
	key     string
	pattern *regexp.Regexp
}{
	{"teammate_power", regexp.MustCompile(`(?:队友综合力|队友总合力|队友综合|队友总和)\s*([0-9]+)`)},
	{"teammate_scoreup", regexp.MustCompile(`(?:队友实效|队友技能|队友时效)\s*([0-9]+)`)},
	{"area_item_level", regexp.MustCompile(`(?:区域道具|道具|areaitem)\s*([0-9]+)`)},
	{"multi_live_score_up_lower_bound", regexp.MustCompile(`实效\s*(?:>=|>|=|≥)\s*([0-9]+)`)},
	{"current_pt", regexp.MustCompile(`(?:现在|今|现)\s*([0-9][0-9,._]*(?:百万|万|亿|k|w|e)?)`)},
	{"target_pt", regexp.MustCompile(`目标\s*([0-9][0-9,._]*(?:百万|万|亿|k|w|e)?)`)},
}

func takeDeckKeyword(args *string, words ...string) bool {
	for _, word := range words {
		if strings.Contains(*args, word) {
			*args = strings.TrimSpace(strings.ReplaceAll(*args, word, " "))
			return true
		}
	}
	return false
}

func extractDeckKeyword(args *string, groups []deckKeywords) string {
	for _, group := range groups {
		if takeDeckKeyword(args, group.words...) {
			return group.value
		}
	}
	return ""
}

func parseDeckPoints(raw string) (int64, bool) {
	multiplier := 1.0
	for _, suffix := range []struct {
		name  string
		value float64
	}{
		{"百万", 1e6}, {"万", 1e4}, {"亿", 1e8}, {"k", 1e3}, {"w", 1e4}, {"e", 1e6},
	} {
		if number, ok := strings.CutSuffix(raw, suffix.name); ok {
			raw, multiplier = number, suffix.value
			break
		}
	}
	n, err := strconv.ParseFloat(strings.NewReplacer(",", "", "_", "").Replace(raw), 64)
	n *= multiplier
	return int64(n), err == nil && n >= 0 && n < float64(math.MaxInt64)
}

func extractAdditionalOptions(args string) (map[string]any, string) {
	additional := map[string]any{}
	if match := deckBoostPattern.FindStringSubmatch(args); match != nil {
		if n, ok := parseIntToken(match[1]); ok && n <= 10 {
			additional["boost"] = n
			args = strings.Replace(args, match[0], " ", 1)
		}
	}
	for _, filter := range []struct {
		key    string
		groups []deckKeywords
	}{
		{"unit_filter", deckUnitFilters}, {"attr_filter", deckAttrFilters},
	} {
		if value := extractDeckKeyword(&args, filter.groups); value != "" {
			additional[filter.key] = value
		}
	}
	for _, flag := range []deckKeywords{
		{"sub_max_profile", []string{"次顶配", "次满配", "中配"}},
		{"max_profile", []string{"顶配", "满配"}},
		{"use_current_deck", []string{"当前", "目前"}},
		{"keep_after_training_state", []string{"bfes不变", "bf不变"}},
		{"war_prepare", []string{"战备", "备战"}},
	} {
		if takeDeckKeyword(&args, flag.words...) {
			additional[flag.value] = true
		}
	}
	for _, field := range deckNumericOptions {
		if match := field.pattern.FindStringSubmatch(args); match != nil {
			n, ok := parseDeckPoints(match[1])
			if ok {
				additional[field.key] = n
				args = strings.Replace(args, match[0], " ", 1)
			}
		}
	}
	if match := deckRoundsPattern.FindStringSubmatch(args); match != nil {
		if n, err := strconv.ParseFloat(match[1], 64); err == nil {
			additional["rounds_per_hour"] = n
			args = strings.Replace(args, match[0], " ", 1)
		}
	}
	return additional, strings.TrimSpace(args)
}

func extractLiveType(args *string) string {
	switch {
	case takeDeckKeyword(args, "多人", "协力"):
		return "multi"
	case takeDeckKeyword(args, "单人"):
		return "solo"
	case takeDeckKeyword(args, "自动", "auto"):
		return "auto"
	default:
		return "multi"
	}
}

func (d *DeckModule) extractFixedCards(args string, parsed deckOptions) string {
	args, raw, found := strings.Cut(strings.ReplaceAll(args, "＃", "#"), "#")
	if !found {
		return args
	}
	tokens := strings.Fields(extractExcludedCards(raw, parsed))
	options := parsed.options
	if len(tokens) == 0 {
		return args
	}
	ids := make([]int, 0, 5)
	for _, token := range tokens {
		id, ok := parseIntToken(token)
		if !ok || id <= 0 {
			ids = nil
			break
		}
		ids = append(ids, id)
	}
	if len(ids) > 0 {
		options["fixed_cards"] = ids[:min(len(ids), 5)]
		return args
	}
	for _, token := range tokens {
		if id := d.chara.Resolve(token); id > 0 && !slices.Contains(ids, id) {
			ids = append(ids, id)
		}
	}
	if len(ids) > 0 {
		options["fixed_characters"] = ids[:min(len(ids), 5)]
		options["forced_leader_character_id"] = ids[0]
	}
	return args
}

func extractCardConfig(args *string, options map[string]any, noChange bool) {
	flags := map[string]bool{}
	for _, flag := range []deckKeywords{
		{"skill_max", []string{"满技能", "满技", "skillmax", "技能满级", "slv4"}},
		{"master_max", []string{"满突破", "满破", "rankmax", "mastermax", "5破", "五破"}},
		{"episode_read", []string{"剧情已读", "满剧情", "前后篇已读", "前后篇", "已读"}},
		{"canvas", []string{"满画布", "全画布", "画布", "满画板", "全画板", "画板"}},
		{"disable", []string{"禁用", "disable"}},
	} {
		flags[flag.value] = takeDeckKeyword(args, flag.words...)
	}
	for index, rarity := range []string{"1", "2", "3", "4", "birthday"} {
		lowRarity := !noChange && index < 2
		options["rarity_"+rarity+"_config"] = map[string]bool{
			"level_max":    !noChange,
			"skill_max":    flags["skill_max"] || lowRarity,
			"master_max":   flags["master_max"] || lowRarity,
			"episode_read": flags["episode_read"] || lowRarity,
			"disable":      flags["disable"],
			"canvas":       flags["canvas"],
		}
	}
}

func extractTarget(args *string) string {
	target := "score"
	if takeDeckKeyword(args, "综合力", "综合", "总合力", "总和", "power") {
		target = "power"
	}
	if takeDeckKeyword(args, "倍率", "实效", "skill", "时效") {
		target = "skill"
	}
	return target
}

var deckSkillStrategy = []deckKeywords{
	{"max", []string{"最高", "最大", "最优", "最强", "最佳"}},
	{"min", []string{"最低", "最小", "最差", "最弱", "最烂"}},
	{"average", []string{"平均", "均值", "期望"}},
}

var deckSkillPattern = regexp.MustCompile(`技能(?:顺序|排列|抽取|吸取)`)
var deckSkillValuePattern = regexp.MustCompile(`最高|最大|最优|最强|最佳|最低|最小|最差|最弱|最烂|平均|均值|期望`)

func extractSkillStrategy(args string, options map[string]any, fallback string) string {
	options["skill_order_choose_strategy"] = fallback
	options["skill_reference_choose_strategy"] = fallback
	for {
		loc := deckSkillPattern.FindStringIndex(args)
		if loc == nil {
			return strings.TrimSpace(args)
		}
		key := "skill_order_choose_strategy"
		if keyword := args[loc[0]:loc[1]]; keyword == "技能抽取" || keyword == "技能吸取" {
			key = "skill_reference_choose_strategy"
		}
		before, after := args[:loc[0]], args[loc[1]:]
		if values := deckSkillValuePattern.FindAllStringIndex(before, -1); len(values) > 0 && strings.TrimSpace(before[values[len(values)-1][1]:]) == "" {
			last := values[len(values)-1]
			value := before[last[0]:last[1]]
			options[key] = extractDeckKeyword(&value, deckSkillStrategy)
			before = before[:last[0]]
		} else {
			valueAt := deckSkillValuePattern.FindStringIndex(after)
			next := deckSkillPattern.FindStringIndex(after)
			if valueAt != nil && (next == nil || valueAt[0] < next[0]) {
				value := after[valueAt[0]:valueAt[1]]
				options[key] = extractDeckKeyword(&value, deckSkillStrategy)
				after = after[:valueAt[0]] + " " + after[valueAt[1]:]
			}
		}
		args = before + " " + after
	}
}

func extractEventID(args string, events []map[string]any, allowBareID bool) (int, string) {
	if match := deckEventPattern.FindStringSubmatch(args); match != nil {
		if id, ok := parseIntToken(match[1]); ok && id > 0 {
			return id, strings.Replace(args, match[0], " ", 1)
		}
	}
	if !allowBareID {
		return currentEventID(events, nowMSDefault()), args
	}
	tokens := strings.Fields(args)
	for i, token := range tokens {
		if len(token) > 3 || i > 0 && slices.Contains([]string{"wl", "-c", "c"}, tokens[i-1]) {
			continue
		}
		if id, ok := parseIntToken(token); ok && id > 0 && slices.ContainsFunc(events, func(row map[string]any) bool {
			return intField(row, "id") == id
		}) {
			return id, strings.Join(append(tokens[:i], tokens[i+1:]...), " ")
		}
	}
	return currentEventID(events, nowMSDefault()), args
}

func (d *DeckModule) extractWLChapter(args string, chapters []map[string]any) (map[string]any, string) {
	if len(chapters) == 0 {
		return nil, args
	}
	if match := deckWLPattern.FindStringSubmatch(args); match != nil {
		if n, ok := parseIntToken(match[1]); ok {
			for _, chapter := range chapters {
				if intField(chapter, "chapterNo") == n {
					return chapter, strings.Replace(args, match[0], " ", 1)
				}
			}
		}
	}
	findChapter := func(alias string) map[string]any {
		id := d.chara.Resolve(alias)
		for _, chapter := range chapters {
			if id > 0 && intField(chapter, "gameCharacterId") == id {
				return chapter
			}
		}
		return nil
	}
	tokens := strings.Fields(args)
	for i, token := range tokens {
		if i+1 < len(tokens) && (token == "-c" || token == "c" || token == "wl") {
			if chapter := findChapter(tokens[i+1]); chapter != nil {
				return chapter, strings.Join(append(tokens[:i], tokens[i+2:]...), " ")
			}
		}
		if alias, ok := strings.CutPrefix(token, "wl"); ok && alias != "" {
			if chapter := findChapter(alias); chapter != nil {
				return chapter, strings.Join(append(tokens[:i], tokens[i+1:]...), " ")
			}
		}
	}
	current := chapters[0]
	now := nowMSDefault()
	for _, chapter := range chapters[1:] {
		start := int64(intField(chapter, "chapterStartAt"))
		if start <= now && start >= int64(intField(current, "chapterStartAt")) {
			current = chapter
		}
	}
	if i := slices.Index(tokens, "wl"); i >= 0 {
		return current, strings.Join(append(tokens[:i], tokens[i+1:]...), " ")
	}
	for i, token := range tokens {
		if _, numeric := parseIntToken(token); !numeric {
			if chapter := findChapter(token); chapter != nil {
				return chapter, strings.Join(append(tokens[:i], tokens[i+1:]...), " ")
			}
		}
	}
	return current, args
}

func (d *DeckModule) applyDeckEvent(args string, server int, options map[string]any, allowBareID bool) string {
	events, _ := d.md.Load("events.json", server)
	id, args := extractEventID(args, events, allowBareID)
	if id <= 0 {
		return args
	}
	options["event_id"] = id
	worldBlooms, _ := d.md.Load("worldBlooms.json", server)
	var chapters []map[string]any
	for _, row := range worldBlooms {
		if intField(row, "eventId") == id {
			chapters = append(chapters, row)
		}
	}
	slices.SortStableFunc(chapters, func(a, b map[string]any) int {
		return cmp.Compare(intField(a, "chapterNo"), intField(b, "chapterNo"))
	})
	chapter, args := d.extractWLChapter(args, chapters)
	if chapter != nil {
		options["world_bloom_character_id"] = intField(chapter, "gameCharacterId")
		options["world_bloom_chapter_no"] = intField(chapter, "chapterNo")
	}
	return args
}

func extractDeckDifficulty(args string) (string, string) {
	tokens := strings.Fields(args)
	bestIndex, bestLength, diff := -1, 0, ""
	for i, token := range tokens {
		for _, group := range deckDifficulties {
			if len(token) > bestLength && slices.Contains(group.words, token) {
				bestIndex, bestLength, diff = i, len(token), group.value
			}
		}
	}
	if bestIndex < 0 {
		return "", args
	}
	return diff, strings.Join(append(tokens[:bestIndex], tokens[bestIndex+1:]...), " ")
}

func (d *DeckModule) extractMusic(ctx context.Context, args string, server int, parsed deckOptions) string {
	diff, args := extractDeckDifficulty(args)
	musicID := 0
	index, indexErr := d.songs.searchIndex(server)

	// 单独的 -N 优先作歌曲倒序号，与歌名或 ID 并用时作排除卡牌。
	if n, ok := parseIntToken(strings.TrimSpace(args)); ok && n < 0 && indexErr == nil && n >= -len(index.byID) {
		var published []map[string]any
		now := nowMSDefault()
		for _, song := range index.byID {
			if int64(intField(song, "publishedAt")) <= now {
				published = append(published, song)
			}
		}
		slices.SortFunc(published, func(a, b map[string]any) int {
			if order := cmp.Compare(intField(a, "publishedAt"), intField(b, "publishedAt")); order != 0 {
				return order
			}
			return cmp.Compare(intField(a, "id"), intField(b, "id"))
		})
		if n >= -len(published) {
			musicID = intField(published[len(published)+n], "id")
			args = ""
		}
	}

	args = extractExcludedCards(args, parsed)
	if args != "" {
		if loc := deckMusicIDPattern.FindStringIndex(args); loc != nil {
			if id, _ := parseIntToken(args[loc[0]:loc[1]]); id == deckOmakaseMusicID || indexErr == nil && index.byID[id] != nil {
				musicID = id
				args = strings.TrimSpace(args[:loc[0]] + " " + args[loc[1]:])
			}
		}
		if musicID == 0 && args != "" && indexErr == nil {
			if song := d.songs.findDeckSong(ctx, args, server); song.found {
				musicID, args = song.musicID, ""
			}
		}
	}
	if musicID == 0 {
		musicID = deckOmakaseMusicID
	}
	if musicID != deckOmakaseMusicID && diff != "" {
		if diffs, err := d.md.Load("musicDifficulties.json", server); err == nil && !slices.ContainsFunc(diffs, func(row map[string]any) bool {
			return intField(row, "musicId") == musicID && strField(row, "musicDifficulty") == diff
		}) {
			parsed.options["music_diff_missing"] = diff
		}
	}
	if diff == "" {
		diff = "master"
	}
	parsed.options["music_id"], parsed.options["music_diff"] = musicID, diff
	return strings.TrimSpace(args)
}

func extractExcludedCards(args string, parsed deckOptions) string {
	tokens := strings.Fields(args)
	excluded, _ := parsed.additional["excluded_cards"].([]int)
	configs, _ := parsed.options["single_card_configs"].([]map[string]any)
	remaining := tokens[:0]
	for _, token := range tokens {
		if n, ok := parseIntToken(token); ok && n < 0 && n > -5000 {
			excluded = append(excluded, -n)
			configs = append(configs, map[string]any{"card_id": -n, "disable": true})
		} else {
			remaining = append(remaining, token)
		}
	}
	if len(excluded) > 0 {
		parsed.additional["excluded_cards"] = excluded
		parsed.options["single_card_configs"] = configs
	}
	return strings.Join(remaining, " ")
}

func (d *DeckModule) buildEventOptions(ctx context.Context, args string, server int) deckOptions {
	return d.buildScoreOptions(ctx, args, server, "event", d.cfg.Timeout, d.cfg.ReturnNumMulti)
}

func (d *DeckModule) buildChallengeOptions(ctx context.Context, args string, server int) deckOptions {
	return d.buildScoreOptions(ctx, args, server, "challenge", d.cfg.Timeout, d.cfg.ReturnNumChallenge)
}

func (d *DeckModule) buildNoEventOptions(ctx context.Context, args string, server int) deckOptions {
	return d.buildScoreOptions(ctx, args, server, "no_event", d.cfg.TimeoutNoEvent, d.cfg.ReturnNumMulti)
}

func (d *DeckModule) buildScoreOptions(ctx context.Context, args string, server int, mode string, timeout, limit int) deckOptions {
	additional, args := extractAdditionalOptions(strings.ToLower(args))
	options := map[string]any{"algorithm": "all", "limit": limit}
	parsed := deckOptions{options: options, additional: additional}
	live := extractLiveType(&args)
	strategy := "average"
	if mode == "challenge" {
		strategy = "max"
		if live == "auto" {
			live = "challenge_auto"
		} else {
			live = "challenge"
		}
	}
	options["live_type"] = live
	args = d.extractFixedCards(args, parsed)
	extractCardConfig(&args, options, false)
	options["target"] = extractTarget(&args)
	args = extractSkillStrategy(args, options, strategy)
	if takeDeckKeyword(&args, "dfs") || options["fixed_characters"] != nil {
		options["algorithm"] = "dfs"
		timeout = d.cfg.TimeoutSingleAlgorithm
	}
	options["timeout_ms"] = timeout * 1000
	if mode == "event" {
		rest := args
		unit, attr := extractDeckKeyword(&rest, deckUnits), extractDeckKeyword(&rest, deckAttrs)
		if unit != "" && attr != "" && !deckEventPattern.MatchString(args) {
			options["event_unit"], options["event_attr"], args = unit, attr, rest
		} else {
			args = d.applyDeckEvent(args, server, options, true)
		}
	}
	if mode == "challenge" {
		args = d.extractChallengeCharacter(args, options)
	}
	parsed.lastArgs = d.extractMusic(ctx, args, server, parsed)
	if live == "multi" {
		options["multi_live_teammate_power"] = 250000
		options["multi_live_teammate_score_up"] = 200
	}
	applyAdditionalDeckOptions(options, additional)
	return parsed
}

func (d *DeckModule) extractChallengeCharacter(args string, options map[string]any) string {
	tokens := strings.Fields(args)
	for _, partial := range []bool{false, true} {
		for i, token := range tokens {
			if strings.ContainsAny(token, "0123456789") || slices.ContainsFunc(deckDifficulties, func(group deckKeywords) bool {
				return slices.Contains(group.words, token)
			}) {
				continue
			}
			id := d.chara.Resolve(token)
			if partial {
				id = d.chara.ResolvePartial(token)
			}
			if id > 0 {
				options["challenge_live_character_id"] = id
				return strings.Join(append(tokens[:i], tokens[i+1:]...), " ")
			}
		}
	}
	return args
}

func applyAdditionalDeckOptions(options, additional map[string]any) {
	for _, key := range []string{"unit_filter", "attr_filter", "keep_after_training_state", "multi_live_score_up_lower_bound", "boost"} {
		if value := additional[key]; value != nil {
			options[key] = value
		}
	}
	for _, item := range []struct{ from, to string }{
		{"teammate_power", "multi_live_teammate_power"},
		{"teammate_scoreup", "multi_live_teammate_score_up"},
	} {
		if value := additional[item.from]; value != nil {
			options[item.to] = value
		}
	}
}

func (d *DeckModule) buildBonusOptions(_ context.Context, args string, server int) deckOptions {
	additional, args := extractAdditionalOptions(strings.ToLower(args))
	options := map[string]any{
		"algorithm": "dfs", "timeout_ms": d.cfg.TimeoutBonus * 1000,
		"target": "bonus", "live_type": "solo", "music_id": deckOmakaseMusicID,
		"music_diff": "master", "limit": d.cfg.ReturnNumBonus,
	}
	parsed := deckOptions{options: options, additional: additional}
	args = d.extractFixedCards(args, parsed)
	extractCardConfig(&args, options, true)
	args = extractExcludedCards(d.applyDeckEvent(args, server, options, false), parsed)
	applyAdditionalDeckOptions(options, additional)
	var targets []int
	for _, token := range strings.Fields(args) {
		n, ok := parseIntToken(token)
		if !ok {
			parsed.lastArgs = args
			return parsed
		}
		targets = append(targets, n)
	}
	if len(targets) > 0 {
		options["target_bonus_list"] = targets
	}
	return parsed
}
