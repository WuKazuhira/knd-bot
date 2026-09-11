package pjsk

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
)

func TestParseUpdateGroups(t *testing.T) {
	all, err := parseUpdateGroups("0")
	if err != nil || len(all) != 7 {
		t.Fatalf("all=%v err=%v", all, err)
	}
	groups, err := parseUpdateGroups("6 2 2")
	if err != nil || strings.Join(groups, ",") != "2,6" {
		t.Fatalf("groups=%v err=%v", groups, err)
	}
	if _, err := parseUpdateGroups("8"); err == nil {
		t.Fatal("invalid group should fail")
	}
}

func TestUpdateModuleRefreshAndExecute(t *testing.T) {
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/masterdata/refresh" || r.URL.Query().Get("region") != "jp" {
			t.Errorf("unexpected helper request: %s", r.URL.String())
		}
		calls++
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"region":"jp","file":"cards.json","changed":true}`))
	}))
	defer server.Close()

	module := NewUpdateModule(nil, t.TempDir(), server.URL, []int64{42})
	defer module.Close()
	event := onebot.MessageEvent{MessageType: "private", UserID: 42}
	action := module.executeUpdate(event, 0, "3")
	if action == nil || !strings.Contains(messageText(action), "卡面相关资源") {
		t.Fatalf("action=%+v text=%q", action, messageText(action))
	}
	if calls != len(updateGroups["3"]) {
		t.Fatalf("helper calls=%d want %d", calls, len(updateGroups["3"]))
	}
	if err := module.refreshMasterdata(context.Background(), 0, "cards.json"); err != nil {
		t.Fatal(err)
	}
}
