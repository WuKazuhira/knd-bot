package pjsk

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

func TestLiveAPIPathUsesEncodedPlaceholder(t *testing.T) {
	path, err := liveAPIPath("cn")
	if err != nil {
		t.Fatal(err)
	}
	if want := "/api/cn/user/%25user_id%25/live/auto"; path != want {
		t.Fatalf("path=%q want %q", path, want)
	}
	if _, err := liveAPIPath("us"); err == nil {
		t.Fatal("未知区服应报错")
	}
}

func TestRemoteModuleLiveCanBeCancelled(t *testing.T) {
	var liveCalls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/echo":
			w.WriteHeader(http.StatusOK)
		case "/api/cn/user/%user_id%/live/auto":
			liveCalls.Add(1)
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"success":true}`))
		default:
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()

	m := NewRemoteModule(RemoteConfig{
		APIURL:       srv.URL,
		APIToken:     "token",
		Region:       "cn",
		Account:      "123",
		LiveInterval: 10 * time.Millisecond,
		DataDir:      t.TempDir(),
	})
	defer m.Close()
	m.startLive()
	deadline := time.Now().Add(time.Second)
	for liveCalls.Load() == 0 && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	if liveCalls.Load() == 0 {
		t.Fatal("后台循环没有调用 live API")
	}
	if !m.isLiveRunning() {
		t.Fatal("启动后应处于运行状态")
	}
	if !m.stopLive() {
		t.Fatal("stopLive 应返回 stopped")
	}
	if m.isLiveRunning() {
		t.Fatal("取消后仍显示运行中")
	}
	if got := m.state.Load(); got.LiveOn {
		t.Fatalf("取消后 live_on 应为 false: %+v", got)
	}
}

func TestEnsureAPIUsesEcho(t *testing.T) {
	var gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()
	m := NewRemoteModule(RemoteConfig{APIURL: srv.URL, DataDir: t.TempDir()})
	ok, message := m.ensureAPI(context.Background())
	if !ok || message != "API 可用" || gotPath != "/echo" {
		t.Fatalf("ensureAPI=(%v,%q), path=%q", ok, message, gotPath)
	}
}

func TestRemoteStatusFormattingHelpers(t *testing.T) {
	if got := onOff(true); got != "开启" || onOff(false) != "关闭" {
		t.Fatalf("onOff 输出错误: %q/%q", got, onOff(false))
	}
	if got := compactBody([]byte(strings.Repeat("x", 250))); len(got) != 203 {
		t.Fatalf("compactBody 长度=%d", len(got))
	}
}

func TestRemoteCommandsRouteToReplyForSuperuser(t *testing.T) {
	m := NewRemoteModule(RemoteConfig{
		APIURL:     "http://127.0.0.1:1",
		DataDir:    t.TempDir(),
		Superusers: []int64{1994226627},
	})
	defer m.Close()

	r := router.New([]string{"/", ""}, router.ParseOwnership(`[
		"pjsk_remote", "pjsk_live"
	]`))
	m.Register(r)

	for _, text := range []string{"remote off", "live off"} {
		event := onebot.MessageEvent{
			SelfID:      1,
			UserID:      1994226627,
			MessageID:   1,
			MessageType: "group",
			GroupID:     883957546,
			Message:     onebot.Message{onebot.Text(text)},
		}
		req, handler, ok := r.Match(event)
		if !ok || handler == nil {
			t.Fatalf("%q 未经过 remote 路由: ok=%v req=%+v", text, ok, req)
		}
		action := handler(context.Background(), req)
		if action == nil {
			t.Fatalf("%q handler 未生成回复 action", text)
		}
		body, err := action.Marshal()
		if err != nil || !strings.Contains(string(body), "send_msg") || !strings.Contains(string(body), "883957546") {
			t.Fatalf("%q action=%s err=%v", text, body, err)
		}
	}
}
