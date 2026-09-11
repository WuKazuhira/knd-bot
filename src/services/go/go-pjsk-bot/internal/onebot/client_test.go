package onebot

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

func TestSendWithoutConnectionIsSafe(t *testing.T) {
	client := NewClient("ws://127.0.0.1:1", "", nil, nil)
	if err := client.SendGroupMessage(100, Message{Text("hello")}); !errors.Is(err, ErrNotConnected) {
		t.Fatalf("expected ErrNotConnected, got %v", err)
	}
	if err := client.SendMessage("other", 100, Message{Text("hello")}); !errors.Is(err, ErrInvalidMessageType) {
		t.Fatalf("expected ErrInvalidMessageType, got %v", err)
	}
	if err := client.SendPrivateMessage(0, Message{Text("hello")}); !errors.Is(err, ErrInvalidTarget) {
		t.Fatalf("expected ErrInvalidTarget, got %v", err)
	}
}

func TestConcurrentActiveSends(t *testing.T) {
	upgrader := websocket.Upgrader{CheckOrigin: func(*http.Request) bool { return true }}
	received := make(chan []byte, 32)
	connected := make(chan struct{})
	var once sync.Once
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		once.Do(func() { close(connected) })
		defer conn.Close()
		for {
			_, data, err := conn.ReadMessage()
			if err != nil {
				return
			}
			received <- data
		}
	}))
	defer server.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	url := "ws" + strings.TrimPrefix(server.URL, "http")
	client := NewClient(url, "", nil, nil)
	runDone := make(chan error, 1)
	go func() { runDone <- client.Run(ctx) }()
	select {
	case <-connected:
	case <-time.After(2 * time.Second):
		t.Fatal("client did not connect")
	}
	// 服务端 Upgrade 返回早于客户端 Dial 完成；等待 Client 发布 conn，
	// 避免把握手窗口误判成后台发送失败。
	deadline := time.Now().Add(2 * time.Second)
	for {
		client.mu.Lock()
		ready := client.conn != nil
		client.mu.Unlock()
		if ready {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("client connection was not published")
		}
		time.Sleep(time.Millisecond)
	}

	const count = 20
	var wg sync.WaitGroup
	for i := 0; i < count; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if err := client.SendGroupMessage(100, Message{Text("hello")}); err != nil {
				t.Errorf("concurrent SendGroupMessage: %v", err)
			}
		}()
	}
	wg.Wait()
	for i := 0; i < count; i++ {
		select {
		case data := <-received:
			var action ActionRequest
			if err := json.Unmarshal(data, &action); err != nil {
				t.Fatalf("decode action: %v", err)
			}
			if action.Action != "send_msg" || action.Params["message_type"] != "group" {
				t.Fatalf("unexpected action: %#v", action)
			}
		case <-time.After(2 * time.Second):
			t.Fatalf("received %d/%d actions", i, count)
		}
	}
	cancel()
	select {
	case <-runDone:
	case <-time.After(2 * time.Second):
		t.Fatal("client did not stop after context cancellation")
	}
}

func TestReverseServeDispatchesAndSends(t *testing.T) {
	eventReceived := make(chan MessageEvent, 1)
	client := NewClientWithNotice("", "", func(event MessageEvent) *ActionRequest {
		eventReceived <- event
		return ReplyText(event, "go-ok", false)
	}, nil, nil)

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	runDone := make(chan error, 1)
	go func() { runDone <- client.serveListener(ctx, listener, "/onebot/v11/ws") }()

	healthDeadline := time.Now().Add(2 * time.Second)
	for {
		resp, healthErr := http.Get("http://" + listener.Addr().String() + "/healthz")
		if healthErr == nil {
			if resp.StatusCode != http.StatusOK {
				resp.Body.Close()
				t.Fatalf("health status=%d", resp.StatusCode)
			}
			resp.Body.Close()
			break
		}
		if time.Now().After(healthDeadline) {
			t.Fatalf("reverse server healthz unavailable: %v", healthErr)
		}
		time.Sleep(5 * time.Millisecond)
	}

	url := "ws://" + listener.Addr().String() + "/onebot/v11/ws"
	var conn *websocket.Conn
	deadline := time.Now().Add(2 * time.Second)
	for {
		conn, _, err = websocket.DefaultDialer.Dial(url, nil)
		if err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("reverse server did not accept connection: %v", err)
		}
		time.Sleep(5 * time.Millisecond)
	}
	defer conn.Close()

	incoming := MessageEvent{
		Time:        time.Now().Unix(),
		SelfID:      2277876593,
		PostType:    "message",
		MessageType: "group",
		SubType:     "normal",
		MessageID:   123,
		UserID:      456,
		GroupID:     789,
		Message:     Message{Text("hello")},
		RawMessage:  "hello",
	}
	if err := conn.WriteJSON(incoming); err != nil {
		t.Fatal(err)
	}
	select {
	case got := <-eventReceived:
		if got.GroupID != incoming.GroupID || got.Message.PlainText() != "hello" {
			t.Fatalf("unexpected event: %#v", got)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("reverse server did not dispatch event")
	}

	conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	_, data, err := conn.ReadMessage()
	if err != nil {
		t.Fatal("read handler action:", err)
	}
	var action ActionRequest
	if err := json.Unmarshal(data, &action); err != nil {
		t.Fatal(err)
	}
	if action.Action != "send_msg" || action.Params["message_type"] != "group" {
		t.Fatalf("unexpected handler action: %#v", action)
	}

	if err := client.SendGroupMessage(incoming.GroupID, Message{Text("scheduled")}); err != nil {
		t.Fatal("proactive send:", err)
	}
	_, data, err = conn.ReadMessage()
	if err != nil {
		t.Fatal("read proactive action:", err)
	}
	if err := json.Unmarshal(data, &action); err != nil {
		t.Fatal(err)
	}
	if action.Action != "send_msg" || action.Params["group_id"] != float64(incoming.GroupID) {
		t.Fatalf("unexpected proactive action: %#v", action)
	}

	cancel()
	select {
	case err := <-runDone:
		if err != nil {
			t.Fatalf("reverse server stop: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("reverse server did not stop after context cancellation")
	}
}
