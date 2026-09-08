package skranking

import (
	"testing"
	"time"
)

func TestFromSK(t *testing.T) {
	now := time.Now()
	// 完整字段
	r, ok := FromSK(map[string]any{
		"userId": float64(12345), "name": "player", "score": float64(1000000), "rank": float64(1),
	}, now)
	if !ok {
		t.Fatal("完整字段应解析成功")
	}
	if r.UID != "12345" || r.Name != "player" || r.Score != 1000000 || r.Rank != 1 {
		t.Errorf("解析结果错误: %+v", r)
	}
	// userId 为字符串
	r2, ok := FromSK(map[string]any{
		"userId": "999", "name": "p2", "score": float64(50), "rank": float64(100),
	}, now)
	if !ok || r2.UID != "999" {
		t.Errorf("字符串 userId 解析错误: %+v", r2)
	}
	// 缺字段
	if _, ok := FromSK(map[string]any{"userId": float64(1), "name": "x"}, now); ok {
		t.Error("缺 score/rank 应解析失败")
	}
}

func TestFromItems(t *testing.T) {
	items := []any{
		map[string]any{"userId": float64(1), "name": "a", "score": float64(300), "rank": float64(1)},
		map[string]any{"userId": float64(2), "name": "b", "score": float64(200), "rank": float64(2)},
		"invalid",                   // 非 map，跳过
		map[string]any{"name": "c"}, // 字段不全，跳过
	}
	rs := FromItems(items, time.Now())
	if len(rs) != 2 {
		t.Fatalf("应解析出 2 条, got %d", len(rs))
	}
}

func TestMerge(t *testing.T) {
	now := time.Now()
	t100 := []Ranking{
		{Rank: 1, Score: 1000, Time: now},
		{Rank: 2, Score: 900, Time: now},
	}
	borders := []Ranking{
		{Rank: 2, Score: 111, Time: now}, // 与 t100 rank 冲突，t100 优先
		{Rank: 100, Score: 50, Time: now},
	}
	merged := Merge(t100, borders)
	if len(merged) != 3 {
		t.Fatalf("合并应有 3 条, got %d", len(merged))
	}
	// 按 rank 升序
	if merged[0].Rank != 1 || merged[1].Rank != 2 || merged[2].Rank != 100 {
		t.Errorf("排序错误: %+v", merged)
	}
	// rank 2 应保留 t100 的分数（先传入优先）
	if merged[1].Score != 900 {
		t.Errorf("rank2 应保留 t100 分数 900, got %d", merged[1].Score)
	}
}
