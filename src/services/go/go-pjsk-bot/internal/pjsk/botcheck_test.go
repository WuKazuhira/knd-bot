package pjsk

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

func TestBotcheckPersistenceAndPermission(t *testing.T) {
	dataDir := t.TempDir()
	module := NewBotcheckModule(dataDir, []int64{42})
	event := onebot.MessageEvent{MessageType: "private", UserID: 42}
	if action := module.handleModify(context.Background(), router.Request{Event: event, RawCmd: "添加uni分布式", Arg: "12345"}); action == nil {
		t.Fatal("add should reply")
	}
	path := filepath.Join(dataDir, "ondemand", "database", "unibot.json")
	if _, err := os.Stat(path); err != nil {
		t.Fatal(err)
	}
	query := module.handleQuery(context.Background(), router.Request{Event: event})
	if query == nil || messageText(query) == "" {
		t.Fatalf("query action=%+v", query)
	}
	if want := "12345"; !containsMessageText(query, want) {
		t.Fatalf("query text=%q", messageText(query))
	}
	if action := module.handleModify(context.Background(), router.Request{Event: event, RawCmd: "删除uni分布式", Arg: "12345"}); action == nil {
		t.Fatal("delete should reply")
	}
	if action := module.handleModify(context.Background(), router.Request{Event: onebot.MessageEvent{MessageType: "private", UserID: 7}, RawCmd: "添加uni分布式", Arg: "12345"}); action == nil {
		t.Fatal("denied action should reply")
	}
}

func TestBotcheckUsesCachedGroupState(t *testing.T) {
	module := NewBotcheckModule(t.TempDir(), nil)
	event := onebot.MessageEvent{MessageType: "group", GroupID: 123}
	if blocked, action := module.CheckGroup(context.Background(), event); blocked || action != nil {
		t.Fatalf("empty cache should allow group: blocked=%v action=%v", blocked, action)
	}

	module.mu.Lock()
	module.blocked[event.GroupID] = true
	module.blockedUsers[event.GroupID] = 456
	module.mu.Unlock()

	if blocked, action := module.CheckGroup(context.Background(), event); !blocked || action != nil {
		t.Fatalf("cached block should be returned without action: blocked=%v action=%v", blocked, action)
	}
	report := module.scanReport(context.Background())
	if len(report) != 1 || report[0].groupID != 123 || report[0].userID != 456 {
		t.Fatalf("cached report=%v", report)
	}
}

func messageText(action *onebot.ActionRequest) string {
	message, _ := action.Params["message"].(onebot.Message)
	return message.PlainText()
}

func containsMessageText(action *onebot.ActionRequest, value string) bool {
	return len(messageText(action)) > 0 && len(value) > 0 && stringContains(messageText(action), value)
}

func stringContains(text, value string) bool {
	for i := 0; i+len(value) <= len(text); i++ {
		if text[i:i+len(value)] == value {
			return true
		}
	}
	return false
}
