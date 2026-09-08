package pjsk

import "testing"

func newTestEventModule() *EventModule {
	// chara 为 nil：仅用内置缩写解析（足够覆盖参数解析逻辑）。
	return &EventModule{}
}

func TestEventArgParseType(t *testing.T) {
	m := newTestEventModule()
	p := m.eventArgParse([]string{"5v5"})
	if !p.IsLegal || p.EventType != "cheerful_carnival" {
		t.Errorf("5v5 => %+v", p)
	}
	// 两个活动类型 → 非法
	p = m.eventArgParse([]string{"普活", "wl"})
	if p.IsLegal {
		t.Error("两个活动类型应非法")
	}
}

func TestEventArgParseAttr(t *testing.T) {
	m := newTestEventModule()
	p := m.eventArgParse([]string{"蓝星"})
	if !p.IsLegal || p.EventAttr != "cool" {
		t.Errorf("蓝星 => %+v", p)
	}
	p = m.eventArgParse([]string{"心"})
	if p.EventAttr != "happy" {
		t.Errorf("心 => attr=%s", p.EventAttr)
	}
}

func TestEventArgParseUnit(t *testing.T) {
	m := newTestEventModule()
	// 单组合 → 箱活语义（isEqualUnits 保持 true）
	p := m.eventArgParse([]string{"ln"})
	if !p.IsLegal || len(p.UnitsName) != 1 || p.UnitsName[0] != "light_sound" {
		t.Errorf("ln => %+v", p)
	}
	// 组合+加成 → isEqualUnits false
	p = m.eventArgParse([]string{"ln加成"})
	if p.IsEqualUnits {
		t.Error("ln加成 应使 isEqualUnits=false")
	}
}

func TestEventArgParseChara(t *testing.T) {
	m := newTestEventModule()
	// 内置缩写 miku（VS 角色 21）
	p := m.eventArgParse([]string{"miku"})
	if !p.IsLegal || len(p.CharasID) != 1 {
		t.Fatalf("miku => %+v", p)
	}
	if cid, ok := p.CharasID[0].(int); !ok || cid != 21 {
		t.Errorf("miku 应解析为 21, got %v", p.CharasID[0])
	}
	// 普通角色 knd(17) → 会并入其组合 school_refusal
	p = m.eventArgParse([]string{"knd"})
	if len(p.CharasID) != 1 {
		t.Errorf("knd => charas=%v", p.CharasID)
	}

	// 带附属组合的 VS 角色：ln miku → (21, light_sound)
	p = m.eventArgParse([]string{"lnmiku"})
	if !p.IsLegal || len(p.CharasID) != 1 {
		t.Fatalf("lnmiku => %+v", p)
	}
	if tup, ok := p.CharasID[0].([2]any); !ok || tup[0].(int) != 21 || tup[1].(string) != "light_sound" {
		t.Errorf("lnmiku 应为 (21, light_sound), got %v", p.CharasID[0])
	}
}

func TestEventArgParseTeamEvent(t *testing.T) {
	m := newTestEventModule()
	p := m.eventArgParse([]string{"箱活"})
	if p.IsTeamEvent == nil || !*p.IsTeamEvent {
		t.Errorf("箱活 => %+v", p)
	}
	// 箱活 + 组合 → 非法
	p = m.eventArgParse([]string{"箱活", "ln"})
	if p.IsLegal {
		t.Error("箱活+组合 应非法")
	}
}

func TestEventArgParseUnknown(t *testing.T) {
	m := newTestEventModule()
	p := m.eventArgParse([]string{"不存在的东西xyz"})
	if p.IsLegal {
		t.Error("无法识别的参数应非法")
	}
}

func TestToCharasPayload(t *testing.T) {
	p := eventArgParams{CharasID: []any{21, [2]any{22, "light_sound"}}}
	payload := p.toCharasPayload()
	if len(payload) != 2 {
		t.Fatalf("应有 2 项, got %d", len(payload))
	}
	if payload[0].(int) != 21 {
		t.Errorf("首项应为 int 21")
	}
	arr, ok := payload[1].([]any)
	if !ok || len(arr) != 2 || arr[0].(int) != 22 || arr[1].(string) != "light_sound" {
		t.Errorf("次项应为 [22, light_sound], got %v", payload[1])
	}
}
