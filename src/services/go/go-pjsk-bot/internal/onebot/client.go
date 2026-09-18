package onebot

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

// Handler 处理一条 message 事件，返回需要发送的 action（可为 nil 表示不处理）。
type Handler func(MessageEvent) *ActionRequest

// NoticeHandler 处理 notice 事件（例如 offline_file），返回可选 action。
type NoticeHandler func(NoticeEvent) *ActionRequest

// ActionSender 用于业务模块在处理事件之外主动发送 OneBot action。
// 例如有状态会话的超时结算可通过该接口向群里推送消息。
type ActionSender interface {
	Send(*ActionRequest) error
}

// ActionSenderFunc 将函数适配为 ActionSender。
type ActionSenderFunc func(*ActionRequest) error

func (f ActionSenderFunc) Send(action *ActionRequest) error { return f(action) }

var (
	// ErrNotConnected 表示当前没有可用的 OneBot WebSocket 连接。
	ErrNotConnected = errors.New("onebot connection not established")
	// ErrInvalidMessageType 表示主动发送的 message_type 不是 group/private。
	ErrInvalidMessageType = errors.New("invalid OneBot message_type")
	// ErrInvalidTarget 表示主动发送的目标 QQ/群号无效。
	ErrInvalidTarget = errors.New("invalid OneBot message target")
)

const (
	eventDedupTTL   = 10 * time.Minute
	eventDedupLimit = 4096
)

type eventKey struct {
	selfID    int64
	messageID int64
}

type seenEvent struct {
	key eventKey
	at  time.Time
}

type ConnectionStatus struct {
	Connected   bool      `json:"connected"`
	SelfID      int64     `json:"self_id,omitempty"`
	ConnectedAt time.Time `json:"connected_at,omitempty"`
	LastEventAt time.Time `json:"last_event_at,omitempty"`
	QueueDepth  int64     `json:"queue_depth"`
}

// Client 负责 OneBot WebSocket 的事件分发与 action 发送。
// 可由 Run 主动连接正向 WS，也可由 Serve 接受 OneBotFilter 的反向 WS。
type Client struct {
	url           string
	token         string
	handler       Handler
	noticeHandler NoticeHandler
	logf          func(string, ...any)
	logMessages   bool

	mu          sync.Mutex
	conn        *websocket.Conn
	selfID      int64
	connectedAt time.Time
	lastEventAt time.Time
	seenEvents  map[eventKey]time.Time
	seenOrder   []seenEvent
	writeMu     sync.Mutex // gorilla/websocket 只允许一个并发 writer。
	seq         uint64
	queueDepth  atomic.Int64
	pending     map[string]chan apiResponse
}

// NewClient 创建 WS 客户端。url 形如 ws://napcat:3001；token 为 access_token（可空）。
func NewClient(url, token string, handler Handler, logf func(string, ...any)) *Client {
	return NewClientWithNotice(url, token, handler, nil, logf)
}

// NewClientWithNotice 创建同时处理 message/notice 事件的 OneBot 客户端。
func NewClientWithNotice(url, token string, handler Handler, noticeHandler NoticeHandler, logf func(string, ...any)) *Client {
	if logf == nil {
		logf = func(string, ...any) {}
	}
	return &Client{
		url:           url,
		token:         token,
		handler:       handler,
		noticeHandler: noticeHandler,
		logf:          logf,
		pending:       make(map[string]chan apiResponse),
		seenEvents:    make(map[eventKey]time.Time),
	}
}

// SetLogMessages 控制是否记录未命中普通消息的截断文本。
// 命中/疑似命令和处理结果不受该开关影响。
func (c *Client) SetLogMessages(enabled bool) {
	c.mu.Lock()
	c.logMessages = enabled
	c.mu.Unlock()
}

func (c *Client) shouldLogMessages() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.logMessages
}

func (c *Client) setSelfID(selfID int64) {
	if selfID <= 0 {
		return
	}
	c.mu.Lock()
	c.selfID = selfID
	c.mu.Unlock()
}

