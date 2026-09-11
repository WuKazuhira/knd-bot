package onebot

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"strings"
	"time"

	"github.com/gorilla/websocket"
)

const DefaultReversePath = "/onebot/v11/ws"

// Serve 启动 OneBot v11 反向 WebSocket 服务。
// OneBotFilter 作为客户端连接到 addr+path，事件通过该连接上报，Go 通过同一连接发送 action。
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

	upgrader := websocket.Upgrader{
		// 当前服务只绑定宿主机回环地址；OneBotFilter 不携带浏览器 Origin。
		CheckOrigin: func(*http.Request) bool { return true },
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
	mux.HandleFunc(path, func(w http.ResponseWriter, r *http.Request) {
		if c.token != "" && r.Header.Get("Authorization") != "Bearer "+c.token {
			http.Error(w, http.StatusText(http.StatusUnauthorized), http.StatusUnauthorized)
			return
		}
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			c.logf("[onebot] 反向 WS 握手失败: %v", err)
			return
		}
		if err := c.serveConn(ctx, conn, fmt.Sprintf("reverse %s", r.RemoteAddr)); err != nil && ctx.Err() == nil && !errors.Is(err, context.Canceled) {
			c.logf("[onebot] 反向 WS 断开: %v", err)
		}
	})

	server := &http.Server{Handler: mux}
	shutdownDone := make(chan struct{})
	go func() {
		select {
		case <-ctx.Done():
			c.closeCurrentConn()
			shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			_ = server.Shutdown(shutdownCtx)
		case <-shutdownDone:
		}
	}()

	err := server.Serve(listener)
	close(shutdownDone)
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
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

func (c *Client) serveConn(ctx context.Context, conn *websocket.Conn, label string) error {
	if ctx == nil {
		ctx = context.Background()
	}
	c.installConn(conn)

	closed := make(chan struct{})
	go func() {
		select {
		case <-ctx.Done():
			c.closeIfCurrent(conn)
		case <-closed:
		}
	}()
	defer func() {
		close(closed)
		c.clearConn(conn)
		_ = conn.Close()
	}()

	c.logf("[onebot] 已连接 %s", label)
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
		c.dispatch(data)
	}
}

func (c *Client) installConn(conn *websocket.Conn) {
	c.writeMu.Lock()
	c.mu.Lock()
	old := c.conn
	c.conn = conn
	c.mu.Unlock()
	if old != nil && old != conn {
		_ = old.Close()
	}
	c.writeMu.Unlock()
}

func (c *Client) clearConn(conn *websocket.Conn) {
	c.writeMu.Lock()
	c.mu.Lock()
	if c.conn == conn {
		c.conn = nil
	}
	c.mu.Unlock()
	c.writeMu.Unlock()
}

func (c *Client) closeIfCurrent(conn *websocket.Conn) {
	c.writeMu.Lock()
	c.mu.Lock()
	current := c.conn == conn
	c.mu.Unlock()
	if current {
		_ = conn.Close()
	}
	c.writeMu.Unlock()
}

func (c *Client) closeCurrentConn() {
	c.writeMu.Lock()
	c.mu.Lock()
	conn := c.conn
	c.conn = nil
	c.mu.Unlock()
	if conn != nil {
		_ = conn.Close()
	}
	c.writeMu.Unlock()
}
