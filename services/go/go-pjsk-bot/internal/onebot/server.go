package onebot

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

const DefaultReversePath = "/onebot/v11/ws"

func (c *Client) Serve(ctx context.Context, addr, path string) error {
	if ctx == nil {
		ctx = context.Background()
	}
	addr = strings.TrimSpace(addr)
	if addr == "" {
		return errors.New("onebot reverse listen address is empty")
	}
	listener, err := net.Listen("tcp", addr)
	if err != nil {
		return fmt.Errorf("listen %s: %w", addr, err)
	}
	return c.serveListener(ctx, listener, normalizeReversePath(path))
}

func (c *Client) serveListener(ctx context.Context, listener net.Listener, path string) error {
	if ctx == nil {
		ctx = context.Background()
	}
	if listener == nil {
		return errors.New("onebot reverse listener is nil")
	}
	path = normalizeReversePath(path)
	c.mu.Lock()
	allowUnauthenticated := c.allowUnauthenticatedReverse
	c.mu.Unlock()

	if c.token == "" && !allowUnauthenticated {
		c.logf("[onebot] 拒绝反向 WS 握手：请配置 PJSKBOT_ONEBOT_TOKEN")
	}
	serveCtx, cancel := context.WithCancel(ctx)
	var activeMu sync.Mutex
	var active sync.WaitGroup
	upgrader := websocket.Upgrader{
		HandshakeTimeout: 10 * time.Second,
		CheckOrigin: func(r *http.Request) bool {
			return r.Header.Get("Origin") == ""
		},
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
	mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, _ *http.Request) {
		status := c.ConnectionStatus()
		w.Header().Set("Content-Type", "application/json")
		if !status.Connected {
			w.WriteHeader(http.StatusServiceUnavailable)
		}
		_ = json.NewEncoder(w).Encode(status)
	})
	mux.HandleFunc(path, func(w http.ResponseWriter, r *http.Request) {
		if !c.authorizeReverse(r, allowUnauthenticated) {
			http.Error(w, http.StatusText(http.StatusUnauthorized), http.StatusUnauthorized)
			return
		}
		activeMu.Lock()
		if serveCtx.Err() != nil {
			activeMu.Unlock()
			http.Error(w, http.StatusText(http.StatusServiceUnavailable), http.StatusServiceUnavailable)
			return
		}
		active.Add(1)
		activeMu.Unlock()
		defer active.Done()

		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			c.logf("[onebot] 反向 WS 握手失败: %v", err)
			return
		}
		if err := c.serveConn(serveCtx, conn, fmt.Sprintf("reverse %s", r.RemoteAddr)); err != nil && ctx.Err() == nil && !errors.Is(err, context.Canceled) {
			c.logf("[onebot] 反向 WS 断开: %v", err)
		}
	})

	server := &http.Server{Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	shutdownDone := make(chan struct{})
	go func() {
		select {
		case <-ctx.Done():
			shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			_ = server.Shutdown(shutdownCtx)
		case <-shutdownDone:
		}
	}()

	err := server.Serve(listener)
	close(shutdownDone)
	cancel()
	// 等被接管的 WS 连接收尾，http.Server.Shutdown 不管这块
	activeMu.Lock()
	activeMu.Unlock()
	active.Wait()
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}

func (c *Client) authorizeReverse(r *http.Request, allowUnauthenticated bool) bool {
	if c.token == "" {
		return allowUnauthenticated
	}
	want := []byte(c.token)
	headerToken, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
	if ok && subtle.ConstantTimeCompare([]byte(headerToken), want) == 1 {
		return true
	}
	return subtle.ConstantTimeCompare([]byte(r.URL.Query().Get("access_token")), want) == 1
}

func normalizeReversePath(path string) string {
	path = strings.TrimSpace(path)
	if path == "" {
		return DefaultReversePath
	}
	if !strings.HasPrefix(path, "/") {
		return "/" + path
	}
	return path
}

type queuedEvent struct {
	data     []byte
	postType string
}

const (
	eventQueueLimit         = 1024
	eventQueueMaxBytes      = 32 << 20
	maxIncomingMessageBytes = 8 << 20
)

type eventQueue struct {
	mu     sync.Mutex
	cond   *sync.Cond
	items  []queuedEvent
	bytes  int
	closed bool
}

