package skranking

// CalculateSpeed 按实际采样间隔折算速度（万分/period），对齐 _calculate_rank_speed。
// 分数回退或时间异常时返回 nil（拒绝该样本）。
func CalculateSpeed(latest Ranking, older *Ranking, periodSeconds int) *float64 {
	if older == nil || latest.Score < older.Score {
		return nil
	}
	elapsed := latest.Time.Sub(older.Time).Seconds()
	if elapsed <= 0 {
		return nil
	}
	v := float64(latest.Score-older.Score) * float64(periodSeconds) / elapsed / 10000
	return &v
}

// RankTableRow 是排名线/时速表的一行。
type RankTableRow struct {
	Rank  int      `json:"rank"`
	Score int64    `json:"score"`
	Speed *float64 `json:"speed"` // nil 表示数据不足
}

// BuildRankTableData 合并最新与历史榜线，按 rank 匹配计算时速，
// 对齐 _build_rank_table_data。
func BuildRankTableData(latest, older []Ranking, periodSeconds int) []RankTableRow {
	olderByRank := make(map[int]*Ranking, len(older))
	for i := range older {
		olderByRank[older[i].Rank] = &older[i]
	}
	rows := make([]RankTableRow, 0, len(latest))
	for _, l := range latest {
		rows = append(rows, RankTableRow{
			Rank:  l.Rank,
			Score: l.Score,
			Speed: CalculateSpeed(l, olderByRank[l.Rank], periodSeconds),
		})
	}
	return rows
}
