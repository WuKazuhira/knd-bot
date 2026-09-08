package onebot

import (
	"context"
	"fmt"
	"net/http"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

// Handler 处理一条 message 事件，返回需要发送的 action（可为 nil 表示不处理）。
type Handler func(MessageEvent) *ActionRequest

// Client 连接 OneBot 实现的正向 WebSocket，收事件、发 action。
type Client struct {
	url     string
	token   string
	handler Handler
	logf    func(string, ...any)

	mu   sync.Mutex
	conn *websocket.Conn
}

// NewClient 创建 WS 客户端。url 形如 ws://napcat:3001；token 为 access_token（可空）。
func NewClient(url, token string, handler Handler, logf func(string, ...any)) *Client {
	if logf == nil {
		logf = func(string, ...any) {}
	}
	return &Client{url: url, token: token, handler: handler, logf: logf}
}

// Run 持续连接并处理事件，断线自动重连，直到 ctx 取消。
func (c *Client) Run(ctx context.Context) error {
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		if err := c.connectAndServe(ctx); err != nil {
			c.logf("[onebot] 连接断开: %v，3s 后重连", err)
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(3 * time.Second):
			}
		}
	}
}

func (c *Client) connectAndServe(ctx context.Context) error {
	header := http.Header{}
	if c.token != "" {
		header.Set("Authorization", "Bearer "+c.token)
	}
	conn, _, err := websocket.DefaultDialer.DialContext(ctx, c.url, header)
	if err != nil {
		return fmt.Errorf("dial %s: %w", c.url, err)
	}
	c.mu.Lock()
	c.conn = conn
	c.mu.Unlock()
	defer func() {
		c.mu.Lock()
		if c.conn == conn {
			c.conn = nil
		}
		c.mu.Unlock()
		_ = conn.Close()
	}()
	c.logf("[onebot] 已连接 %s", c.url)

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		_, data, err := conn.ReadMessage()
		if err != nil {
			return fmt.Errorf("read: %w", err)
		}
		c.dispatch(data)
	}
}

func (c *Client) dispatch(data []byte) {
	event, err := DecodeMessageEvent(data)
	if err != nil {
		return // 非 message 事件或解析失败：忽略（pjsk 只关心消息）
	}
	if c.handler == nil {
		return
	}
	defer func() {
		if r := recover(); r != nil {
			c.logf("[onebot] handler panic: %v", r)
		}
	}()
	if action := c.handler(event); action != nil {
		if err := c.send(action); err != nil {
			c.logf("[onebot] 发送 action 失败: %v", err)
		}
	}
}

func (c *Client) send(action *ActionRequest) error {
	payload, err := action.Marshal()
	if err != nil {
		return err
	}
	c.mu.Lock()
	conn := c.conn
	c.mu.Unlock()
	if conn == nil {
		return fmt.Errorf("connection not established")
	}
	return conn.WriteMessage(websocket.TextMessage, payload)
}
