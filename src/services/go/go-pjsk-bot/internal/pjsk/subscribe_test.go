package pjsk

import "testing"

func TestIsNotifiableVlive(t *testing.T) {
	now := int64(1_000_000_000_000)
	day := int64(24 * 3600 * 1000)

	// 正常 live：未结束、持续 < 30 天
	normal := map[string]any{
		"virtualLiveType": "normal",
		"startAt":         float64(now - day),
		"endAt":           float64(now + 5*day),
	}
	if !isNotifiableVlive(normal, now) {
		t.Error("正常进行中的 live 应可通知")
	}

	// beginner 类型排除
	beginner := map[string]any{
		"virtualLiveType": "beginner",
		"startAt":         float64(now),
		"endAt":           float64(now + day),
	}
	if isNotifiableVlive(beginner, now) {
		t.Error("beginner live 不应通知")
	}

	// 已结束
	ended := map[string]any{
		"virtualLiveType": "normal",
		"startAt":         float64(now - 10*day),
		"endAt":           float64(now - day),
	}
	if isNotifiableVlive(ended, now) {
		t.Error("已结束的 live 不应通知")
	}

	// 常驻 live（持续 > 30 天）
	permanent := map[string]any{
		"virtualLiveType": "normal",
		"startAt":         float64(now),
		"endAt":           float64(now + 40*day),
	}
	if isNotifiableVlive(permanent, now) {
		t.Error("常驻 live（>30天）不应通知")
	}
}
