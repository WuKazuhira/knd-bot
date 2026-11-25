package onebot

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
)

var ErrUnsupportedEvent = errors.New("unsupported OneBot event")

type Segment struct {
	Type string         `json:"type"`
	Data map[string]any `json:"data"`
}

type Message []Segment

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

type Sender struct {
	UserID   int64  `json:"user_id"`
	Nickname string `json:"nickname"`
	Card     string `json:"card"`
	Role     string `json:"role"`
}

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

func (e MessageEvent) IsGroup() bool { return e.MessageType == "group" }

func DecodeMessageEvent(data []byte) (MessageEvent, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var event MessageEvent
	if err := decoder.Decode(&event); err != nil {
		return MessageEvent{}, fmt.Errorf("decode OneBot message event: %w", err)
	}
	var trailing json.RawMessage
	if err := decoder.Decode(&trailing); err != io.EOF {
		if err != nil {
			return MessageEvent{}, fmt.Errorf("decode OneBot message event trailing data: %w", err)
		}
		return MessageEvent{}, errors.New("decode OneBot message event: multiple JSON values")
	}
	if event.PostType != "message" {
		return MessageEvent{}, fmt.Errorf("%w: post_type=%q", ErrUnsupportedEvent, event.PostType)
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

type FileInfo struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	Size int64  `json:"size"`
	URL  string `json:"url"`
}

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
