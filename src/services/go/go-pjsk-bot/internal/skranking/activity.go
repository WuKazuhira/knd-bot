package skranking

import (
	"sort"
	"time"
)

// ActivityStats 是查房/订阅共用的玩家活动统计，对齐 _build_activity_stats。
type ActivityStats struct {
	HourlySpeed    float64        // 近1小时时速（万/h）
	TwentyMinSpeed float64        // 近20分钟时速（万/h）
	PlayCount      int            // 近1小时游玩次数（有效加分次数）
	AvgPt          float64        // 近10次平均单曲得分
	LastPt         int64          // 最近一次单曲得分
	IsPlaying      bool           // 近5分钟是否有分数变化
	StopDuration   *time.Duration // 未在打时的停车时长（nil=在打）
}

// BuildActivityStats 根据玩家历史记录（按时间升序）与最新一条记录计算活动统计。
// preWindowMaxScore 是最近一小时之前的历史最高分；完整历史可传 0。
// 对齐 old-python _build_activity_stats。
func BuildActivityStats(history []Ranking, latest Ranking, preWindowMaxScore int64) ActivityStats {
	// 保证升序（调用方通常已排好，这里稳妥起见再排一次）
	sorted := make([]Ranking, len(history))
	copy(sorted, history)
	sort.SliceStable(sorted, func(i, j int) bool { return sorted[i].Time.Before(sorted[j].Time) })

	var stats ActivityStats

	cfStart := latest.Time.Add(-time.Hour)
	recent := filterFrom(sorted, cfStart)
	stats.HourlySpeed = speedOver(recent)

	twentyAgo := latest.Time.Add(-20 * time.Minute)
	stats.TwentyMinSpeed = speedOver(filterFrom(sorted, twentyAgo))

	// 两路榜线快照可能先报出新分、随后回退到旧分，恢复时不应重复计周回。
	// 从窗口内第一条分数及窗口前的历史最高分开始，只计突破历史高点的增量。
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

	// 是否在打：近5分钟内是否有分数变化
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

	// 停车时长：从最新时间回溯到最后一次分数变化
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

// filterFrom 返回 time >= from 的记录（保持原顺序）。
func filterFrom(history []Ranking, from time.Time) []Ranking {
	var out []Ranking
	for _, r := range history {
		if !r.Time.Before(from) {
			out = append(out, r)
		}
	}
	return out
}

// speedOver 用首尾两点算时速（万/h）；少于2点返回 0。
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
