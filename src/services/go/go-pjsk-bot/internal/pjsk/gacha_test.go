package pjsk

import "testing"

func TestCurrentGacha(t *testing.T) {
	gachas := []map[string]any{
		{"id": float64(1), "startAt": float64(1000), "endAt": float64(2000)},
		{"id": float64(2), "startAt": float64(2000), "endAt": float64(3000)},
		{"id": float64(3), "startAt": float64(2500), "endAt": float64(4000)},
	}
	// now=2700 落在卡池 2(2000-3000) 和 3(2500-4000)，从后往前找应返回 3
	if g := currentGacha(gachas, 2700); g == nil || intField(g, "id") != 3 {
		t.Errorf("应返回最新进行中卡池 3, got %v", g)
	}
	// now=9999 无进行中卡池
	if g := currentGacha(gachas, 9999); g != nil {
		t.Errorf("无进行中卡池应返回 nil, got %v", g)
	}
}

func TestPickByWeight(t *testing.T) {
	// 全部权重集中在一张卡，必选它
	cards := []gachaCard{
		{id: 1, weight: 0},
		{id: 2, weight: 100},
	}
	for i := 0; i < 20; i++ {
		if pick := pickByWeight(cards, 100); pick.id == 0 {
			t.Fatal("不应选到空卡")
		}
	}
	// allWeight<=0 时退化为等概率，不崩溃
	single := []gachaCard{{id: 5}}
	if pick := pickByWeight(single, 0); pick.id != 5 {
		t.Errorf("单卡应必选, got %d", pick.id)
	}
}

func TestMakeCharNamer(t *testing.T) {
	chars := []map[string]any{
		{"id": float64(1), "firstName": "星乃", "givenName": "一歌"},
		{"id": float64(21), "givenName": "初音ミク"},
	}
	namer := makeCharNamer(chars)
	if got := namer(1); got != "星乃一歌" {
		t.Errorf("namer(1) = %q, want 星乃一歌", got)
	}
	if got := namer(21); got != "初音ミク" {
		t.Errorf("namer(21) = %q, want 初音ミク", got)
	}
	if got := namer(999); got != "" {
		t.Errorf("未知角色应返回空串, got %q", got)
	}
}

func TestAtoiDefault(t *testing.T) {
	if atoiDefault("42", 0) != 42 {
		t.Error("有效数字解析失败")
	}
	if atoiDefault("abc", 10) != 10 {
		t.Error("无效数字应返回默认值")
	}
}
