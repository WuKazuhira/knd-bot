package router

import (
	"context"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
)

func msgEvent(text string) onebot.MessageEvent {
	return onebot.MessageEvent{
		SelfID:      1,
		UserID:      100,
		MessageID:   1,
		MessageType: "group",
		GroupID:     200,
		Message:     onebot.Message{onebot.Text(text)},
	}
}

func newTestRouter() (*Router, *[]Request) {
	r := New([]string{"/", ""}, ParseOwnership(`["sk","skill","bind"]`))
	var seen []Request
	h := func(_ context.Context, req Request) *onebot.ActionRequest {
		seen = append(seen, req)
		return nil
	}
	r.Register("sk", []string{"查榜"}, h)
	r.Register("skill", nil, h)
	r.Register("bind", []string{"绑定"}, h)
	return r, &seen
}

func TestMatchServerPrefix(t *testing.T) {
	r, _ := newTestRouter()
	cases := []struct {
		text       string
		wantCmd    string
		wantServer ServerType
		wantArg    string
	}{
		{"/sk 100", "sk", ServerJP, "100"},
		{"cnsk 50", "sk", ServerCN, "50"},
		{"twsk", "sk", ServerTW, ""},
		{"/绑定 123", "bind", ServerJP, "123"},
		{"cn绑定 456", "bind", ServerCN, "456"},
	}
	for _, c := range cases {
		req, _, ok := r.Match(msgEvent(c.text))
		if !ok {
			t.Errorf("%q 未匹配", c.text)
			continue
		}
		if req.Command != c.wantCmd || req.Server != c.wantServer || req.Arg != c.wantArg {
			t.Errorf("%q => cmd=%s server=%d arg=%q, want cmd=%s server=%d arg=%q",
				c.text, req.Command, req.Server, req.Arg, c.wantCmd, c.wantServer, c.wantArg)
		}
	}
}

func TestMatchNumericSuffix(t *testing.T) {
	r := New([]string{"/", ""}, ParseOwnership(`["sk","cf"]`))
	h := func(_ context.Context, req Request) *onebot.ActionRequest { return nil }
	r.RegisterNumericSuffix("sk", nil, h)
	r.RegisterNumericSuffix("cf", nil, h)

	cases := []struct {
		text       string
		wantCmd    string
		wantServer ServerType
		wantArg    string
	}{
		{"/sk100", "sk", ServerJP, "100"},
		{"cf10", "cf", ServerJP, "10"},
		{"cnsk100", "sk", ServerCN, "100"},
		{"twcf10", "cf", ServerTW, "10"},
	}
	for _, tc := range cases {
		req, _, ok := r.Match(msgEvent(tc.text))
		if !ok || req.Command != tc.wantCmd || req.Server != tc.wantServer || req.Arg != tc.wantArg {
			t.Errorf("%q => ok=%v command=%q server=%v arg=%q, want %q/%v/%q",
				tc.text, ok, req.Command, req.Server, req.Arg, tc.wantCmd, tc.wantServer, tc.wantArg)
		}
	}
	if _, _, ok := r.Match(msgEvent("/skabc")); ok {
		t.Fatal("数字后缀路由不应放宽为任意文本后缀")
	}
}

func TestMatchLongestWins(t *testing.T) {
	// "skill" 不应被 "sk" 抢先匹配
	r, _ := newTestRouter()
	req, _, ok := r.Match(msgEvent("/skill"))
	if !ok || req.Command != "skill" {
		t.Fatalf("skill 应匹配 skill 指令，got ok=%v cmd=%s", ok, req.Command)
	}
}

func TestMatchNoFalsePrefix(t *testing.T) {
	// "skabc" 不是 sk（后面非空白），也不是已注册指令
	r, _ := newTestRouter()
	if _, _, ok := r.Match(msgEvent("/skabc")); ok {
		t.Fatal("skabc 不应匹配任何指令")
	}
}

func TestMatchUnknown(t *testing.T) {
	r, _ := newTestRouter()
	if _, _, ok := r.Match(msgEvent("/不存在的指令")); ok {
		t.Fatal("未知指令不应匹配")
	}
	if _, _, ok := r.Match(msgEvent("")); ok {
		t.Fatal("空消息不应匹配")
	}
}

func TestMatchOwnershipFilter(t *testing.T) {
	// 只接管 bind，不接管 sk：sk 应交给 Python（不匹配）
	r := New([]string{"/", ""}, ParseOwnership(`["bind"]`))
	h := func(_ context.Context, req Request) *onebot.ActionRequest { return nil }
	r.Register("sk", nil, h)
	r.Register("bind", nil, h)

	if _, _, ok := r.Match(msgEvent("/bind 123")); !ok {
		t.Fatal("bind 已接管，应匹配")
	}
	if _, _, ok := r.Match(msgEvent("/sk")); ok {
		t.Fatal("sk 未接管，应交给 Python（不匹配）")
	}
}

