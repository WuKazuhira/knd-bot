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

type Handler func(MessageEvent) *ActionRequest

type NoticeHandler func(NoticeEvent) *ActionRequest

type ActionSender interface {
	Send(*ActionRequest) error
}

type ActionSenderFunc func(*ActionRequest) error

func (f ActionSenderFunc) Send(action *ActionRequest) error { return f(action) }

var (
	ErrNotConnected       = errors.New("onebot connection not established")
	ErrInvalidMessageType = errors.New("invalid OneBot message_type")
	ErrInvalidTarget      = errors.New("invalid OneBot message target")
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

type Client struct {
	url                         string
	token                       string
	handler                     Handler
	noticeHandler               NoticeHandler
	logf                        func(string, ...any)
	logMessages                 bool
	allowUnauthenticatedReverse bool

	mu          sync.Mutex
	conn        *websocket.Conn
	selfID      int64
	connectedAt time.Time
	lastEventAt time.Time
	seenEvents  map[eventKey]time.Time
	seenOrder   []seenEvent
	writer      chan struct{} // gorilla/websocket 只允许单写
	seq         uint64
	queueDepth  atomic.Int64
	pending     map[string]chan apiResponse
}

func NewClient(url, token string, handler Handler, logf func(string, ...any)) *Client {
	return NewClientWithNotice(url, token, handler, nil, logf)
}

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
		writer:        make(chan struct{}, 1),
		seenEvents:    make(map[eventKey]time.Time),
	}
}

func (c *Client) SetLogMessages(enabled bool) {
	c.mu.Lock()
	c.logMessages = enabled
	c.mu.Unlock()
}

func (c *Client) SetAllowUnauthenticatedReverse(enabled bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.allowUnauthenticatedReverse = enabled
}

func (c *Client) shouldLogMessages() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.logMessages
}

func (c *Client) setSelfID(conn *websocket.Conn, selfID int64) {
	if selfID <= 0 {
		return
	}
	c.mu.Lock()
	if conn == nil || c.conn == conn {
		c.selfID = selfID
	}
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
	err     error
	Status  string          `json:"status"`
	Retcode int             `json:"retcode"`
	Data    json.RawMessage `json:"data"`
	Message string          `json:"message"`
}

func (c *Client) dispatchResponse(conn *websocket.Conn, data []byte, echo string) {
	var response apiResponse
	if json.Unmarshal(data, &response) != nil {
		return
	}
	c.mu.Lock()
	if c.conn != conn {
		c.mu.Unlock()
		return
	}
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

func (c *Client) dispatchEvent(ctx context.Context, conn *websocket.Conn, data []byte, postType string) {
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
			if err := c.sendContext(ctx, conn, action); err != nil {
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
		if err := c.sendContext(ctx, conn, action); err != nil {
			c.logf("[onebot] 发送 action 失败: %v", err)
		}
	} else if c.shouldLogMessages() {
		c.logf("[onebot] message ignored %s elapsed=%s", EventSummary(event, true), time.Since(started).Round(time.Millisecond))
	}
}

func (c *Client) SendContext(ctx context.Context, action *ActionRequest) error {
	return c.sendContext(ctx, nil, action)
}

func (c *Client) sendContext(ctx context.Context, source *websocket.Conn, action *ActionRequest) error {
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

	if err := c.acquireWriter(ctx); err != nil {
		return err
	}
	c.mu.Lock()
	conn := c.conn
	c.mu.Unlock()
	if conn == nil || (source != nil && source != conn) {
		c.releaseWriter()
		return ErrNotConnected
	}
	err = writeAction(ctx, conn, payload)
	c.releaseWriter()
	if err != nil {
		return fmt.Errorf("write OneBot action: %w", err)
	}
	c.logf("[onebot] action sent %s", ActionSummary(action))
	return nil
}

func (c *Client) acquireWriter(ctx context.Context) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	case c.writer <- struct{}{}:
		if err := ctx.Err(); err != nil {
			c.releaseWriter()
			return err
		}
		return nil
	}
}

func (c *Client) releaseWriter() { <-c.writer }

func writeAction(ctx context.Context, conn *websocket.Conn, payload []byte) error {
	deadline := time.Now().Add(10 * time.Second)
	if earlier, ok := ctx.Deadline(); ok && earlier.Before(deadline) {
		deadline = earlier
	}
	if err := conn.SetWriteDeadline(deadline); err != nil {
		return err
	}
	return conn.WriteMessage(websocket.TextMessage, payload)
}

func (c *Client) Send(action *ActionRequest) error {
	return c.SendContext(context.Background(), action)
}

func (c *Client) Call(ctx context.Context, action string, params map[string]any) (json.RawMessage, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	if _, ok := ctx.Deadline(); !ok {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, 30*time.Second)
		defer cancel()
	}
	started := time.Now()
	echo := fmt.Sprintf("kndbot-%d", atomic.AddUint64(&c.seq, 1))
	c.logf("[onebot] API call action=%s echo=%s", action, echo)
	payload, err := json.Marshal(&ActionRequest{Action: action, Params: params, Echo: echo})
	if err != nil {
		return nil, err
	}
	if err := c.acquireWriter(ctx); err != nil {
		return nil, err
	}
	ch := make(chan apiResponse, 1)
	c.mu.Lock()
	conn := c.conn
	if conn == nil {
		c.mu.Unlock()
		c.releaseWriter()
		return nil, ErrNotConnected
	}
	c.pending[echo] = ch
	c.mu.Unlock()

	defer func() {
		c.mu.Lock()
		delete(c.pending, echo)
		c.mu.Unlock()
	}()
	err = writeAction(ctx, conn, payload)
	c.releaseWriter()
	if err != nil {
		return nil, fmt.Errorf("write OneBot action: %w", err)
	}
	select {
	case response := <-ch:
		if response.err != nil {
			return nil, response.err
		}
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

func (c *Client) SendMessage(messageType string, targetID int64, msg Message) error {
	return c.SendMessageContext(context.Background(), messageType, targetID, msg)
}

func (c *Client) SendGroupMessage(groupID int64, msg Message) error {
	return c.SendMessage("group", groupID, msg)
}

func (c *Client) SendGroupMessageContext(ctx context.Context, groupID int64, msg Message) error {
	return c.SendMessageContext(ctx, "group", groupID, msg)
}

func (c *Client) SendPrivateMessage(userID int64, msg Message) error {
	return c.SendMessage("private", userID, msg)
}

func (c *Client) SendPrivateMessageContext(ctx context.Context, userID int64, msg Message) error {
	return c.SendMessageContext(ctx, "private", userID, msg)
}

func (c *Client) SendGroupForwardMessage(groupID int64, nodes []ForwardNode) error {
	return c.SendGroupForwardMessageContext(context.Background(), groupID, nodes)
}

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

func (c *Client) SendPrivateForwardMessage(userID int64, nodes []ForwardNode) error {
	return c.SendPrivateForwardMessageContext(context.Background(), userID, nodes)
}

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
		c.setSelfID(nil, info.UserID)
		uin = info.UserID
	}
	return WithForwardUIN(nodes, uin), nil
}
