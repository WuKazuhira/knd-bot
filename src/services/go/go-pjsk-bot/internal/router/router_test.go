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
	r := New([]string{"/", ""})
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