func TestMatchRegex(t *testing.T) {
	r := New([]string{"/", ""}, ParseOwnership(`["pjsk抽卡"]`))
	r.RegisterRegex("pjsk抽卡", `^(cn|tw|jp)? *(?:pjsk|sekai) *(反向?)? *(抽卡|十连抽?|[0-9]+连抽?) *([0-9]+)?$`,
		func(_ context.Context, req Request) *onebot.ActionRequest { return nil })

	req, _, ok := r.Match(msgEvent("pjsk十连"))
	if !ok {
		t.Fatal("pjsk十连 应匹配抽卡正则")
	}
	if req.Command != "pjsk抽卡" || len(req.RegexGroups) < 4 {
		t.Fatalf("正则捕获组不完整: cmd=%s groups=%v", req.Command, req.RegexGroups)
	}
	// cn 前缀 + 反向 + 卡池 id
	req2, _, ok2 := r.Match(msgEvent("cnpjsk反十连 123"))
	if !ok2 {
		t.Fatal("cnpjsk反十连 123 应匹配")
	}
	if req2.RegexGroups[1] != "cn" || req2.RegexGroups[2] == "" || req2.RegexGroups[4] != "123" {
		t.Fatalf("捕获组解析错误: %v", req2.RegexGroups)
	}
}

func TestMatchRegexOwnership(t *testing.T) {
	// 未接管时不匹配（交 Python）
	r := New([]string{"/", ""}, ParseOwnership(`[]`))
	r.RegisterRegex("pjsk抽卡", `^(?:pjsk|sekai)抽卡$`,
		func(_ context.Context, req Request) *onebot.ActionRequest { return nil })
	if _, _, ok := r.Match(msgEvent("pjsk抽卡")); ok {
		t.Fatal("未接管的正则指令不应匹配")
	}
}

func TestMigratedCommandAliasesAndServerPrefixes(t *testing.T) {
	ownership := ParseOwnership(`[
		"pjskbpm", "查bpm", "卡牌一览"
	]`)
	r := New([]string{"/", ""}, ownership)
	h := func(_ context.Context, req Request) *onebot.ActionRequest { return nil }
	r.Register("pjskbpm", []string{"bpm", "查曲bpm"}, h)
	r.Register("查bpm", nil, h)
	r.Register("卡牌一览", []string{"cardbox"}, h)
	cases := []struct {
		message, command string
		server           ServerType
	}{
		{"cnbpm", "pjskbpm", ServerCN},
		{"tw查bpm", "查bpm", ServerTW},
		{"/cncardbox", "卡牌一览", ServerCN},
	}
	for _, tc := range cases {
		req, _, ok := r.Match(msgEvent(tc.message))
		if !ok || req.Command != tc.command || req.Server != tc.server {
			t.Errorf("%q => ok=%v command=%q server=%v, want %q/%v", tc.message, ok, req.Command, req.Server, tc.command, tc.server)
		}
	}
}

func TestParseOwnership(t *testing.T) {
	cases := []struct {
		raw     string
		command string
		want    bool
	}{
		{`["bind","sk"]`, "bind", true},
		{`["bind","sk"]`, "deck", false},
		{`bind,sk`, "sk", true}, // 逗号分隔兜底
		{``, "bind", false},     // 空：全部由 Python
	}
	for _, c := range cases {
		o := ParseOwnership(c.raw)
		if got := o.Owns(c.command); got != c.want {
			t.Errorf("ParseOwnership(%q).Owns(%q) = %v, want %v", c.raw, c.command, got, c.want)
		}
	}
	if !ParseOwnership("").Empty() {
		t.Fatal("空配置应 Empty")
	}
	if ParseOwnership(`["bind"]`).Empty() {
		t.Fatal("非空配置不应 Empty")
	}
	standalone := All()
	if standalone.Empty() || !standalone.Owns("deck") {
		t.Fatal("All ownership 应接管任意已注册命令")
	}
}

// TestMatchCnPrefixedCommandName 验证命令名本身以 cn/tw 开头时不被误判成分服前缀。
func TestMatchCnPrefixedCommandName(t *testing.T) {
	seen := []Request{}
	h := func(_ context.Context, req Request) *onebot.ActionRequest {
		seen = append(seen, req)
		return nil
	}
	r := New([]string{"/", ""}, ParseOwnership(`["cnmsr启用","sk"]`))
	r.Register("cnmsr启用", nil, h) // 规范名以 cn 开头，不是分服指令
	r.Register("sk", nil, h)

	// cnmsr启用：不应被剥成 msr启用 + ServerCN
	req, _, ok := r.Match(msgEvent("/cnmsr启用 123456"))
	if !ok {
		t.Fatal("cnmsr启用 应匹配成功")
	}
	if req.Command != "cnmsr启用" {
		t.Errorf("Command=%q want cnmsr启用", req.Command)
	}
	if req.Server != ServerJP {
		t.Errorf("Server=%v want ServerJP（cn 是命令名一部分，非分服前缀）", req.Server)
	}

	// 对照：cnsk 仍应正确识别为 CN 服的 sk
	req, _, ok = r.Match(msgEvent("cnsk 100"))
	if !ok || req.Command != "sk" || req.Server != ServerCN {
		t.Errorf("cnsk => cmd=%q server=%v, want sk/ServerCN", req.Command, req.Server)
	}
}
