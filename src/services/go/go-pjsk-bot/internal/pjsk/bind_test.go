package pjsk

import (
	"context"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

func TestDigitsOnly(t *testing.T) {
	cases := map[string]string{
		"123":       "123",
		" 123 ":     "123",
		"id:456789": "456789",
		"abc":       "",
		"12a34b":    "1234",
		"":          "",
		"７４８６":      "", // 全角数字不算（对齐 \d ASCII 行为的保守实现）
	}
	for in, want := range cases {
		if got := digitsOnly(in); got != want {
			t.Errorf("digitsOnly(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestResolveUserIDWithArg(t *testing.T) {
	// 带参数分支在触及 store 之前返回，可用 nil store 安全测试。
	r := NewUserResolver(nil)
	mkReq := func(arg string, server router.ServerType) router.Request {
		return router.Request{
			Event:  onebot.MessageEvent{UserID: 100, SelfID: 1},
			Server: server,
			Arg:    arg,
		}
	}

	// 合法国服 uid
	uid, priv, errMsg := r.Resolve(context.Background(), mkReq("7486056827726306063", router.ServerCN))
	if errMsg != "" || priv || uid != "7486056827726306063" {
		t.Fatalf("合法 uid 参数应直接返回, got uid=%q priv=%v err=%q", uid, priv, errMsg)
	}

	// 非法 uid
	_, _, errMsg = r.Resolve(context.Background(), mkReq("123", router.ServerCN))
	if errMsg != errID {
		t.Fatalf("非法 uid 应返回 errID, got %q", errMsg)
	}
}
