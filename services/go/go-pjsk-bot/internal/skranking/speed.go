package skranking

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

type RankTableRow struct {
	Rank  int      `json:"rank"`
	Score int64    `json:"score"`
	Speed *float64 `json:"speed"`
}

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
