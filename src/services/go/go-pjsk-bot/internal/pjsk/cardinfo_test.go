package pjsk

import "testing"

func TestParseCardParametersJP(t *testing.T) {
	// JP: list of {cardParameterType, power}，同类型取最大
	raw := []any{
		map[string]any{"cardParameterType": "performance", "power": float64(1000)},
		map[string]any{"cardParameterType": "vocal", "power": float64(900)},
		map[string]any{"cardParameterType": "technical", "power": float64(950)}, // 也映射 param2
		map[string]any{"cardParameterType": "visual", "power": float64(800)},
	}
	p := parseCardParameters(raw)
	if p["param1"] != 1000 {
		t.Errorf("param1(performance) = %v, want 1000", p["param1"])
	}
	// vocal(900) 与 technical(950) 都映射 param2，取最大 950
	if p["param2"] != 950 {
		t.Errorf("param2 = %v, want 950", p["param2"])
	}
	if p["param3"] != 800 {
		t.Errorf("param3(visual) = %v, want 800", p["param3"])
	}
}

func TestParseCardParametersCN(t *testing.T) {
	// CN: dict of {param: [powers...]}，取最大
	raw := map[string]any{
		"param1": []any{float64(800), float64(1000), float64(900)},
		"param2": []any{float64(1100)},
	}
	p := parseCardParameters(raw)
	if p["param1"] != 1000 {
		t.Errorf("param1 = %v, want 1000", p["param1"])
	}
	if p["param2"] != 1100 {
		t.Errorf("param2 = %v, want 1100", p["param2"])
	}
}

func TestMaxOfAny(t *testing.T) {
	if got := maxOfAny([]any{float64(1), float64(5), float64(3)}); got != 5 {
		t.Errorf("maxOfAny list = %d, want 5", got)
	}
	if got := maxOfAny(float64(42)); got != 42 {
		t.Errorf("maxOfAny scalar = %d, want 42", got)
	}
	if got := maxOfAny(nil); got != 0 {
		t.Errorf("maxOfAny nil = %d, want 0", got)
	}
}
