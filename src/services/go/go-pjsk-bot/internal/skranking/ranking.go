// Package skranking 解析 PJSK 活动榜线数据，对齐 old-python sk._ranking_api /
// _sk_sql.Ranking 的结构与解析规则。
//
// 只做榜线快照的解析与合并（榜线抓取、时序存储、时速/预测出图作为后续增量）。
package skranking

import "time"

// Ranking 是一条榜线记录，对齐 _sk_sql.Ranking。
type Ranking struct {
	UID   string
	Name  string
	Score int64
	Rank  int
	Time  time.Time
}

// FromSK 从 haruki 榜线 API 的单条数据解析，对齐 Ranking.from_sk。
// 需要 userId/name/score/rank 字段齐全，否则返回 (Ranking{}, false)。
func FromSK(item map[string]any, t time.Time) (Ranking, bool) {
	uid, hasUID := stringField(item, "userId")
	name, hasName := item["name"].(string)
	score, hasScore := intField(item, "score")
	rank, hasRank := intField(item, "rank")
	if !hasUID || !hasName || !hasScore || !hasRank {
		return Ranking{}, false
	}
	if t.IsZero() {
		t = time.Now()
	}
	return Ranking{UID: uid, Name: name, Score: score, Rank: int(rank), Time: t}, true
}

// FromItems 批量解析榜线数组，跳过字段不全的项，对齐 rankings_from_items。
func FromItems(items []any, t time.Time) []Ranking {
	out := make([]Ranking, 0, len(items))
	for _, it := range items {
		m, ok := it.(map[string]any)
		if !ok {
			continue
		}
		if r, ok := FromSK(m, t); ok {
			out = append(out, r)
		}
	}
	return out
}

// Merge 按排名去重合并多组榜线；较早传入的优先（避免 T100 与档线重复），
// 结果按 rank 升序。对齐 merge_rankings。
func Merge(groups ...[]Ranking) []Ranking {
	merged := map[int]Ranking{}
	var order []int
	for _, group := range groups {
		for _, r := range group {
			if _, exists := merged[r.Rank]; !exists {
				merged[r.Rank] = r
				order = append(order, r.Rank)
			}
		}
	}
	// 按 rank 升序输出
	sortInts(order)
	out := make([]Ranking, 0, len(order))
	for _, rank := range order {
		out = append(out, merged[rank])
	}
	return out
}

// stringField 取字符串字段，数字 userId 也转成字符串（对齐 str(data["userId"])）。
func stringField(m map[string]any, key string) (string, bool) {
	v, ok := m[key]
	if !ok {
		return "", false
	}
	switch t := v.(type) {
	case string:
		return t, true
	case float64:
		return formatInt(int64(t)), true
	case int64:
		return formatInt(t), true
	case int:
		return formatInt(int64(t)), true
	}
	return "", false
}

func intField(m map[string]any, key string) (int64, bool) {
	v, ok := m[key]
	if !ok {
		return 0, false
	}
	switch t := v.(type) {
	case float64:
		return int64(t), true
	case int64:
		return t, true
	case int:
		return int64(t), true
	}
	return 0, false
}
