package pjsk

import "testing"

func TestParseFilter(t *testing.T) {
	// 组合：四星 + cool + 限定 + mmj团
	f := parseFilter("四星 cool 限定 mmj")
	if f.rarity != "rarity_4" {
		t.Errorf("rarity = %q, want rarity_4", f.rarity)
	}
	if f.attr != "cool" {
		t.Errorf("attr = %q, want cool", f.attr)
	}
	if f.unit != "idol" {
		t.Errorf("unit = %q, want idol (mmj)", f.unit)
	}
	if !f.limited || !f.hasLimited {
		t.Error("应识别限定")
	}

	// 颜色别名 + 数字稀有度 + fes
	f2 := parseFilter("蓝 3 fes")
	if f2.attr != "cool" {
		t.Errorf("蓝 应映射为 cool, got %q", f2.attr)
	}
	if f2.rarity != "rarity_3" {
		t.Errorf("3 应映射为 rarity_3, got %q", f2.rarity)
	}
	if !f2.fes {
		t.Error("应识别 fes")
	}

	// 常驻
	f3 := parseFilter("常驻")
	if !f3.permanent || !f3.hasLimited {
		t.Error("应识别常驻")
	}

	// 空参数
	f4 := parseFilter("")
	if f4.rarity != "" || f4.attr != "" || f4.unit != "" || f4.hasLimited {
		t.Errorf("空参数应无筛选, got %+v", f4)
	}
}

func TestParseFilterEventAndLeak(t *testing.T) {
	if f := parseFilter("box"); !f.showBox {
		t.Error("应识别 box 持卡模式")
	}
	f := parseFilter("活动 leak 2024")
	if !f.eventOnly {
		t.Error("应识别活动卡筛选")
	}
	if !f.showLeak {
		t.Error("应识别 leak 剧透模式")
	}
	if f.year != 2024 {
		t.Errorf("year=%d want 2024", f.year)
	}
	if f2 := parseFilter("event"); !f2.eventOnly {
		t.Error("event 应识别为活动卡筛选")
	}
}

func TestExtractUserCardPairs(t *testing.T) {
	data := map[string]any{
		"userGamedata": map[string]any{
			"userCards": []any{
				map[string]any{"cardId": float64(101), "masterRank": float64(4)},
				map[string]any{"cardId": float64(102), "master_rank": float64(2)},
			},
		},
	}
	got := extractUserCardPairs(data)
	if len(got) != 2 || got[0] != [2]int64{101, 4} || got[1] != [2]int64{102, 2} {
		t.Fatalf("pairs=%v, want [[101 4] [102 2]]", got)
	}
}

func TestParseFilterYear(t *testing.T) {
	// 4 位数字识别为年份
	f := parseFilter("四星 2021")
	if f.year != 2021 {
		t.Errorf("year=%d want 2021", f.year)
	}
	if f.rarity != "rarity_4" {
		t.Errorf("rarity=%q want rarity_4", f.rarity)
	}
	// 非 4 位数字不作年份（3 已是稀有度、20 无匹配）
	if f2 := parseFilter("3"); f2.year != 0 {
		t.Errorf("单个 3 不应是年份, got %d", f2.year)
	}
	if f3 := parseFilter("2021 2022"); f3.year != 2022 {
		t.Errorf("多个年份取后者, got %d", f3.year)
	}
}
