package pjsk

import "testing"

func TestParseFilter(t *testing.T) {
	// 组合：四星 + cool + 限定 + mmj团
	f := parseFilter("四星 cool 限定 mmj")
	if f.rarity != "rarity_4" {
		t.Errorf("rarity = %q, want rarity_4", f.rarity)
	}
	if f.attr != "cool" {
		t.Errorf("attr = %q, want cool", f.attr)
	}
	if f.unit != "idol" {
		t.Errorf("unit = %q, want idol (mmj)", f.unit)
	}
	if !f.limited || !f.hasLimited {
		t.Error("应识别限定")
	}

	// 颜色别名 + 数字稀有度 + fes
	f2 := parseFilter("蓝 3 fes")
	if f2.attr != "cool" {
		t.Errorf("蓝 应映射为 cool, got %q", f2.attr)
	}
	if f2.rarity != "rarity_3" {
		t.Errorf("3 应映射为 rarity_3, got %q", f2.rarity)
	}
	if !f2.fes {
		t.Error("应识别 fes")
	}

	// 常驻
	f3 := parseFilter("常驻")
	if !f3.permanent || !f3.hasLimited {
		t.Error("应识别常驻")
	}

	// 空参数
	f4 := parseFilter("")
	if f4.rarity != "" || f4.attr != "" || f4.unit != "" || f4.hasLimited {
		t.Errorf("空参数应无筛选, got %+v", f4)
	}
}
