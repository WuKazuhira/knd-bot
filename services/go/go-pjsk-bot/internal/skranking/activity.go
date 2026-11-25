package skranking

import (
	"sort"
	"time"
)

type ActivityStats struct {
	HourlySpeed    float64
	TwentyMinSpeed float64
	PlayCount      int
	AvgPt          float64
	LastPt         int64
	IsPlaying      bool
	StopDuration   *time.Duration // nil 表示还在打
}

func BuildActivityStats(history []Ranking, latest Ranking, preWindowMaxScore int64) ActivityStats {
	sorted := make([]Ranking, len(history))
	copy(sorted, history)
	sort.SliceStable(sorted, func(i, j int) bool { return sorted[i].Time.Before(sorted[j].Time) })

	var stats ActivityStats

	cfStart := latest.Time.Add(-time.Hour)
	recent := filterFrom(sorted, cfStart)
	stats.HourlySpeed = speedOver(recent)

	twentyAgo := latest.Time.Add(-20 * time.Minute)
	stats.TwentyMinSpeed = speedOver(filterFrom(sorted, twentyAgo))

	// 两路榜线快照可能偶发回退到旧分，只计突破历史高点的增量避免多算周回
	var pts []int64
	if len(recent) > 0 {
		highWater := max(recent[0].Score, preWindowMaxScore)
		for _, r := range sorted {
			if r.Time.Before(cfStart) && r.Score > highWater {
				highWater = r.Score
			}
		}
		for _, r := range recent[1:] {
			if r.Score > highWater {
				pts = append(pts, r.Score-highWater)
				highWater = r.Score
			}
		}
	}
	stats.PlayCount = len(pts)
	if len(pts) > 0 {
		n := 10
		if len(pts) < n {
			n = len(pts)
		}
		var sum int64
		for _, p := range pts[len(pts)-n:] {
			sum += p
		}
		stats.AvgPt = float64(sum) / float64(n)
		stats.LastPt = pts[len(pts)-1]
	}

	fiveAgo := latest.Time.Add(-5 * time.Minute)
	recent5 := filterFrom(sorted, fiveAgo)
	if len(recent5) >= 2 {
		for i := 0; i+1 < len(recent5); i++ {
			if recent5[i+1].Score != recent5[i].Score {
				stats.IsPlaying = true
				break
			}
		}
	}

	if !stats.IsPlaying {
		lastChange := latest.Time
		for i := len(sorted) - 1; i > 0; i-- {
			if sorted[i].Score != sorted[i-1].Score {
				lastChange = sorted[i].Time
				break
			}
		}
		d := latest.Time.Sub(lastChange)
		stats.StopDuration = &d
	}

	return stats
}

func filterFrom(history []Ranking, from time.Time) []Ranking {
	var out []Ranking
	for _, r := range history {
		if !r.Time.Before(from) {
			out = append(out, r)
		}
	}
	return out
}

func speedOver(seq []Ranking) float64 {
	if len(seq) < 2 {
		return 0
	}
	first, last := seq[0], seq[len(seq)-1]
	dt := last.Time.Sub(first.Time).Seconds()
	if dt <= 0 {
		return 0
	}
	return float64(last.Score-first.Score) / dt * 3600 / 10000
}
