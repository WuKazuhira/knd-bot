package pjsk

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/cards"
	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/profile"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/settings"
)

type DeckModule struct {
	md       *masterdata.Loader
	draw     *draw.Client
	fetcher  *profile.Fetcher
	resolver *UserResolver
	songs    *SongModule
	chara    *cards.CharaAliasResolver
	dataDir  string
	supers   []int64
	cfg      settings.Deck
	http     *deckHTTP
}

func NewDeckModule(md *masterdata.Loader, drawClient *draw.Client, fetcher *profile.Fetcher, resolver *UserResolver, songs *SongModule, chara *cards.CharaAliasResolver, dataDir string, supers []int64, cfg settings.Deck) *DeckModule {
	return &DeckModule{
		md: md, draw: drawClient, fetcher: fetcher, resolver: resolver, songs: songs, chara: chara,
		dataDir: dataDir, supers: supers, cfg: cfg, http: newDeckHTTP(cfg),
	}
}

func (d *DeckModule) Register(r *router.Router) {
	r.Register("活动组卡", []string{"组卡", "活动卡组", "活动组队", "活动配队", "配队", "组队", "模拟组卡", "pjsk deck", "pjsk event deck"}, d.handle(d.buildEventOptions))
	r.Register("挑战组卡", []string{"挑战卡组", "挑战组队", "挑战配队", "pjsk challenge deck"}, d.handle(d.buildChallengeOptions))
	r.Register("长草组卡", []string{"最强卡组", "最强组卡", "长草卡组", "长草组队", "pjsk best deck", "pjsk no event deck"}, d.handle(d.buildNoEventOptions))
	r.Register("加成组卡", []string{"控分组卡", "加成卡组", "控分卡组", "pjsk bonus deck"}, d.handle(d.buildBonusOptions))
	r.Register("组卡后端", []string{"组卡后端切换", "切换组卡后端", "deck backend"}, d.handleBackend)
}

func (d *DeckModule) handleBackend(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if !slices.Contains(d.supers, req.Event.UserID) {
		return nil
	}
	if req.Arg != "" {
		return onebot.ReplyText(req.Event, "Go 版固定使用 Allium HTTP 组卡引擎。", false)
	}
	lines := []string{"组卡后端：Allium HTTP（" + d.cfg.ServiceAPI + "）"}
	if len(d.http.urls) == 0 {
		lines = append(lines, "未配置地址")
	}
	for _, url := range d.http.urls {
		lines = append(lines, url+" "+d.http.probe(ctx, url))
	}
	return onebot.ReplyText(req.Event, strings.Join(lines, "\n"), false)
}

func (d *DeckModule) handle(build func(context.Context, string, int) deckOptions) router.Handler {
	return func(ctx context.Context, req router.Request) *onebot.ActionRequest {
		reply := func(message string) *onebot.ActionRequest {
			return onebot.ReplyText(req.Event, message, true)
		}
		server := int(req.Server)
		parsed := build(ctx, req.Arg, server)
		mode := deckMode(parsed.options)
		current := parsed.additional["use_current_deck"] == true
		if current && mode == "challenge_all" {
			return reply("需要指定挑战角色才能使用「当前」，例如：挑战组卡 miku 当前")
		}
		if len(d.http.urls) == 0 {
			return reply("未配置可用的 Allium HTTP 组卡服务")
		}
		suite, header, isPrivate, err := d.userData(ctx, req, parsed.additional)
		if err != nil {
			return reply(err.Error())
		}
		if current {
			members := currentDeckMembers(suite)
			if len(members) != 5 {
				return reply("无法获取当前卡组，请先更新抓包数据")
			}
			parsed.options["fixed_cards"] = members
			delete(parsed.options, "fixed_characters")
			delete(parsed.options, "forced_leader_character_id")
			parsed.options["algorithm"] = "dfs"
		}
		user, err := json.Marshal(suite)
		if err != nil {
			return reply("读取组卡数据失败：" + err.Error())
		}
		region := serverDirName(server)
		started := time.Now()
		decks, err := d.recommend(ctx, region, parsed.options, user, mode == "challenge_all")
		if err != nil {
			return reply("组卡失败：" + err.Error())
		}
		elapsed := time.Since(started).Seconds()
		decks = sortDeckResults(decks, mode == "challenge_all")
		if limit := intField(parsed.options, "limit"); mode != "challenge_all" && limit > 0 && len(decks) > limit {
			decks = decks[:limit]
		}
		algorithms := make([]string, len(decks))
		for i := range algorithms {
			algorithms[i] = "http"
		}
		img, err := d.draw.Render(ctx, "deck", map[string]any{
			"profile_data": header, "is_private": isPrivate,
			"result_decks": decks, "result_algs": algorithms,
			"cost_times": map[string]float64{"http": elapsed}, "wait_times": map[string]float64{"http": 0},
			"recommend_type": mode, "options": parsed.options, "additional": parsed.additional, "pjsk_type": server,
		})
		if err != nil {
			log.Printf("[deck] 绘图失败: %v", err)
			return reply("组卡图片生成失败")
		}
		return onebot.ReplyImage(req.Event, base64Encode(img))
	}
}

