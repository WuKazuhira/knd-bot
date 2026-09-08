package pjsk

import (
	"context"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

func mkReq(cmd string, userID, groupID int64) router.Request {
	ev := onebot.MessageEvent{UserID: userID, GroupID: groupID}
	if groupID > 0 {
		ev.MessageType = "group"
	} else {
		ev.MessageType = "private"
	}
	return router.Request{Event: ev, Command: cmd, Server: router.ServerJP}
}

func okHandler(_ context.Context, req router.Request) *onebot.ActionRequest {
	return onebot.ReplyText(req.Event, "ok", false)
}

func replyText(a *onebot.ActionRequest) string {
	if a == nil {
		return ""
	}
	msg, _ := a.Params["message"].(onebot.Message)
	return msg.PlainText()
}

func TestRateLimiterCDBlocksAfterLimit(t *testing.T) {
	rl := NewRateLimiter(nil)
	ctx := context.Background()
	// pjsk b30: 60s 内 2 次
	req := mkReq("pjsk b30", 100, 0)
	if got := replyText(rl.Wrap(ctx, req, okHandler)); got != "ok" {
		t.Fatalf("第1次应通过, got %q", got)
	}
	if got := replyText(rl.Wrap(ctx, req, okHandler)); got != "ok" {
		t.Fatalf("第2次应通过, got %q", got)
	}
	// 第3次超限
	if got := replyText(rl.Wrap(ctx, req, okHandler)); got == "ok" {
		t.Error("第3次应被 CD 拦截")
	}
}

func TestRateLimiterPerUserIsolation(t *testing.T) {
	rl := NewRateLimiter(nil)
	ctx := context.Background()
	// 用户 A 刷满
	for i := 0; i < 2; i++ {
		rl.Wrap(ctx, mkReq("逮捕", 1, 0), okHandler)
	}
	if got := replyText(rl.Wrap(ctx, mkReq("逮捕", 1, 0), okHandler)); got == "ok" {
		t.Error("用户 A 第3次应被拦截")
	}
	// 用户 B 不受影响
	if got := replyText(rl.Wrap(ctx, mkReq("逮捕", 2, 0), okHandler)); got != "ok" {
		t.Error("用户 B 应不受用户 A 的 CD 影响")
	}
}

func TestRateLimiterSuperuserExempt(t *testing.T) {
	rl := NewRateLimiter([]int64{999})
	ctx := context.Background()
	// superuser 连发多次都应通过
	for i := 0; i < 10; i++ {
		if got := replyText(rl.Wrap(ctx, mkReq("pjsk b30", 999, 0), okHandler)); got != "ok" {
			t.Fatalf("superuser 第%d次应豁免, got %q", i+1, got)
		}
	}
}

func TestRateLimiterDefaultRule(t *testing.T) {
	rl := NewRateLimiter(nil)
	ctx := context.Background()
	// 未配置的命令用默认规则（60s/5 次）
	req := mkReq("查时间", 5, 0)
	for i := 0; i < 5; i++ {
		if got := replyText(rl.Wrap(ctx, req, okHandler)); got != "ok" {
			t.Fatalf("默认规则第%d次应通过, got %q", i+1, got)
		}
	}
	if got := replyText(rl.Wrap(ctx, req, okHandler)); got == "ok" {
		t.Error("默认规则第6次应被拦截")
	}
}
