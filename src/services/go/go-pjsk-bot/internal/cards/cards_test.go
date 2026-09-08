package cards

import "testing"

func TestCardType(t *testing.T) {
	costume3ds := []map[string]any{
		{"id": float64(10), "partType": "hair"},
		{"id": float64(11), "partType": "body"},
	}
	cardCostume3ds := []map[string]any{
		{"cardId": float64(100), "costume3dId": float64(10)}, // 关联 hair -> 限定
		{"cardId": float64(200), "costume3dId": float64(11)}, // 关联 body -> 非限定
	}
	if CardType(100, cardCostume3ds, costume3ds) != 1 {
		t.Error("card 100 应为限定(1)")
	}
	if CardType(200, cardCostume3ds, costume3ds) != 0 {
		t.Error("card 200 应为常驻(0)")
	}
	if CardType(999, cardCostume3ds, costume3ds) != 0 {
		t.Error("无关联卡应为常驻(0)")
	}
}

func TestIsFes(t *testing.T) {
	supplies := []map[string]any{
		{"id": float64(1), "cardSupplyType": "colorful_festival_limited"},
		{"id": float64(2), "cardSupplyType": "term_limited"},
		{"id": float64(3), "cardSupplyType": "bloom_festival_limited"},
	}
	if !IsFes(map[string]any{"cardSupplyId": float64(1)}, supplies) {
		t.Error("colorful_festival_limited 应为 fes")
	}
	if !IsFes(map[string]any{"cardSupplyId": float64(3)}, supplies) {
		t.Error("bloom_festival_limited 应为 fes")
	}
	if IsFes(map[string]any{"cardSupplyId": float64(2)}, supplies) {
		t.Error("term_limited 不应为 fes")
	}
	if IsFes(map[string]any{}, supplies) {
		t.Error("无 supplyId 不应为 fes")
	}
}

func TestUnitVsChars(t *testing.T) {
	gcu := []map[string]any{
		{"gameCharacterId": float64(21), "unit": "light_sound"},
		{"gameCharacterId": float64(22), "unit": "light_sound"},
		{"gameCharacterId": float64(21), "unit": "idol"},
		{"gameCharacterId": float64(1), "unit": "light_sound"}, // 非 VS 角色，忽略
	}
	vs := UnitVsChars("light_sound", gcu)
	if len(vs) != 2 || vs[0] != 21 || vs[1] != 22 {
		t.Errorf("light_sound VS 角色应为 [21,22], got %v", vs)
	}
	if UnitVsChars("piapro", gcu) != nil {
		t.Error("piapro 不应有额外 VS 角色")
	}
}
