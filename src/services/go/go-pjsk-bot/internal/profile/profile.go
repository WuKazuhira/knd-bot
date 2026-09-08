// Package profile 拉取并解析 PJSK 玩家档案（Suite API），计算收歌进度，
// 对齐 old-python _models.UserProfile.getsuite。供 rop/b30/profile 等模块复用。
package profile

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/kazuhira/go-pjsk-bot/internal/gameapi"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/serverconfig"
)

// suiteAPIKeys 对齐 old-python SUITE_API_KEYS。
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

// ScoreEntry 是某等级的收歌统计 [AP, FC, clear, total]。
type ScoreEntry [4]int

// Profile 是解析后的玩家档案（getsuite 版本）。
type Profile struct {
	Name        string
	Rank        int
	UserID      string
	MasterScore map[int]*ScoreEntry // level(26..37) -> 统计
	ExpertScore map[int]*ScoreEntry // level(21..31) -> 统计
	UserDecks   []int64
	Honors      any // userProfileHonors 原样透传给绘图服务
	Missions    any // userHonorMissions
	UploadTime  any
	rawData     map[string]any
}

// Fetcher 组合 profile 所需的依赖。
type Fetcher struct {
	api    *gameapi.Client
	md     *masterdata.Loader
	server *serverconfig.Config
}

// NewFetcher 创建档案拉取器。
func NewFetcher(api *gameapi.Client, md *masterdata.Loader, server *serverconfig.Config) *Fetcher {
	return &Fetcher{api: api, md: md, server: server}
}

// GetSuite 拉取并解析用户 suite 数据，计算收歌进度。
func (f *Fetcher) GetSuite(ctx context.Context, uid string, serverType int) (*Profile, error) {
	suiteURL := f.server.SuiteURL(serverType, uid)
	if suiteURL == "" {
		return nil, fmt.Errorf("服务器不支持 suite 查询")
	}
	url := suiteURL + "?mode=latest&key=" + strings.Join(suiteAPIKeys, ",")
	raw, err := f.api.Get(ctx, url)
	if err != nil {
		return nil, err
	}
	var data map[string]any
	if err := json.Unmarshal(raw, &data); err != nil {
		return nil, fmt.Errorf("解析 suite 数据失败: %w", err)
	}

	p := &Profile{
		UserID:      uid,
		MasterScore: map[int]*ScoreEntry{},
		ExpertScore: map[int]*ScoreEntry{},
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

	// 卡组（member1..5）
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

// computeScores 统计 master/expert 收歌进度，对齐 Python 算法。
func (f *Fetcher) computeScores(data, suite map[string]any, serverType int, p *Profile) error {
	diffs, err := f.md.Load("musicDifficulties.json", serverType)
	if err != nil {
		return fmt.Errorf("加载 musicDifficulties: %w", err)
	}

	// 每等级总数（total 在下标 3）
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

	// 每首歌每难度取最好成绩
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

	// 建 (musicId,difficulty)->level 索引
	levelOf := map[key]int{}
	for _, d := range diffs {
		levelOf[key{int(mdInt(d, "musicId")), mdStr(d, "musicDifficulty")}] = int(mdInt(d, "playLevel"))
	}

	for k, rank := range best {
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
		case rank >= 3: // AP
			entry[0]++
			entry[1]++
			entry[2]++
		case rank >= 2: // FC
			entry[1]++
			entry[2]++
		case rank >= 1: // clear
			entry[2]++
		}
	}
	return nil
}

// resultRank 把 playResult 文本映射为等级，对齐 Python。
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

// HeaderPayload 构造绘图服务 Header 载荷，对齐 build_header_payload。
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

// ScoreMap 把某难度收歌统计转成绘图服务可接收的 {level: [4]int}。
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

// MusicResults 返回原始 userMusicResults（根层优先，兼容 gamedata 层），供 b30 使用。
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