func (c *Client) cachedSelfID() int64 {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.selfID
}

func (c *Client) ConnectionStatus() ConnectionStatus {
	c.mu.Lock()
	defer c.mu.Unlock()
	return ConnectionStatus{
		Connected:   c.conn != nil,
		SelfID:      c.selfID,
		ConnectedAt: c.connectedAt,
		LastEventAt: c.lastEventAt,
		QueueDepth:  c.queueDepth.Load(),
	}
}

func (c *Client) isCurrentConn(conn *websocket.Conn) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.conn == conn
}

func (c *Client) acceptMessageEvent(event MessageEvent, now time.Time) bool {
	key := eventKey{selfID: event.SelfID, messageID: event.MessageID}
	cutoff := now.Add(-eventDedupTTL)
	c.mu.Lock()
	defer c.mu.Unlock()
	for len(c.seenOrder) > 0 && (c.seenOrder[0].at.Before(cutoff) || len(c.seenEvents) > eventDedupLimit) {
		old := c.seenOrder[0]
		c.seenOrder[0] = seenEvent{}
		c.seenOrder = c.seenOrder[1:]
		if at, ok := c.seenEvents[old.key]; ok && at.Equal(old.at) {
			delete(c.seenEvents, old.key)
		}
	}
	if at, ok := c.seenEvents[key]; ok && now.Sub(at) <= eventDedupTTL {
		return false
	}
	c.seenEvents[key] = now
	c.seenOrder = append(c.seenOrder, seenEvent{key: key, at: now})
	c.lastEventAt = now
	return true
}

func (c *Client) markEvent(now time.Time) {
	c.mu.Lock()
	c.lastEventAt = now
	c.mu.Unlock()
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
	return c.serveConn(ctx, conn, c.url)
}

type apiResponse struct {
	Status  string          `json:"status"`
	Retcode int             `json:"retcode"`
	Data    json.RawMessage `json:"data"`
	Message string          `json:"message"`
}

func (c *Client) dispatch(data []byte) {
	var envelope struct {
		PostType string `json:"post_type"`
		Echo     string `json:"echo"`
		SelfID   int64  `json:"self_id"`
	}
	if err := json.Unmarshal(data, &envelope); err != nil {
		return
	}
	c.setSelfID(envelope.SelfID)
	if envelope.Echo != "" {
		c.dispatchResponse(data, envelope.Echo)
		return
	}
	c.dispatchEvent(data, envelope.PostType)
}

func (c *Client) dispatchResponse(data []byte, echo string) {
	var response apiResponse
	if json.Unmarshal(data, &response) != nil {
		return
	}
	c.mu.Lock()
	ch := c.pending[echo]
	if ch != nil {
		delete(c.pending, echo)
	}
	c.mu.Unlock()
	if ch != nil {
		c.logf("[onebot] API response echo=%s status=%s retcode=%d", echo, response.Status, response.Retcode)
		ch <- response
	}
}

func (c *Client) dispatchEvent(data []byte, postType string) {
	defer func() {
		if r := recover(); r != nil {
			c.logf("[onebot] handler panic: %v", r)
		}
	}()
	if postType == "notice" {
		if c.noticeHandler == nil {
			return
		}
		event, err := DecodeNoticeEvent(data)
		if err != nil {
			c.logf("[onebot] notice 解码失败: %v", err)
			return
		}
		started := time.Now()
		c.markEvent(started)
		action := c.noticeHandler(event)
		c.logf("[onebot] notice type=%s user=%d group=%d handled=%t elapsed=%s", event.NoticeType, event.UserID, event.GroupID, action != nil, time.Since(started).Round(time.Millisecond))
		if action != nil {
			if err := c.send(action); err != nil {
				c.logf("[onebot] 发送 notice action 失败: %v", err)
			}
		}
		return
	}
	if postType != "message" || c.handler == nil {
		return
	}
	event, err := DecodeMessageEvent(data)
	if err != nil {
		c.logf("[onebot] message 解码失败: %v", err)
		return
	}
	started := time.Now()
	if !c.acceptMessageEvent(event, started) {
		c.logf("[onebot] duplicate message ignored self=%d message_id=%d", event.SelfID, event.MessageID)
		return
	}
	action := c.handler(event)
	if action != nil {
		c.logf("[onebot] message handled %s elapsed=%s %s", EventSummary(event, true), time.Since(started).Round(time.Millisecond), ActionSummary(action))
		if err := c.send(action); err != nil {
			c.logf("[onebot] 发送 action 失败: %v", err)
		}
	} else if c.shouldLogMessages() {
		c.logf("[onebot] message ignored %s elapsed=%s", EventSummary(event, true), time.Since(started).Round(time.Millisecond))
	}
}

