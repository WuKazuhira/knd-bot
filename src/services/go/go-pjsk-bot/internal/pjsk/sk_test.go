package pjsk

import (
	"testing"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/skranking"
)

func TestHasWLToken(t *testing.T) {
	cases := []struct {
		in   string
		want bool
	}{
		{"wl", true},
		{"wl2", true},
		{"wl 2", true},
		{"wlmfy", true},
		{"-c mfy", true},
		{"100", false},
		{"1-10", false},
		{"1 2 3", false},
		{"", false},
		{"123456789", false},
	}
	for _, c := range cases {
		if got := hasWLToken(c.in); got != c.want {
			t.Errorf("hasWLToken(%q)=%v want %v", c.in, got, c.want)
		}
	}
}

func TestCfRangeRegex(t *testing.T) {
	if !reCfRange.MatchString("1-10") {
		t.Error("1-10 应匹配范围")
	}
	if reCfRange.MatchString("1 2") {
		t.Error("1 2 不应匹配范围")
	}
	if !reCfMultiRank.MatchString("1 2 3") {
		t.Error("1 2 3 应匹配多排名")
	}
	if reCfMultiRank.MatchString("100") {
		t.Error("单个数字不应匹配多排名")
	}
	if reCfMultiRank.MatchString("1-10") {
		t.Error("范围不应匹配多排名")
	}
}

func TestDaysBetween(t *testing.T) {
	loc := time.UTC
	a := time.Date(2024, 5, 1, 23, 0, 0, 0, loc)
	b := time.Date(2024, 5, 3, 1, 0, 0, 0, loc)
	if got := daysBetween(a, b); got != 2 {
		t.Errorf("daysBetween=%d want 2", got)
	}
	if got := daysBetween(a, a); got != 0 {
		t.Errorf("同日应为 0, got %d", got)
	}
}

func TestComputeStopPeriods(t *testing.T) {
	base := time.Date(2024, 5, 1, 0, 0, 0, 0, time.UTC)
	mk := func(score int64, minAfter int) skranking.Ranking {
		return skranking.Ranking{Score: score, Time: base.Add(time.Duration(minAfter) * time.Minute)}
	}
	// score=1000 从 0→10min（停车 10min，≥5 计入）；11min 变化；
	// score=2000 从 11→13min（仅 2min，不计）；14min 变化后立即结束。
	history := []skranking.Ranking{
		mk(1000, 0),
		mk(1000, 10),
		mk(2000, 11),
		mk(2000, 13),
		mk(3000, 14),
	}
	periods := computeStopPeriods(history)
	if len(periods) != 1 {
		t.Fatalf("应有 1 个停车区间(≥5min), got %d: %v", len(periods), periods)
	}
	if periods[0]["minutes"].(int) != 10 {
		t.Errorf("停车时长应为 10, got %v", periods[0]["minutes"])
	}
}

func TestComputeStopPeriodsMultiple(t *testing.T) {
	base := time.Date(2024, 5, 1, 0, 0, 0, 0, time.UTC)
	mk := func(score int64, minAfter int) skranking.Ranking {
		return skranking.Ranking{Score: score, Time: base.Add(time.Duration(minAfter) * time.Minute)}
	}
	// 两段各 ≥5min 的停车：1000(0→8) 和 2000(9→20)
	history := []skranking.Ranking{
		mk(1000, 0),
		mk(1000, 8),
		mk(2000, 9),
		mk(2000, 20),
		mk(3000, 21),
	}
	periods := computeStopPeriods(history)
	if len(periods) != 2 {
		t.Fatalf("应有 2 个停车区间, got %d: %v", len(periods), periods)
	}
	if periods[0]["minutes"].(int) != 8 || periods[1]["minutes"].(int) != 11 {
		t.Errorf("停车时长应为 8 和 11, got %v / %v", periods[0]["minutes"], periods[1]["minutes"])
	}
}
