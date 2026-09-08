package pjsk

import "testing"

func TestCurrentEventID(t *testing.T) {
	events := []map[string]any{
		{"id": float64(1), "startAt": float64(1000), "aggregateAt": float64(2000)},
		{"id": float64(2), "startAt": float64(5000), "aggregateAt": float64(6000)},
		{"id": float64(3), "startAt": float64(9000), "aggregateAt": float64(10000)},
	}

	// now 在活动1的结算缓冲期内（aggregateAt=2000, +600000）
	if id := currentEventID(events, 2500); id != 1 {
		t.Errorf("结算缓冲期应返回活动1, got %d", id)
	}
	// now 在活动1进行中
	if id := currentEventID(events, 1500); id != 1 {
		t.Errorf("进行中应返回活动1, got %d", id)
	}
	// now 在所有活动之前 -> 下一期（最早的活动1）
	if id := currentEventID(events, 500); id != 1 {
		t.Errorf("应返回下一期活动1, got %d", id)
	}
	// now 在活动3结束后很久 -> 最后一期（活动3）
	if id := currentEventID(events, 100_000_000); id != 3 {
		t.Errorf("应返回最后一期活动3, got %d", id)
	}
}

func TestFmtEventTime(t *testing.T) {
	if fmtEventTime(0) != "" {
		t.Error("0 时间戳应返回空串")
	}
	// 固定时间戳（UTC+8）：1704067200000 ms = 2024-01-01 08:00:00 CST
	got := fmtEventTime(1704067200000)
	if got != "2024/01/01 08:00:00" {
		t.Errorf("fmtEventTime = %q, want 2024/01/01 08:00:00", got)
	}
}