func deckMode(options map[string]any) string {
	switch {
	case options["target"] == "bonus":
		if intField(options, "world_bloom_character_id") > 0 {
			return "wl_bonus"
		}
		return "bonus"
	case options["live_type"] == "challenge" || options["live_type"] == "challenge_auto":
		if options["challenge_live_character_id"] != nil {
			return "challenge"
		}
		return "challenge_all"
	case intField(options, "event_id") > 0:
		if intField(options, "world_bloom_character_id") > 0 {
			return "wl"
		}
		return "event"
	case options["event_unit"] != nil:
		return "unit_attr"
	default:
		return "no_event"
	}
}

func (d *DeckModule) recommend(ctx context.Context, region string, options map[string]any, user []byte, all bool) ([]map[string]any, error) {
	if !all {
		return d.http.recommend(ctx, region, options, user, false)
	}
	var firstErr error
	if d.http.api != "legacy" {
		decks, err := d.http.recommend(ctx, region, options, user, true)
		if err == nil {
			return decks, nil
		}
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		log.Printf("[deck] challenge-all 请求失败，改为逐角色组卡: %v", err)
	}
	var decks []map[string]any
	params := maps.Clone(options)
	params["limit"] = 1
	for cid := 1; cid <= 26; cid++ {
		params["challenge_live_character_id"] = cid
		result, err := d.http.recommend(ctx, region, params, user, false)
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		if err != nil {
			if firstErr == nil {
				firstErr = err
			}
			continue
		}
		decks = append(decks, sortDeckResults(result, false)[0])
	}
	if len(decks) == 0 {
		return nil, firstErr
	}
	decks = sortDeckResults(decks, false)
	for i, deck := range decks {
		deck["rank"] = i + 1
	}
	return decks, nil
}

func sortDeckResults(decks []map[string]any, preferRank bool) []map[string]any {
	slices.SortStableFunc(decks, func(a, b map[string]any) int {
		if preferRank && intField(a, "rank") != intField(b, "rank") {
			return cmp.Compare(intField(a, "rank"), intField(b, "rank"))
		}
		for _, key := range []string{"target_value", "score", "total_power"} {
			if diff := cmp.Compare(intField(b, key), intField(a, key)); diff != 0 {
				return diff
			}
		}
		return 0
	})
	seen := make(map[string]bool, len(decks))
	results := decks[:0]
	for _, deck := range decks {
		cards, _ := deck["cards"].([]map[string]any)
		ids := make([]int, len(cards))
		for i, card := range cards {
			ids[i] = intField(card, "card_id")
		}
		key := fmt.Sprint(deck["target_value"], "/", deck["total_power"], "/", ids)
		if !seen[key] {
			seen[key] = true
			results = append(results, deck)
		}
	}
	return results
}

