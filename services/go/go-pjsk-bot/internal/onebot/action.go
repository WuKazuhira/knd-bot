package onebot

import (
	"encoding/json"
	"strconv"
)

// ActionRequest 是发往 OneBot 实现的 API 调用请求。
type ActionRequest struct {
	Action string         `json:"action"`
	Params map[string]any `json:"params"`
	Echo   string         `json:"echo,omitempty"`
}

// ForwardNode 是 OneBot 自定义合并转发节点。
type ForwardNode struct {
	Type string          `json:"type"`
	Data ForwardNodeData `json:"data"`
}

// ForwardNodeData 是 node.data 的标准字段。
type ForwardNodeData struct {
	Name    string  `json:"name"`
	UIN     string  `json:"uin"`
	Content Message `json:"content"`
}

// NewForwardNode 构造一个自定义合并转发节点。uin 为 0 时交给 Client 发送前补全。
func NewForwardNode(name string, uin int64, content Message) ForwardNode {
	if content == nil {
		content = Message{}
	}
	node := ForwardNode{
		Type: "node",
		Data: ForwardNodeData{Name: name, Content: content},
	}
	if uin > 0 {
		node.Data.UIN = strconv.FormatInt(uin, 10)
	}
	return node
}

// WithForwardUIN 为节点补充自定义发送者 QQ 号，返回独立副本。
func WithForwardUIN(nodes []ForwardNode, uin int64) []ForwardNode {
	out := append([]ForwardNode(nil), nodes...)
	if uin <= 0 {
		return out
	}
	value := strconv.FormatInt(uin, 10)
	for i := range out {
		if out[i].Type == "" {
			out[i].Type = "node"
		}
		if out[i].Data.UIN == "" {
			out[i].Data.UIN = value
		}
	}
	return out
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

// RecordFile 构造一个语音段。file 可以是本地路径、file:// URL 或可访问的 HTTP URL。
func RecordFile(file string) Segment {
	return Segment{Type: "record", Data: map[string]any{"file": file}}
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

// SendGroupForwardAction 构造群合并转发 action。
func SendGroupForwardAction(groupID int64, nodes []ForwardNode) *ActionRequest {
	return &ActionRequest{
		Action: "send_group_forward_msg",
		Params: map[string]any{"group_id": groupID, "messages": nodes},
	}
}

// SendPrivateForwardAction 构造私聊合并转发 action。
func SendPrivateForwardAction(userID int64, nodes []ForwardNode) *ActionRequest {
	return &ActionRequest{
		Action: "send_private_forward_msg",
		Params: map[string]any{"user_id": userID, "messages": nodes},
	}
}

// SendForwardAction 按来源事件选择群/私聊合并转发 action。
func SendForwardAction(e MessageEvent, nodes []ForwardNode) *ActionRequest {
	if e.IsGroup() {
		return SendGroupForwardAction(e.GroupID, WithForwardUIN(nodes, e.SelfID))
	}
	return SendPrivateForwardAction(e.UserID, WithForwardUIN(nodes, e.SelfID))
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