func newEventQueue() *eventQueue {
	q := &eventQueue{}
	q.cond = sync.NewCond(&q.mu)
	return q
}

func (q *eventQueue) push(event queuedEvent) bool {
	q.mu.Lock()
	defer q.mu.Unlock()
	if q.closed || len(q.items) >= eventQueueLimit || len(event.data) > eventQueueMaxBytes-q.bytes {
		return false
	}
	q.items = append(q.items, event)
	q.bytes += len(event.data)
	q.cond.Signal()
	return true
}

func (q *eventQueue) pop() (queuedEvent, bool) {
	q.mu.Lock()
	defer q.mu.Unlock()
	for len(q.items) == 0 && !q.closed {
		q.cond.Wait()
	}
	if len(q.items) == 0 {
		return queuedEvent{}, false
	}
	event := q.items[0]
	q.bytes -= len(event.data)
	q.items[0] = queuedEvent{}
	q.items = q.items[1:]
	if len(q.items) == 0 {
		q.items = nil
	}
	return event, true
}

func (q *eventQueue) close() int {
	q.mu.Lock()
	dropped := len(q.items)
	q.closed = true
	q.bytes = 0
	q.items = nil
	q.cond.Broadcast()
	q.mu.Unlock()
	return dropped
}

func (c *Client) serveConn(ctx context.Context, conn *websocket.Conn, label string) error {
	if ctx == nil {
		ctx = context.Background()
	}
	conn.SetReadLimit(maxIncomingMessageBytes)
	c.installConn(conn)

	stopClose := context.AfterFunc(ctx, func() { _ = conn.Close() })
	events := newEventQueue()
	var workers sync.WaitGroup
	const eventWorkerCount = 8
	workers.Add(eventWorkerCount)
	for i := 0; i < eventWorkerCount; i++ {
		go func() {
			defer workers.Done()
			for {
				event, ok := events.pop()
				if !ok {
					return
				}
				c.queueDepth.Add(-1)
				if ctx.Err() != nil || !c.isCurrentConn(conn) {
					return
				}
				c.dispatchEvent(ctx, conn, event.data, event.postType)
			}
		}()
	}
	defer func() {
		if dropped := events.close(); dropped > 0 {
			c.queueDepth.Add(-int64(dropped))
		}
		stopClose()
		c.clearConn(conn)
		_ = conn.Close()
		if ctx.Err() != nil {
			workers.Wait()
		}
	}()

	c.logf("[onebot] 已连接 %s", label)
	var lastQueueWarning time.Time
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		_, data, err := conn.ReadMessage()
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			return fmt.Errorf("read: %w", err)
		}
		var envelope struct {
			PostType string `json:"post_type"`
			Echo     string `json:"echo"`
			SelfID   int64  `json:"self_id"`
		}
		if err := json.Unmarshal(data, &envelope); err != nil {
			continue
		}
		if envelope.SelfID > 0 {
			c.setSelfID(conn, envelope.SelfID)
		}
		if envelope.Echo != "" {
			c.dispatchResponse(conn, data, envelope.Echo)
			continue
		}
		if envelope.PostType == "message" || envelope.PostType == "notice" {
			c.queueDepth.Add(1)
			if !events.push(queuedEvent{data: data, postType: envelope.PostType}) {
				c.queueDepth.Add(-1)
				if time.Since(lastQueueWarning) >= time.Minute {
					c.logf("[onebot] 事件队列已达容量上限，开始丢弃新事件")
					lastQueueWarning = time.Now()
				}
			}
		}
	}
}

func (c *Client) installConn(conn *websocket.Conn) {
	c.mu.Lock()
	old := c.conn
	if old != nil && old != conn {
		c.failPendingLocked()
	}
	c.conn = conn
	c.selfID = 0
	c.connectedAt = time.Now()
	c.mu.Unlock()
	if old != nil && old != conn {
		_ = old.Close()
	}
}

func (c *Client) clearConn(conn *websocket.Conn) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.conn == conn {
		c.conn = nil
		c.selfID = 0
		c.connectedAt = time.Time{}
		c.failPendingLocked()
	}
}

// 顺手把还在等的调用唤醒，防止断线后卡死
func (c *Client) failPendingLocked() {
	for echo, ch := range c.pending {
		delete(c.pending, echo)
		ch <- apiResponse{err: ErrNotConnected}
	}
}
