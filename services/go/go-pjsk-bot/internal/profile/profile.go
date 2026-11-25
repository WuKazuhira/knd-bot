package profile

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/kazuhira/go-pjsk-bot/internal/gameapi"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/serverconfig"
)

var suiteAPIKeys = []string{
	"userCards", "userDecks", "userGamedata", "userMusics", "userMusicResults",
	"userMysekaiMaterials", "userAreas", "userChallengeLiveSoloDecks", "userCharacters",
	"userMysekaiCanvases", "userMysekaiFixtureGameCharacterPerformanceBonuses",
	"userMysekaiGates", "userWorldBloomSupportDecks", "userHonors",
	"userMysekaiCharacterTalks", "userChallengeLiveSoloResults", "userChallengeLiveSoloStages",
	"userChallengeLiveSoloHighScoreRewards", "userEvents", "userWorldBlooms",
	"userMusicAchievements", "userPlayerFrames", "userMaterials", "upload_time",
	"userCharacterMissionV2s", "userCharacterMissionV2Statuses", "userBonds", "userProfileHonors",
}

// [AP, FC, clear, total]
type ScoreEntry [4]int

type Profile struct {
	Name        string
	Rank        int
	UserID      string
	MasterScore map[int]*ScoreEntry
	ExpertScore map[int]*ScoreEntry
	// musicId -> [6]int，按 easy..append 存最好成绩（0无 1clear 2FC 3AP）
	MusicResult     map[int]*[6]int
	FullPerfect     [6]int
	FullCombo       [6]int
	Clear           [6]int
	UserDecks       []int64
	Honors          any
	Missions        any
	UploadTime      any
	Word            string
	TwitterID       string
	CharacterID     int
	CharacterRank   any
	SpecialTraining []bool
	DeckMasterRanks []int
	HighScore       int
	MvpCount        int
	SuperStarCount  int
	IsNewData       bool
	rawData         map[string]any
}

type Fetcher struct {
	api    *gameapi.Client
	md     *masterdata.Loader
	server *serverconfig.Config
}

func NewFetcher(api *gameapi.Client, md *masterdata.Loader, server *serverconfig.Config) *Fetcher {
	return &Fetcher{api: api, md: md, server: server}
}

func (f *Fetcher) GetSuite(ctx context.Context, uid string, serverType int) (*Profile, error) {
	data, err := f.fetchSuite(ctx, uid, serverType, suiteAPIKeys, false)
	if err != nil {
		return nil, err
	}

	p := &Profile{
		UserID:      uid,
		MasterScore: map[int]*ScoreEntry{},
		ExpertScore: map[int]*ScoreEntry{},
		MusicResult: map[int]*[6]int{},
		rawData:     data,
	}
	for i := 26; i <= 37; i++ {
		p.MasterScore[i] = &ScoreEntry{}
	}
	for i := 21; i <= 31; i++ {
		p.ExpertScore[i] = &ScoreEntry{}
	}

	gamedata, _ := data["userGamedata"].(map[string]any)
	suite := gamedata
	if suite == nil {
		suite = data
	}
	p.Name = firstStr(strGet(data, "name"), strGet(suite, "name"))
	p.Rank = firstInt(intGet(data, "rank"), intGet(suite, "rank"))
	p.Honors = firstNonNil(data["userProfileHonors"], suite["userProfileHonors"])
	p.Missions = firstNonNil(data["userHonorMissions"], suite["userHonorMissions"])
	p.UploadTime = firstNonNil(data["upload_time"], data["updatedAt"], suite["upload_time"])

	clearCount := sliceOf(firstNonNil(data["userMusicDifficultyClearCount"], suite["userMusicDifficultyClearCount"]))
	for i := 0; i < len(clearCount) && i < 6; i++ {
		if cm, ok := clearCount[i].(map[string]any); ok {
			p.FullPerfect[i] = intGet(cm, "allPerfect")
			p.FullCombo[i] = intGet(cm, "fullCombo")
			p.Clear[i] = intGet(cm, "liveClear")
		}
	}

	decknum := firstInt(intGet(gamedata, "deck"), intGet(suite, "deck"), 1)
	userDecks := sliceOf(firstNonNil(data["userDecks"], suite["userDecks"]))
	p.UserDecks = make([]int64, 5)
	for _, d := range userDecks {
		dm, ok := d.(map[string]any)
		if !ok || intGet(dm, "deckId") != decknum {
			continue
		}
		for i := 0; i < 5; i++ {
			p.UserDecks[i] = int64(intGet(dm, fmt.Sprintf("member%d", i+1)))
		}
		break
	}

	if err := f.computeScores(data, suite, serverType, p); err != nil {
		return nil, err
	}
	return p, nil
}

func (f *Fetcher) GetDeckSuite(ctx context.Context, uid string, serverType int) (map[string]any, error) {
	return f.fetchSuite(ctx, uid, serverType, []string{
		"userGamedata", "userDecks", "userCards", "userAreas", "userCharacters", "userHonors",
		"userChallengeLiveSoloDecks", "userWorldBloomSupportDecks", "userMysekaiCanvases",
		"userMysekaiFixtureGameCharacterPerformanceBonuses", "userMysekaiGates", "userProfileHonors", "userHonorMissions", "upload_time",
	}, true)
}