func (d *DeckModule) userData(ctx context.Context, req router.Request, additional map[string]any) (map[string]any, map[string]any, bool, error) {
	server := int(req.Server)
	header := map[string]any{
		"name": "", "rank": 0, "userid": nil, "update_time": time.Now().Unix(),
		"userDecks": []int{}, "special_training": []bool{}, "userProfileHonors": []any{},
		"userHonorMissions": []any{}, "suite_update_time": nil,
	}
	if additional["max_profile"] == true || additional["sub_max_profile"] == true {
		cap := 20
		if additional["max_profile"] != true {
			cap = 15
		}
		suite, err := d.constructMaxProfile(server, cap)
		return suite, header, false, err
	}

	req.Arg = ""
	req.Event.Message = nil
	uid, private, resolveErr := d.resolver.Resolve(ctx, req)
	if resolveErr != "" {
		return nil, nil, false, errors.New(resolveErr)
	}
	suite, err := d.loadUserSuite(ctx, uid, server, additional["use_current_deck"] == true)
	if err != nil {
		return nil, nil, false, err
	}
	now := time.Now().Unix()
	members := currentDeckMembers(suite)
	header["name"], header["rank"], header["userid"] = strField(suite, "name"), intField(suite, "rank"), uid
	header["userDecks"] = members
	trained := make([]bool, len(members))
	for _, card := range sliceOfMap(suite["userCards"]) {
		if i := slices.Index(members, intField(card, "cardId")); i >= 0 {
			trained[i] = card["defaultImage"] == "special_training"
		}
	}
	header["special_training"] = trained
	honors := sliceOfMap(suite["userProfileHonors"])
	missions := sliceOfMap(suite["userHonorMissions"])
	if len(honors) == 0 && len(missions) == 0 && d.fetcher != nil {
		if p, err := d.fetcher.GetProfile(ctx, uid, server); err == nil {
			honors, missions = sliceOfMap(p.Honors), sliceOfMap(p.Missions)
			if header["name"] == "" {
				header["name"] = p.Name
			}
			if header["rank"] == 0 {
				header["rank"] = p.Rank
			}
		}
	}
	if honors == nil {
		honors = []map[string]any{}
	}
	if missions == nil {
		missions = []map[string]any{}
	}
	header["userProfileHonors"], header["userHonorMissions"] = honors, missions
	stamp := int64(deckNumber(deckValue(suite, "upload_time", "updatedAt")))
	if stamp > 1e10 {
		stamp /= 1000
	}
	if stamp <= 0 {
		stamp = now
	}
	header["suite_update_time"] = stamp

	if level := additional["area_item_level"]; level != nil {
		levels, err := d.areaItemLevels(server, int(deckNumber(level)))
		if err != nil {
			return nil, nil, false, err
		}
		raiseDeckAreaLevels(suite, levels)
	}
	return suite, header, private, nil
}

func (d *DeckModule) loadUserSuite(ctx context.Context, uid string, server int, requireDeck bool) (map[string]any, error) {
	var fetchErr error
	var hasCards bool
	usable := func(suite map[string]any) bool {
		hasUserCards := len(sliceOfMap(suite["userCards"])) > 0
		hasCards = hasCards || hasUserCards
		return hasUserCards && (!requireDeck || len(currentDeckMembers(suite)) == 5)
	}
	if d.fetcher != nil {
		data, err := d.fetcher.GetDeckSuite(ctx, uid, server)
		fetchErr = err
		if err == nil {
			if suite := normalizeDeckSuite(data); usable(suite) {
				return suite, nil
			}
		}
	}
	region := serverDirName(server)
	for _, dir := range []string{filepath.Join(d.dataDir, "ondemand", "suite", region), filepath.Join(d.dataDir, "ondemand", region)} {
		file, err := os.Open(filepath.Join(dir, uid+".json"))
		if err != nil {
			continue
		}
		decoder := json.NewDecoder(file)
		decoder.UseNumber()
		var data map[string]any
		err = decoder.Decode(&data)
		file.Close()
		if err == nil {
			if suite := normalizeDeckSuite(data); usable(suite) {
				return suite, nil
			}
		}
	}
	if requireDeck && hasCards {
		return nil, errors.New("无法获取当前卡组，请先更新抓包数据")
	}
	if fetchErr != nil {
		return nil, errors.New(apiErrText(fetchErr))
	}
	return nil, errors.New("未找到用户数据，请先更新抓包数据")
}

func normalizeDeckSuite(data map[string]any) map[string]any {
	out := make(map[string]any, len(data))
	maps.Copy(out, data)
	user, _ := data["user"].(map[string]any)
	for _, source := range []any{data["userGamedata"], user["userGamedata"], user} {
		row, _ := source.(map[string]any)
		for key, value := range row {
			switch current := out[key].(type) {
			case nil:
				out[key] = value
			case []any:
				if len(current) == 0 {
					out[key] = value
				}
			case map[string]any:
				if len(current) == 0 {
					out[key] = value
				}
			}
		}
	}
	if deck, ok := out["userDeck"].(map[string]any); ok && len(sliceOfMap(out["userDecks"])) == 0 {
		out["userDecks"] = []any{deck}
	}
	for _, key := range []string{"userDecks", "userCards", "userAreas"} {
		if _, ok := out[key].([]any); !ok {
			out[key] = []any{}
		}
	}
	return out
}

