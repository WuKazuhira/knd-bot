// Package onebot 提供 OneBot v11 反向 WebSocket 接入所需的最小数据模型。
//
// 只保留 pjsk 业务需要的 message 事件与消息段结构，接入层参考自 Go 分支
// go-kndbot/internal/onebot，但裁剪为 pjsk 专用，避免背上整套 bot 框架。
package onebot

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
)

// ErrUnsupportedEvent 表示收到的事件不是我们关心的类型。
var ErrUnsupportedEvent = errors.New("unsupported OneBot event")

// Segment 是 OneBot v11 的消息段。
type Segment struct {
	Type string         `json:"type"`
	Data map[string]any `json:"data"`
}

// Message 是消息段数组。兼容纯字符串消息（CQ 码上报格式）。
type Message []Segment

// UnmarshalJSON 兼容 message 字段为字符串或段数组两种上报格式。
func (m *Message) UnmarshalJSON(data []byte) error {
	var text string
	if err := json.Unmarshal(data, &text); err == nil {
		*m = Message{{Type: "text", Data: map[string]any{"text": text}}}
		return nil
	}
	var segments []Segment
	if err := json.Unmarshal(data, &segments); err != nil {
		return errors.New("message must be a string or segment array")
	}
	for i := range segments {
		if strings.TrimSpace(segments[i].Type) == "" {
			return fmt.Errorf("message segment %d has no type", i)
		}
		if segments[i].Data == nil {
			segments[i].Data = map[string]any{}
		}
	}
	*m = segments
	return nil
}

// PlainText 返回所有 text 段拼接后的纯文本。
func (m Message) PlainText() string {
	var b strings.Builder
	for _, seg := range m {
		if seg.Type != "text" {
			continue
		}
		if v, ok := seg.Data["text"].(string); ok {
			b.WriteString(v)
		}
	}
	return b.String()
}

// AtTargets 返回消息中所有 at 段指向的 QQ 号（忽略 at 全体）。
func (m Message) AtTargets() []int64 {
	var out []int64
	for _, seg := range m {
		if seg.Type != "at" {
			continue
		}
		v, ok := seg.Data["qq"]
		if !ok {
			continue
		}
		var id int64
		switch t := v.(type) {
		case json.Number:
			id, _ = t.Int64()
		case string:
			if t == "all" {
				continue
			}
			_, _ = fmt.Sscan(t, &id)
		case float64:
			id = int64(t)
		}
		if id > 0 {
			out = append(out, id)
		}
	}
	return out
}

// Sender 是消息发送者信息。
type Sender struct {
	UserID   int64  `json:"user_id"`
	Nickname string `json:"nickname"`
	Card     string `json:"card"`
	Role     string `json:"role"`
}

// MessageEvent 是 OneBot v11 的消息上报事件。
type MessageEvent struct {
	Time        int64   `json:"time"`
	SelfID      int64   `json:"self_id"`
	PostType    string  `json:"post_type"`
	MessageType string  `json:"message_type"`
	SubType     string  `json:"sub_type"`
	MessageID   int64   `json:"message_id"`
	UserID      int64   `json:"user_id"`
	GroupID     int64   `json:"group_id,omitempty"`
	Message     Message `json:"message"`
	RawMessage  string  `json:"raw_message"`
	Sender      Sender  `json:"sender"`
}

// IsGroup 判断是否群消息。
func (e MessageEvent) IsGroup() bool { return e.MessageType == "group" }

// DecodeMessageEvent 从原始 JSON 解出 message 事件；非 message 事件返回 ErrUnsupportedEvent。
func DecodeMessageEvent(data []byte) (MessageEvent, error) {
	var envelope struct {
		PostType string `json:"post_type"`
	}
	if err := json.Unmarshal(data, &envelope); err != nil {
		return MessageEvent{}, fmt.Errorf("decode OneBot envelope: %w", err)
	}
	if envelope.PostType != "message" {
		return MessageEvent{}, fmt.Errorf("%w: post_type=%q", ErrUnsupportedEvent, envelope.PostType)
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var event MessageEvent
	if err := decoder.Decode(&event); err != nil {
		return MessageEvent{}, fmt.Errorf("decode OneBot message event: %w", err)
	}
	if event.SelfID <= 0 || event.UserID <= 0 || event.MessageID == 0 {
		return MessageEvent{}, errors.New("self_id, user_id, and message_id are required")
	}
	if event.MessageType != "private" && event.MessageType != "group" {
		return MessageEvent{}, fmt.Errorf("unsupported message_type %q", event.MessageType)
	}
	if event.MessageType == "group" && event.GroupID <= 0 {
		return MessageEvent{}, errors.New("group_id is required for group messages")
	}
	return event, nil
}

// FileInfo 是 OneBot offline_file 等通知中的文件信息。
type FileInfo struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	Size int64  `json:"size"`
	URL  string `json:"url"`
}

// NoticeEvent 是 OneBot notice 上报事件的最小公共模型。
type NoticeEvent struct {
	Time       int64    `json:"time"`
	SelfID     int64    `json:"self_id"`
	PostType   string   `json:"post_type"`
	NoticeType string   `json:"notice_type"`
	UserID     int64    `json:"user_id"`
	GroupID    int64    `json:"group_id,omitempty"`
	SubType    string   `json:"sub_type,omitempty"`
	File       FileInfo `json:"file,omitempty"`
}

// DecodeNoticeEvent 解码 OneBot notice；非 notice 事件返回 ErrUnsupportedEvent。
func DecodeNoticeEvent(data []byte) (NoticeEvent, error) {
	var envelope struct {
		PostType string `json:"post_type"`
	}
	if err := json.Unmarshal(data, &envelope); err != nil {
		return NoticeEvent{}, fmt.Errorf("decode OneBot envelope: %w", err)
	}
	if envelope.PostType != "notice" {
		return NoticeEvent{}, fmt.Errorf("%w: post_type=%q", ErrUnsupportedEvent, envelope.PostType)
	}
	var event NoticeEvent
	if err := json.Unmarshal(data, &event); err != nil {
		return NoticeEvent{}, fmt.Errorf("decode OneBot notice event: %w", err)
	}
	if event.SelfID <= 0 || event.NoticeType == "" {
		return NoticeEvent{}, errors.New("self_id and notice_type are required")
	}
	return event, nil
}