func (f *Fetcher) fetchSuite(ctx context.Context, uid string, serverType int, keys []string, preciseNumbers bool) (map[string]any, error) {
	suiteURL := f.server.SuiteURL(serverType, uid)
	if suiteURL == "" {
		return nil, fmt.Errorf("服务器不支持 suite 查询")
	}
	raw, err := f.api.Get(ctx, suiteURL+"?mode=latest&key="+strings.Join(keys, ","))
	if err != nil {
		return nil, err
	}
	var data map[string]any
	if preciseNumbers {
		decoder := json.NewDecoder(bytes.NewReader(raw))
		decoder.UseNumber()
		err = decoder.Decode(&data)
	} else {
		err = json.Unmarshal(raw, &data)
	}
	if err != nil {
		return nil, fmt.Errorf("解析 suite 数据失败: %w", err)
	}
	return data, nil
}

func (f *Fetcher) computeScores(data, suite map[string]any, serverType int, p *Profile) error {
	diffs, err := f.md.Load("musicDifficulties.json", serverType)
	if err != nil {
		return fmt.Errorf("加载 musicDifficulties: %w", err)
	}

	for _, d := range diffs {
		level := int(mdInt(d, "playLevel"))
		switch mdStr(d, "musicDifficulty") {
		case "master":
			if e := p.MasterScore[level]; e != nil {
				e[3]++
			}
		case "expert":
			if e := p.ExpertScore[level]; e != nil {
				e[3]++
			}
		}
	}

	type key struct {
		musicID int
		diff    string
	}
	best := map[key]int{}
	results := sliceOf(firstNonNil(data["userMusicResults"], suite["userMusicResults"]))
	for _, r := range results {
		rm, ok := r.(map[string]any)
		if !ok {
			continue
		}
		musicID := int(mdInt(rm, "musicId"))
		diffType := strings.ToLower(firstStr(mdStr(rm, "musicDifficultyType"), mdStr(rm, "musicDifficulty")))
		pr := strings.ToLower(strings.NewReplacer("-", "_", " ", "_").Replace(mdStr(rm, "playResult")))
		rank := resultRank(pr)
		k := key{musicID, diffType}
		if cur, ok := best[k]; !ok || rank > cur {
			best[k] = rank
		}
	}

	levelOf := map[key]int{}
	for _, d := range diffs {
		levelOf[key{int(mdInt(d, "musicId")), mdStr(d, "musicDifficulty")}] = int(mdInt(d, "playLevel"))
	}

	for k, rank := range best {
		if p.MusicResult == nil {
			p.MusicResult = map[int]*[6]int{}
		}
		if idx, ok := difficultyIndex[k.diff]; ok {
			mr := p.MusicResult[k.musicID]
			if mr == nil {
				mr = &[6]int{}
				p.MusicResult[k.musicID] = mr
			}
			mr[idx] = rank
		}
		level, ok := levelOf[k]
		if !ok {
			continue
		}
		var entry *ScoreEntry
		if k.diff == "master" {
			entry = p.MasterScore[level]
		} else if k.diff == "expert" {
			entry = p.ExpertScore[level]
		}
		if entry == nil {
			continue
		}
		switch {
		case rank >= 3:
			entry[0]++
			entry[1]++
			entry[2]++
		case rank >= 2:
			entry[1]++
			entry[2]++
		case rank >= 1:
			entry[2]++
		}
	}
	return nil
}

func resultRank(pr string) int {
	switch pr {
	case "full_perfect", "fullperfect", "all_perfect", "allperfect":
		return 3
	case "full_combo", "fullcombo":
		return 2
	case "clear", "live_clear", "liveclear":
		return 1
	}
	return 0
}

func (p *Profile) HeaderPayload(isPrivate bool) map[string]any {
	name := p.Name
	if name == "" {
		name = "???"
	}
	return map[string]any{
		"userid":              p.UserID,
		"name":                name,
		"rank":                p.Rank,
		"is_private":          isPrivate,
		"user_decks":          p.UserDecks,
		"user_profile_honors": p.Honors,
		"user_honor_missions": p.Missions,
		"suite_update_time":   p.UploadTime,
	}
}

func (p *Profile) ScoreMap(diff string) map[string][]int {
	src := p.MasterScore
	if diff == "expert" {
		src = p.ExpertScore
	}
	out := make(map[string][]int, len(src))
	for level, e := range src {
		out[fmt.Sprintf("%d", level)] = []int{e[0], e[1], e[2], e[3]}
	}
	return out
}

func (p *Profile) MusicResults() []any {
	if p.rawData == nil {
		return nil
	}
	if r := sliceOf(p.rawData["userMusicResults"]); r != nil {
		return r
	}
	if gd, ok := p.rawData["userGamedata"].(map[string]any); ok {
		return sliceOf(gd["userMusicResults"])
	}
	return nil
}
