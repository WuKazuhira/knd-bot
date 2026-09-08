package skranking

import (
	"testing"
	"time"
)

func TestCalculateSpeed(t *testing.T) {
	base := time.Now()
	// 一小时内涨 10000 分 -> 时速 1 万/h
	latest := Ranking{Rank: 1, Score: 20000, Time: base}
	older := Ranking{Rank: 1, Score: 10000, Time: base.Add(-3600 * time.Second)}
	sp := CalculateSpeed(latest, &older, 3600)
	if sp == nil || *sp != 1.0 {
		t.Fatalf("时速应为 1.0 万/h, got %v", sp)
	}

	// 半小时涨 10000 -> 折算时速 2 万/h
	older2 := Ranking{Rank: 1, Score: 10000, Time: base.Add(-1800 * time.Second)}
	sp2 := CalculateSpeed(latest, &older2, 3600)
	if sp2 == nil || *sp2 != 2.0 {
		t.Fatalf("半小时涨1万应折算为 2.0 万/h, got %v", sp2)
	}

	// 分数回退 -> nil
	if CalculateSpeed(Ranking{Score: 5000, Time: base}, &Ranking{Score: 6000, Time: base.Add(-3600 * time.Second)}, 3600) != nil {
		t.Error("分数回退应返回 nil")
	}
	// older 为 nil -> nil
	if CalculateSpeed(latest, nil, 3600) != nil {
		t.Error("无历史数据应返回 nil")
	}
	// 时间异常（elapsed<=0）-> nil
	if CalculateSpeed(latest, &Ranking{Score: 10000, Time: base}, 3600) != nil {
		t.Error("时间间隔为0应返回 nil")
	}
}

func TestBuildRankTableData(t *testing.T) {
	base := time.Now()
	latest := []Ranking{
		{Rank: 1, Score: 20000, Time: base},
		{Rank: 100, Score: 5000, Time: base},
	}
	older := []Ranking{
		{Rank: 1, Score: 10000, Time: base.Add(-3600 * time.Second)},
		// rank 100 无历史 -> speed nil
	}
	rows := BuildRankTableData(latest, older, 3600)
	if len(rows) != 2 {
		t.Fatalf("应有 2 行, got %d", len(rows))
	}
	if rows[0].Rank != 1 || rows[0].Speed == nil || *rows[0].Speed != 1.0 {
		t.Errorf("rank1 时速应为 1.0, got %+v", rows[0])
	}
	if rows[1].Speed != nil {
		t.Errorf("rank100 无历史应 speed=nil, got %v", rows[1].Speed)
	}
}
