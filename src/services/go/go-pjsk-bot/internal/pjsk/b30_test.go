package pjsk

import "testing"

func TestFcrank(t *testing.T) {
	cases := []struct {
		level int
		ap    float64
		want  float64
	}{
		{32, 33.0, 31.5}, // level<=32: ap-1.5
		{31, 30.0, 28.5},
		{33, 34.0, 33.0}, // level>32: ap-1
		{37, 37.5, 36.5},
	}
	for _, c := range cases {
		if got := fcrank(c.level, c.ap); got != c.want {
			t.Errorf("fcrank(%d, %.1f) = %.2f, want %.2f", c.level, c.ap, got, c.want)
		}
	}
}

func TestRound2(t *testing.T) {
	cases := map[float64]float64{
		33.333333: 33.33,
		33.335:    33.34,
		30.0:      30.0,
	}
	for in, want := range cases {
		if got := round2(in); got != want {
			t.Errorf("round2(%v) = %v, want %v", in, got, want)
		}
	}
}

func TestIntStrField(t *testing.T) {
	m := map[string]any{"musicId": float64(74), "musicDifficulty": "master"}
	if intField(m, "musicId") != 74 {
		t.Errorf("intField musicId = %d, want 74", intField(m, "musicId"))
	}
	if strField(m, "musicDifficulty") != "master" {
		t.Errorf("strField musicDifficulty 错误")
	}
	if intField(m, "missing") != 0 || strField(m, "missing") != "" {
		t.Error("缺失字段应返回零值")
	}
}
