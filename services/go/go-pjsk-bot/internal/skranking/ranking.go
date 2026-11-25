package skranking

import "time"

type Ranking struct {
	UID   string
	Name  string
	Score int64
	Rank  int
	Time  time.Time
}

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

// 先传入的优先，避免 T100 和档线重复
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
	sortInts(order)
	out := make([]Ranking, 0, len(order))
	for _, rank := range order {
		out = append(out, merged[rank])
	}
	return out
}

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
