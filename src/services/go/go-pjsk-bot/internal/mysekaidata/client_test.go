package mysekaidata

import "testing"

func TestProfileFromSuiteData(t *testing.T) {
	data := map[string]any{
		"name": "MSRPlayer",
		"rank": float64(150),
		"userDecks": []any{
			map[string]any{
				"deckId": float64(1), "member1": float64(100), "member2": float64(200),
				"member3": float64(0), "member4": float64(0), "member5": float64(0),
			},
		},
		"userCards": []any{
			map[string]any{"cardId": float64(100), "defaultImage": "special_training"},
			map[string]any{"cardId": float64(200), "defaultImage": "original"},
		},
		"upload_time": float64(1700000000),
	}

	p := ProfileFromSuiteData("12345", data)

	if p["userid"] != "12345" {
		t.Errorf("userid = %v", p["userid"])
	}
	if p["name"] != "MSRPlayer" {
		t.Errorf("name = %v, want MSRPlayer", p["name"])
	}
	if p["rank"] != 150 {
		t.Errorf("rank = %v, want 150", p["rank"])
	}
	decks, ok := p["userDecks"].([]int64)
	if !ok || len(decks) != 5 || decks[0] != 100 || decks[1] != 200 {
		t.Errorf("userDecks 错误: %v", p["userDecks"])
	}
	st, ok := p["special_training"].([]bool)
	if !ok || !st[0] || st[1] {
		t.Errorf("special_training 错误: %v (member1 应特训, member2 否)", p["special_training"])
	}
	if p["suite_update_time"] != 1700000000 {
		t.Errorf("suite_update_time = %v", p["suite_update_time"])
	}
}

func TestProfileFromSuiteDataEmpty(t *testing.T) {
	// 空数据不崩溃，name 兜底 ???
	p := ProfileFromSuiteData("1", nil)
	if p["name"] != "???" {
		t.Errorf("空数据 name 应兜底 ???, got %v", p["name"])
	}
	if p["userid"] != "1" {
		t.Errorf("userid = %v", p["userid"])
	}
}

func TestProfileFromSuiteDataGamedataNested(t *testing.T) {
	// name/rank 嵌套在 userGamedata 里
	data := map[string]any{
		"userGamedata": map[string]any{"name": "Nested", "rank": float64(99), "deck": float64(1)},
	}
	p := ProfileFromSuiteData("2", data)
	if p["name"] != "Nested" || p["rank"] != 99 {
		t.Errorf("嵌套 gamedata 解析错误: name=%v rank=%v", p["name"], p["rank"])
	}
}