// SendContext 线程安全地发送任意 OneBot action，供后台任务使用。
// ctx 只负责在发送前取消；WebSocket 写入本身由单独的 writer 锁保护。
func (c *Client) SendContext(ctx context.Context, action *ActionRequest) error {
	if action == nil {
		return errors.New("nil OneBot action")
	}
	if ctx == nil {
		ctx = context.Background()
	}
	select {
	case <-ctx.Done():
		return ctx.Err()
	default:
	}
	payload, err := action.Marshal()
	if err != nil {
		return err
	}

	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	select {
	case <-ctx.Done():
		return ctx.Err()
	default:
	}
	c.mu.Lock()
	conn := c.conn
	c.mu.Unlock()
	if conn == nil {
		return ErrNotConnected
	}
	if err := conn.WriteMessage(websocket.TextMessage, payload); err != nil {
		return fmt.Errorf("write OneBot action: %w", err)
	}
	c.logf("[onebot] action sent %s", ActionSummary(action))
	return nil
}

// Send 主动发送一条 action；事件 handler 与后台任务共用同一串行写路径。
func (c *Client) Send(action *ActionRequest) error {
	return c.SendContext(context.Background(), action)
}

// Call 调用 OneBot API 并等待响应，供自动治理类后台任务读取群组/成员列表。
func (c *Client) Call(ctx context.Context, action string, params map[string]any) (json.RawMessage, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	started := time.Now()
	echo := fmt.Sprintf("kndbot-%d", atomic.AddUint64(&c.seq, 1))
	c.logf("[onebot] API call action=%s echo=%s", action, echo)
	ch := make(chan apiResponse, 1)
	c.mu.Lock()
	if c.pending == nil {
		c.pending = make(map[string]chan apiResponse)
	}
	c.pending[echo] = ch
	c.mu.Unlock()
	defer func() { c.mu.Lock(); delete(c.pending, echo); c.mu.Unlock() }()
	payload, err := json.Marshal(&ActionRequest{Action: action, Params: params, Echo: echo})
	if err != nil {
		return nil, err
	}
	c.writeMu.Lock()
	c.mu.Lock()
	conn := c.conn
	c.mu.Unlock()
	if conn == nil {
		c.writeMu.Unlock()
		return nil, ErrNotConnected
	}
	err = conn.WriteMessage(websocket.TextMessage, payload)
	c.writeMu.Unlock()
	if err != nil {
		return nil, fmt.Errorf("write OneBot action: %w", err)
	}
	select {
	case response := <-ch:
		if response.Retcode != 0 || response.Status == "failed" {
			err := fmt.Errorf("OneBot %s failed: %s", action, response.Message)
			c.logf("[onebot] API failed action=%s echo=%s elapsed=%s err=%v", action, echo, time.Since(started).Round(time.Millisecond), err)
			return nil, err
		}
		c.logf("[onebot] API done action=%s echo=%s elapsed=%s", action, echo, time.Since(started).Round(time.Millisecond))
		return response.Data, nil
	case <-ctx.Done():
		c.logf("[onebot] API canceled action=%s echo=%s elapsed=%s err=%v", action, echo, time.Since(started).Round(time.Millisecond), ctx.Err())
		return nil, ctx.Err()
	}
}

func (c *Client) send(action *ActionRequest) error {
	return c.Send(action)
}

