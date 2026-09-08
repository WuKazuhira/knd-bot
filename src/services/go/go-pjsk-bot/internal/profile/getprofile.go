package profile

import (
	"context"
	"encoding/json"
	"fmt"
)

// GetProfile 拉取并解析用户档案（Profile API），对齐 old-python UserProfile.getprofile。
// 与 GetSuite 不同，它不计算 masterscore，只解析档案展示字段（个人信息出图用）。
func (f *Fetcher) GetProfile(ctx context.Context, uid string, serverType int) (*Profile, error) {
	profileURL := f.server.ProfileURL(serverType, uid)
	if profileURL == "" {
		return nil, fmt.Errorf("服务器不支持 profile 查询")
	}
	raw, err := f.api.Get(ctx, profileURL)
	if err != nil {
		return nil, err
	}
	var data map[string]any
	if err := json.Unmarshal(raw, &data); err != nil {
		return nil, fmt.Errorf("解析 profile 数据失败: %w", err)
	}

	p := &Profile{
		UserID:      uid,
		MasterScore: map[int]*ScoreEntry{},
		ExpertScore: map[int]*ScoreEntry{},
		rawData:     data,
	}

	// 新旧数据格式判定：新数据含 totalPower。
	_, p.IsNewData = data["totalPower"]

	userProfile, _ := data["userProfile"].(map[string]any)
	p.TwitterID = strGet(userProfile, "twitterId")
	p.Word = strGet(userProfile, "word")

	userBlock, _ := data["user"].(map[string]any)
	gamedata, _ := userBlock["userGamedata"].(map[string]any)

	// 挑战最高分
	if p.IsNewData {
		if r, ok := data["userChallengeLiveSoloResult"].(map[string]any); ok {
			p.CharacterID = intGet(r, "characterId")
			p.HighScore = intGet(r, "highScore")
		}
	} else {
		for _, item := range sliceOf(data["userChallengeLiveSoloResults"]) {
			r, ok := item.(map[string]any)
			if !ok {
				continue
			}
			if hs := intGet(r, "highScore"); hs > p.HighScore {
				p.HighScore = hs
				p.CharacterID = intGet(r, "characterId")
			}
		}
	}

	p.CharacterRank = firstNonNil(data["userCharacters"], userBlock["userCharacters"], gamedata["userCharacters"])
	p.Honors = firstNonNil(data["userProfileHonors"], userBlock["userProfileHonors"], gamedata["userProfileHonors"])
	p.Missions = firstNonNil(data["userHonorMissions"], userBlock["userHonorMissions"], gamedata["userHonorMissions"])

	if p.IsNewData {
		p.Name = firstStr(strGet(userBlock, "name"), strGet(gamedata, "name"))
		p.Rank = firstInt(intGet(userBlock, "rank"), intGet(gamedata, "rank"))
		clearCount := sliceOf(data["userMusicDifficultyClearCount"])
		for i := 0; i < len(clearCount) && i < 6; i++ {
			if cm, ok := clearCount[i].(map[string]any); ok {
				p.FullPerfect[i] = intGet(cm, "allPerfect")
				p.FullCombo[i] = intGet(cm, "fullCombo")
				p.Clear[i] = intGet(cm, "liveClear")
			}
		}
		if topScore, ok := data["userMultiLiveTopScoreCount"].(map[string]any); ok {
			p.MvpCount = intGet(topScore, "mvp")
			p.SuperStarCount = intGet(topScore, "superStar")
		}
	} else {
		p.Name = firstStr(strGet(gamedata, "name"))
		p.Rank = firstInt(intGet(gamedata, "rank"))
	}

	// 卡组与练度
	p.UserDecks = make([]int64, 5)
	p.SpecialTraining = make([]bool, 5)
	p.DeckMasterRanks = make([]int, 5)
	deckBlock, _ := data["userDeck"].(map[string]any)
	decks := sliceOf(data["userDecks"])
	cards := sliceOf(data["userCards"])
	for i := 0; i < 5; i++ {
		if p.IsNewData && deckBlock != nil {
			p.UserDecks[i] = int64(intGet(deckBlock, fmt.Sprintf("member%d", i+1)))
		} else {
			decknum := firstInt(intGet(gamedata, "deck"), 1)
			for _, d := range decks {
				dm, ok := d.(map[string]any)
				if !ok || intGet(dm, "deckId") != decknum {
					continue
				}
				p.UserDecks[i] = int64(intGet(dm, fmt.Sprintf("member%d", i+1)))
				break
			}
		}
		for _, c := range cards {
			cm, ok := c.(map[string]any)
			if !ok || int64(intGet(cm, "cardId")) != p.UserDecks[i] {
				continue
			}
			if strGet(cm, "defaultImage") == "special_training" {
				p.SpecialTraining[i] = true
			}
			p.DeckMasterRanks[i] = intGet(cm, "masterRank")
		}
	}
	return p, nil
}

// ProfilePayload 构造个人信息出图载荷，对齐 ProfileView.payload_from_profile 的字段集。
func (p *Profile) ProfilePayload() map[string]any {
	return map[string]any{
		"name":              p.Name,
		"rank":              p.Rank,
		"word":              p.Word,
		"twitterId":         p.TwitterID,
		"characterId":       p.CharacterID,
		"characterRank":     p.CharacterRank,
		"userDecks":         p.UserDecks,
		"special_training":  p.SpecialTraining,
		"deck_master_ranks": p.DeckMasterRanks,
		"userProfileHonors": p.Honors,
		"userHonorMissions": p.Missions,
		"highScore":         p.HighScore,
		"clear":             p.Clear[:],
		"full_combo":        p.FullCombo[:],
		"full_perfect":      p.FullPerfect[:],
		"mvpCount":          p.MvpCount,
		"superStarCount":    p.SuperStarCount,
		"isNewData":         p.IsNewData,
	}
}
