package skranking

import (
	"testing"
	"time"
)

func TestBuildActivityStatsCountsOnlyNewScoreHighs(t *testing.T) {
	tests := []struct {
		name    string
		samples []struct {
			minutesBefore int
			score         int64
		}
		preWindowMaxScore int64
		wantCount         int
		wantAvg           float64
		wantLast          int64
	}{
		{
			name: "stale snapshot recovers after a score increase",
			samples: []struct {
				minutesBefore int
				score         int64
			}{
				{70, 900}, {65, 1100}, {59, 1100}, {40, 1200},
				{39, 1100}, {38, 1200}, {0, 1350},
			},
			wantCount: 2, wantAvg: 125, wantLast: 150,
		},
		{
			name: "score high before the window is not a new play",
			samples: []struct {
				minutesBefore int
				score         int64
			}{
				{70, 1200}, {65, 1000}, {59, 1000}, {50, 1200},
				{49, 1000}, {48, 1200}, {0, 1250},
			},
			wantCount: 1, wantAvg: 50, wantLast: 50,
		},
		{
			name: "old high omitted from the Go ranking tail still blocks recovery",
			samples: []struct {
				minutesBefore int
				score         int64
			}{
				{59, 1000}, {50, 1200}, {0, 1250},
			},
			preWindowMaxScore: 1200,
			wantCount:         1, wantAvg: 50, wantLast: 50,
		},
		{
			name: "monotonic scores retain normal counts",
			samples: []struct {
				minutesBefore int
				score         int64
			}{
				{59, 100}, {30, 120}, {0, 145},
			},
			wantCount: 2, wantAvg: 22.5, wantLast: 25,
		},
	}

	base := time.Date(2026, time.September, 26, 10, 0, 0, 0, time.UTC)
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			history := make([]Ranking, 0, len(tt.samples))
			for _, sample := range tt.samples {
				history = append(history, Ranking{Score: sample.score, Time: base.Add(-time.Duration(sample.minutesBefore) * time.Minute)})
			}
			stats := BuildActivityStats(history, history[len(history)-1], tt.preWindowMaxScore)
			if stats.PlayCount != tt.wantCount || stats.AvgPt != tt.wantAvg || stats.LastPt != tt.wantLast {
				t.Errorf("got count=%d avg=%v last=%d; want count=%d avg=%v last=%d",
					stats.PlayCount, stats.AvgPt, stats.LastPt, tt.wantCount, tt.wantAvg, tt.wantLast)
			}
		})
	}
}