// SendMessageContext 按 message_type 和目标 ID 主动发送消息。
// messageType 目前支持 group（targetID=group_id）和 private（targetID=user_id）。
func (c *Client) SendMessageContext(ctx context.Context, messageType string, targetID int64, msg Message) error {
	if targetID <= 0 {
		return ErrInvalidTarget
	}
	params := map[string]any{
		"message_type": messageType,
		"message":      msg,
	}
	switch messageType {
	case "group":
		params["group_id"] = targetID
	case "private":
		params["user_id"] = targetID
	default:
		return ErrInvalidMessageType
	}
	return c.SendContext(ctx, &ActionRequest{Action: "send_msg", Params: params})
}

// SendMessage 是 SendMessageContext 的无 context 便捷形式。
func (c *Client) SendMessage(messageType string, targetID int64, msg Message) error {
	return c.SendMessageContext(context.Background(), messageType, targetID, msg)
}

// SendGroupMessage 主动发送群消息。
func (c *Client) SendGroupMessage(groupID int64, msg Message) error {
	return c.SendMessage("group", groupID, msg)
}

// SendGroupMessageContext 主动发送群消息，并支持取消。
func (c *Client) SendGroupMessageContext(ctx context.Context, groupID int64, msg Message) error {
	return c.SendMessageContext(ctx, "group", groupID, msg)
}

// SendPrivateMessage 主动发送私聊消息。
func (c *Client) SendPrivateMessage(userID int64, msg Message) error {
	return c.SendMessage("private", userID, msg)
}

// SendPrivateMessageContext 主动发送私聊消息，并支持取消。
func (c *Client) SendPrivateMessageContext(ctx context.Context, userID int64, msg Message) error {
	return c.SendMessageContext(ctx, "private", userID, msg)
}

// SendGroupForwardMessage 主动发送群合并转发。
func (c *Client) SendGroupForwardMessage(groupID int64, nodes []ForwardNode) error {
	return c.SendGroupForwardMessageContext(context.Background(), groupID, nodes)
}

// SendGroupForwardMessageContext 主动发送群合并转发，并支持取消。
func (c *Client) SendGroupForwardMessageContext(ctx context.Context, groupID int64, nodes []ForwardNode) error {
	if groupID <= 0 {
		return ErrInvalidTarget
	}
	prepared, err := c.prepareForwardNodes(ctx, nodes)
	if err != nil {
		return err
	}
	return c.SendContext(ctx, SendGroupForwardAction(groupID, prepared))
}

// SendPrivateForwardMessage 主动发送私聊合并转发。
func (c *Client) SendPrivateForwardMessage(userID int64, nodes []ForwardNode) error {
	return c.SendPrivateForwardMessageContext(context.Background(), userID, nodes)
}

// SendPrivateForwardMessageContext 主动发送私聊合并转发，并支持取消。
func (c *Client) SendPrivateForwardMessageContext(ctx context.Context, userID int64, nodes []ForwardNode) error {
	if userID <= 0 {
		return ErrInvalidTarget
	}
	prepared, err := c.prepareForwardNodes(ctx, nodes)
	if err != nil {
		return err
	}
	return c.SendContext(ctx, SendPrivateForwardAction(userID, prepared))
}

func (c *Client) prepareForwardNodes(ctx context.Context, nodes []ForwardNode) ([]ForwardNode, error) {
	if len(nodes) == 0 {
		return nil, errors.New("empty forward nodes")
	}
	if ctx == nil {
		ctx = context.Background()
	}
	uin := c.cachedSelfID()
	if uin <= 0 {
		data, err := c.Call(ctx, "get_login_info", nil)
		if err != nil {
			return nil, fmt.Errorf("resolve OneBot self_id: %w", err)
		}
		var info struct {
			UserID int64 `json:"user_id"`
		}
		if err := json.Unmarshal(data, &info); err != nil || info.UserID <= 0 {
			return nil, fmt.Errorf("get_login_info returned invalid user_id")
		}
		c.setSelfID(info.UserID)
		uin = info.UserID
	}
	return WithForwardUIN(nodes, uin), nil
}
