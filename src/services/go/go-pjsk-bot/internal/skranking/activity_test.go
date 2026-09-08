package skranking

import (
	"testing"
	"time"
)

func mkR(score int64, secAgoFromBase int, base time.Time) Ranking {
	return Ranking{UID: "u1", Name: "P", Score: score, Rank: 1, Time: base.Add(time.Duration(-secAgoFromBase) * time.Second)}
}

func TestBuildActivityStatsPlaying(t *testing.T) {
	base := time.Now()
	// 近5分钟内有分数变化 → 在打
	history := []Ranking{
		mkR(1000, 600, base), // 10分钟前
		mkR(2000, 240, base), // 4分钟前
		mkR(3000, 60, base),  // 1分钟前
	}
	latest := history[len(history)-1]
	stats := BuildActivityStats(history, latest)
	if !stats.IsPlaying {
		t.Error("近5分钟有分数变化应判定为在打")
	}
	if stats.StopDuration != nil {
		t.Error("在打时 StopDuration 应为 nil")
	}
	if stats.PlayCount != 2 {
		t.Errorf("PlayCount 应为 2, got %d", stats.PlayCount)
	}
	if stats.LastPt != 1000 {
		t.Errorf("LastPt 应为 1000, got %d", stats.LastPt)
	}
	if stats.HourlySpeed <= 0 {
		t.Errorf("HourlySpeed 应>0, got %f", stats.HourlySpeed)
	}
}

func TestBuildActivityStatsStopped(t *testing.T) {
	base := time.Now()
	// 最近5分钟无变化 → 停车，停车时长从最后一次变化算起
	history := []Ranking{
		mkR(5000, 1800, base), // 30分钟前
		mkR(6000, 900, base),  // 15分钟前（最后一次变化）
		mkR(6000, 60, base),   // 1分钟前，无变化
	}
	latest := history[len(history)-1]
	stats := BuildActivityStats(history, latest)
	if stats.IsPlaying {
		t.Error("近5分钟无变化应判定为停车")
	}
	if stats.StopDuration == nil {
		t.Fatal("停车时应有 StopDuration")
	}
	// 停车时长约等于 15 分钟（900-60=840s）
	got := stats.StopDuration.Seconds()
	if got < 830 || got > 850 {
		t.Errorf("停车时长应约 840s, got %f", got)
	}
}

func TestBuildActivityStatsEmpty(t *testing.T) {
	base := time.Now()
	latest := mkR(100, 0, base)
	stats := BuildActivityStats([]Ranking{latest}, latest)
	if stats.PlayCount != 0 || stats.HourlySpeed != 0 {
		t.Errorf("单点历史应无速度/次数: %+v", stats)
	}
}
