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

func TestResolveWLFromChapters(t *testing.T) {
	// 两章：chapterNo 1(cid 5)、2(cid 8)；章节2 开始更晚（当前章节）
	past := time.Now().Add(-48 * time.Hour).UnixMilli()
	recent := time.Now().Add(-time.Hour).UnixMilli()
	chapters := []map[string]any{
		{"chapterNo": float64(1), "gameCharacterId": float64(5), "chapterStartAt": float64(past)},
		{"chapterNo": float64(2), "gameCharacterId": float64(8), "chapterStartAt": float64(recent)},
	}
	base := 150

	// wl2 100 → 编码 2*1000+150=2150，剩余 "100"
	id, rest, ch := resolveWLFromChapters(chapters, nil, "wl2 100", base)
	if id != 2150 || rest != "100" || ch == nil {
		t.Errorf("wl2 100 => id=%d rest=%q ch=%v", id, rest, ch)
	}

	// wl 1 100 → 章节1，编码 1150，剩余 "100"
	id, rest, _ = resolveWLFromChapters(chapters, nil, "wl 1 100", base)
	if id != 1150 || rest != "100" {
		t.Errorf("wl 1 100 => id=%d rest=%q", id, rest)
	}

	// 裸 wl（无后随数字）→ 当前章节（chapterStartAt 最晚的 = 章节2）
	id, rest, ch = resolveWLFromChapters(chapters, nil, "wl", base)
	if ch == nil || intField(ch, "chapterNo") != 2 || rest != "" {
		t.Errorf("wl => id=%d rest=%q ch=%v", id, rest, ch)
	}

	// wl 100：100 被当作章节号（对齐 Python：wl 后接数字视为章节选择）；
	// 章节 100 不存在 → 返回 base + 原样参数。
	id, rest, ch = resolveWLFromChapters(chapters, nil, "wl 100", base)
	if id != base || ch != nil {
		t.Errorf("wl 100 => id=%d rest=%q ch=%v (应视为不存在的章节100)", id, rest, ch)
	}

	// 无 WL token → 原样返回 base
	id, rest, ch = resolveWLFromChapters(chapters, nil, "100", base)
	if id != base || rest != "100" || ch != nil {
		t.Errorf("100 => id=%d rest=%q ch=%v", id, rest, ch)
	}

	// 非 WL 活动（无章节）→ 原样
	id, rest, ch = resolveWLFromChapters(nil, nil, "wl2 100", base)
	if id != base || ch != nil {
		t.Errorf("no chapters => id=%d ch=%v", id, ch)
	}
}

func TestIsWLShortcut(t *testing.T) {
	cases := []struct {
		in   string
		want bool
	}{
		{"wlsk", true},
		{"wlcsb", true},
		{"cnwlsk", true},
		{"twwlsks", true},
		{"WLSK", true},
		{"sk", false},
		{"cf", false},
		{"csb", false},
		{"cnsk", false},
	}
	for _, c := range cases {
		if got := isWLShortcut(c.in); got != c.want {
			t.Errorf("isWLShortcut(%q)=%v want %v", c.in, got, c.want)
		}
	}
}

func TestCurrentWLChapter(t *testing.T) {
	past := time.Now().Add(-48 * time.Hour).UnixMilli()
	recent := time.Now().Add(-time.Hour).UnixMilli()
	future := time.Now().Add(48 * time.Hour).UnixMilli()
	chapters := []map[string]any{
		{"chapterNo": float64(1), "chapterStartAt": float64(past)},
		{"chapterNo": float64(2), "chapterStartAt": float64(recent)},
		{"chapterNo": float64(3), "chapterStartAt": float64(future)},
	}
	// 已开始的最晚一章 = 章节2
	ch := currentWLChapter(chapters)
	if ch == nil || intField(ch, "chapterNo") != 2 {
		t.Errorf("当前章节应为 2, got %v", ch)
	}
	// 全未开始 → 首章
	fut2 := []map[string]any{
		{"chapterNo": float64(5), "chapterStartAt": float64(future)},
	}
	if ch := currentWLChapter(fut2); ch == nil || intField(ch, "chapterNo") != 5 {
		t.Errorf("全未开始应返回首章, got %v", ch)
	}
	if currentWLChapter(nil) != nil {
		t.Error("空章节应返回 nil")
	}
}
