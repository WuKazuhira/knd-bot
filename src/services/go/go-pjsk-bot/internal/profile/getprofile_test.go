package profile

import "testing"

func TestProfilePayload(t *testing.T) {
	p := &Profile{
		Name:            "Player",
		Rank:            120,
		Word:            "hello",
		TwitterID:       "tw",
		CharacterID:     21,
		UserDecks:       []int64{1, 2, 3, 4, 5},
		SpecialTraining: []bool{true, false, true, false, true},
		DeckMasterRanks: []int{5, 4, 3, 2, 1},
		HighScore:       123456,
		MvpCount:        10,
		SuperStarCount:  3,
		IsNewData:       true,
	}
	p.Clear[4] = 200
	p.FullCombo[4] = 150
	p.FullPerfect[4] = 100

	payload := p.ProfilePayload()

	// 关键字段齐全且正确
	checks := map[string]any{
		"name":           "Player",
		"rank":           120,
		"word":           "hello",
		"twitterId":      "tw",
		"characterId":    21,
		"highScore":      123456,
		"mvpCount":       10,
		"superStarCount": 3,
		"isNewData":      true,
	}
	for k, want := range checks {
		if payload[k] != want {
			t.Errorf("payload[%q] = %v, want %v", k, payload[k], want)
		}
	}

	// 数组字段类型正确
	if decks, ok := payload["userDecks"].([]int64); !ok || len(decks) != 5 {
		t.Errorf("userDecks 类型/长度错误: %v", payload["userDecks"])
	}
	if clear, ok := payload["clear"].([]int); !ok || clear[4] != 200 {
		t.Errorf("clear 字段错误: %v", payload["clear"])
	}
	if st, ok := payload["special_training"].([]bool); !ok || !st[0] {
		t.Errorf("special_training 字段错误: %v", payload["special_training"])
	}

	// 字段集应与 ProfileView._FIELDS 一致（18 个）
	if len(payload) != 18 {
		t.Errorf("payload 字段数 = %d, want 18", len(payload))
	}
}
