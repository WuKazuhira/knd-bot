package onebot

import (
	"encoding/json"
	"errors"
	"testing"
)

func TestMessageUnmarshalString(t *testing.T) {
	var m Message
	if err := json.Unmarshal([]byte(`"hello"`), &m); err != nil {
		t.Fatalf("unmarshal string: %v", err)
	}
	if len(m) != 1 || m[0].Type != "text" || m[0].Data["text"] != "hello" {
		t.Errorf("字符串消息解析错误: %+v", m)
	}
}

func TestMessageUnmarshalSegments(t *testing.T) {
	var m Message
	raw := `[{"type":"text","data":{"text":"hi"}},{"type":"at","data":{"qq":"123"}}]`
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		t.Fatalf("unmarshal segments: %v", err)
	}
	if len(m) != 2 || m[0].Type != "text" || m[1].Type != "at" {
		t.Errorf("段数组解析错误: %+v", m)
	}
}

func TestMessageUnmarshalNoType(t *testing.T) {
	var m Message
	err := json.Unmarshal([]byte(`[{"data":{"text":"x"}}]`), &m)
	if err == nil {
		t.Error("缺 type 的段应报错")
	}
}

func TestPlainText(t *testing.T) {
	m := Message{
		Text("hello "),
		At(123),
		Text("world"),
	}
	if got := m.PlainText(); got != "hello world" {
		t.Errorf("PlainText=%q", got)
	}
}

func TestAtTargets(t *testing.T) {
	// json.Number（来自 UseNumber 解码）
	m := Message{
		{Type: "at", Data: map[string]any{"qq": json.Number("111")}},
		{Type: "at", Data: map[string]any{"qq": "222"}},
		{Type: "at", Data: map[string]any{"qq": "all"}}, // 全体，忽略
		{Type: "at", Data: map[string]any{"qq": float64(333)}},
		{Type: "text", Data: map[string]any{"text": "x"}},
	}
	got := m.AtTargets()
	want := []int64{111, 222, 333}
	if len(got) != len(want) {
		t.Fatalf("AtTargets=%v want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("AtTargets[%d]=%d want %d", i, got[i], want[i])
		}
	}
}

func TestDecodeMessageEventGroup(t *testing.T) {
	raw := `{"post_type":"message","message_type":"group","self_id":10,"user_id":20,"group_id":30,"message_id":40,"message":"/sk 100","sender":{"role":"admin"}}`
	ev, err := DecodeMessageEvent([]byte(raw))
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if !ev.IsGroup() || ev.GroupID != 30 || ev.UserID != 20 {
		t.Errorf("群事件解析错误: %+v", ev)
	}
	if ev.Message.PlainText() != "/sk 100" {
		t.Errorf("PlainText=%q", ev.Message.PlainText())
	}
	if ev.Sender.Role != "admin" {
		t.Errorf("Sender.Role=%q", ev.Sender.Role)
	}
}

func TestDecodeMessageEventNonMessage(t *testing.T) {
	_, err := DecodeMessageEvent([]byte(`{"post_type":"notice"}`))
	if !errors.Is(err, ErrUnsupportedEvent) {
		t.Errorf("非 message 事件应返回 ErrUnsupportedEvent, got %v", err)
	}
}

func TestDecodeMessageEventMissingFields(t *testing.T) {
	// 缺 message_id
	_, err := DecodeMessageEvent([]byte(`{"post_type":"message","message_type":"private","self_id":1,"user_id":2,"message":"x"}`))
	if err == nil {
		t.Error("缺 message_id 应报错")
	}
	// 群消息缺 group_id
	_, err = DecodeMessageEvent([]byte(`{"post_type":"message","message_type":"group","self_id":1,"user_id":2,"message_id":3,"message":"x"}`))
	if err == nil {
		t.Error("群消息缺 group_id 应报错")
	}
}

func TestSendMessageActionGroupVsPrivate(t *testing.T) {
	group := MessageEvent{MessageType: "group", GroupID: 30, UserID: 20}
	a := SendMessageAction(group, Message{Text("hi")})
	if a.Action != "send_msg" || a.Params["message_type"] != "group" || a.Params["group_id"] != int64(30) {
		t.Errorf("群 action 错误: %+v", a.Params)
	}
	priv := MessageEvent{MessageType: "private", UserID: 20}
	a = SendMessageAction(priv, Message{Text("hi")})
	if a.Params["message_type"] != "private" || a.Params["user_id"] != int64(20) {
		t.Errorf("私聊 action 错误: %+v", a.Params)
	}
}

func TestReplyTextAtSender(t *testing.T) {
	group := MessageEvent{MessageType: "group", GroupID: 30, UserID: 20}
	a := ReplyText(group, "hello", true)
	msg := a.Params["message"].(Message)
	if len(msg) != 2 || msg[0].Type != "at" || msg[1].Type != "text" {
		t.Errorf("群 at_sender 回复应为 [at,text]: %+v", msg)
	}
	// 私聊不 at
	priv := MessageEvent{MessageType: "private", UserID: 20}
	a = ReplyText(priv, "hello", true)
	msg = a.Params["message"].(Message)
	if len(msg) != 1 || msg[0].Type != "text" {
		t.Errorf("私聊不应 at: %+v", msg)
	}
}

func TestImageBytesAndMarshal(t *testing.T) {
	seg := ImageBytes("QUJD")
	if seg.Type != "image" || seg.Data["file"] != "base64://QUJD" {
		t.Errorf("ImageBytes 错误: %+v", seg)
	}
	a := ReplyImage(MessageEvent{MessageType: "private", UserID: 1}, "QUJD")
	data, err := a.Marshal()
	if err != nil {
		t.Fatalf("Marshal: %v", err)
	}
	if len(data) == 0 {
		t.Error("Marshal 结果为空")
	}
}
