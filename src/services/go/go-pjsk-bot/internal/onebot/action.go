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

// Marshal 序列化 action 为 JSON。
func (a *ActionRequest) Marshal() ([]byte, error) { return json.Marshal(a) }
