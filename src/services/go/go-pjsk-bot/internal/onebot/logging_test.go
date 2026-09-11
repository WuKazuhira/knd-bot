package onebot

import (
	"strings"
	"testing"
)

func TestTruncateText(t *testing.T) {
	if got := TruncateText("  hello\n world  ", 20); got != "hello world" {
		t.Fatalf("TruncateText whitespace=%q", got)
	}
	got := TruncateText(strings.Repeat("中", 8), 5)
	if got != "中中中中中…" {
		t.Fatalf("TruncateText long=%q", got)
	}
}

func TestActionSummaryHidesMessagePayload(t *testing.T) {
	action := SendMessageAction(MessageEvent{MessageType: "group", GroupID: 123}, Message{Text("secret"), ImageBytes("very-long-base64")})
	got := ActionSummary(action)
	if !strings.Contains(got, "group=123") || !strings.Contains(got, "segments=text,image") {
		t.Fatalf("ActionSummary=%q", got)
	}
	if strings.Contains(got, "secret") || strings.Contains(got, "very-long-base64") {
		t.Fatalf("ActionSummary leaked payload: %q", got)
	}
}

func TestLooksLikeCommand(t *testing.T) {
	for _, text := range []string{"/sk", "cn卡牌一览 box", "cf 100", "pjsk猜曲"} {
		if !LooksLikeCommand(text) {
			t.Errorf("LooksLikeCommand(%q)=false", text)
		}
	}
	if LooksLikeCommand("今天晚上吃什么") {
		t.Error("普通聊天不应被判为命令")
	}
}
