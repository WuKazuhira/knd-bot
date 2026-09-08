package onebot

import "encoding/json"

// ActionRequest 是发往 OneBot 实现的 API 调用请求。
type ActionRequest struct {
	Action string         `json:"action"`
	Params map[string]any `json:"params"`
	Echo   string         `json:"echo,omitempty"`
}

// Text 构造一个纯文本段。
func Text(text string) Segment {
	return Segment{Type: "text", Data: map[string]any{"text": text}}
}

// At 构造一个 at 段。
func At(userID int64) Segment {
	return Segment{Type: "at", Data: map[string]any{"qq": userID}}
}

// ImageBytes 构造一个 base64 图片段。
func ImageBytes(b64 string) Segment {
	return Segment{Type: "image", Data: map[string]any{"file": "base64://" + b64}}
}

// Reply 构造一个回复段。
func Reply(messageID int64) Segment {
	return Segment{Type: "reply", Data: map[string]any{"id": messageID}}
}

// SendMessageAction 根据来源事件构造回复 action（自动区分群/私聊）。
func SendMessageAction(e MessageEvent, msg Message) *ActionRequest {
	params := map[string]any{"message": msg}
	if e.IsGroup() {
		params["message_type"] = "group"
		params["group_id"] = e.GroupID
	} else {
		params["message_type"] = "private"
		params["user_id"] = e.UserID
	}
	return &ActionRequest{Action: "send_msg", Params: params}
}

// ReplyText 构造一条文本回复；atSender=true 时在群里 @ 发送者（对齐 nonebot at_sender）。
func ReplyText(e MessageEvent, text string, atSender bool) *ActionRequest {
	var msg Message
	if atSender && e.IsGroup() {
		msg = Message{At(e.UserID), Text(" " + text)}
	} else {
		msg = Message{Text(text)}
	}
	return SendMessageAction(e, msg)
}

// ReplyImage 构造一条图片回复（图片字节以 base64 发送）。
func ReplyImage(e MessageEvent, b64 string) *ActionRequest {
	return SendMessageAction(e, Message{ImageBytes(b64)})
}

// Marshal 序列化 action 为 JSON。
func (a *ActionRequest) Marshal() ([]byte, error) { return json.Marshal(a) }