func currentDeckMembers(suite map[string]any) []int {
	decks := sliceOfMap(suite["userDecks"])
	gamedata, _ := suite["userGamedata"].(map[string]any)
	selected := intField(gamedata, "deck")
	if selected == 0 {
		selected = intField(suite, "deck")
	}
	if len(decks) == 0 {
		return nil
	}
	deck := decks[0]
	for _, candidate := range decks {
		if selected > 0 && intField(candidate, "deckId") == selected {
			deck = candidate
			break
		}
	}
	members := make([]int, 0, 5)
	for _, key := range []string{"member1", "member2", "member3", "member4", "member5"} {
		id := intField(deck, key)
		if id <= 0 {
			return nil
		}
		members = append(members, id)
	}
	return members
}

func (d *DeckModule) constructMaxProfile(server, maxAreaLevel int) (map[string]any, error) {
	cardRows, err := d.md.Load("cards.json", server)
	if err != nil {
		return nil, err
	}
	episodeRows, err := d.md.Load("cardEpisodes.json", server)
	if err != nil {
		return nil, err
	}
	episodes := make(map[int][]map[string]any)
	for _, row := range episodeRows {
		cardID, episodeID := intField(row, "cardId"), intField(row, "id")
		if cardID > 0 && episodeID > 0 {
			episodes[cardID] = append(episodes[cardID], map[string]any{"cardEpisodeId": episodeID, "scenarioStatus": "already_read"})
		}
	}
	rarityLevels := map[string]int{"rarity_1": 20, "rarity_2": 30, "rarity_3": 50, "rarity_4": 60, "rarity_birthday": 60}
	now := nowMSDefault()
	userCards := make([]map[string]any, 0, len(cardRows))
	for _, card := range cardRows {
		id := intField(card, "id")
		if id <= 0 || int64(intField(card, "releaseAt")) > now {
			continue
		}
		status, image := "none", "original"
		if costs, ok := card["specialTrainingCosts"].([]any); ok && len(costs) > 0 {
			status, image = "done", "special_training"
		}
		cardEpisodes := episodes[id]
		if cardEpisodes == nil {
			cardEpisodes = []map[string]any{}
		}
		userCards = append(userCards, map[string]any{
			"cardId": id, "level": max(20, rarityLevels[strField(card, "cardRarityType")]),
			"skillLevel": 4, "masterRank": 5, "specialTrainingStatus": status,
			"defaultImage": image, "episodes": cardEpisodes,
		})
	}
	characters := make([]map[string]int, 0, 26)
	for cid := 1; cid <= 26; cid++ {
		characters = append(characters, map[string]int{"characterId": cid, "characterRank": 120})
	}
	levels, err := d.areaItemLevels(server, maxAreaLevel)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"userGamedata": map[string]any{}, "userDecks": []any{}, "userCards": userCards, "userHonors": []any{},
		"userCharacters": characters, "userAreas": []any{deckArea(levels)},
	}, nil
}

func (d *DeckModule) areaItemLevels(server, limit int) (map[int]int, error) {
	rows, err := d.md.Load("areaItemLevels.json", server)
	if err != nil {
		return nil, err
	}
	levels := make(map[int]int)
	for _, row := range rows {
		id, level := intField(row, "areaItemId"), intField(row, "level")
		if id > 0 && level <= limit && level > levels[id] {
			levels[id] = level
		}
	}
	return levels, nil
}

func deckArea(levels map[int]int) map[string]any {
	items := make([]map[string]int, 0, len(levels))
	for _, id := range slices.Sorted(maps.Keys(levels)) {
		items = append(items, map[string]int{"areaItemId": id, "level": levels[id]})
	}
	return map[string]any{"userAreaStatus": map[string]any{}, "areaItems": items}
}

func raiseDeckAreaLevels(suite map[string]any, levels map[int]int) {
	for _, area := range sliceOfMap(suite["userAreas"]) {
		for _, item := range sliceOfMap(area["areaItems"]) {
			id := intField(item, "areaItemId")
			if level, exists := levels[id]; exists {
				item["level"] = max(level, intField(item, "level"))
				delete(levels, id)
			}
		}
	}
	if len(levels) > 0 {
		areas, _ := suite["userAreas"].([]any)
		suite["userAreas"] = append(areas, deckArea(levels))
	}
}
