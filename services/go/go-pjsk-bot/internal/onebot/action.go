package onebot

import (
	"encoding/json"
	"strconv"
)

type ActionRequest struct {
	Action string         `json:"action"`
	Params map[string]any `json:"params"`
	Echo   string         `json:"echo,omitempty"`
}

type ForwardNode struct {
	Type string          `json:"type"`
	Data ForwardNodeData `json:"data"`
}

type ForwardNodeData struct {
	Name    string  `json:"name"`
	UIN     string  `json:"uin"`
	Content Message `json:"content"`
}

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

func Text(text string) Segment {
	return Segment{Type: "text", Data: map[string]any{"text": text}}
}

func At(userID int64) Segment {
	return Segment{Type: "at", Data: map[string]any{"qq": userID}}
}

func ImageBytes(b64 string) Segment {
	return Segment{Type: "image", Data: map[string]any{"file": "base64://" + b64}}
}

func RecordFile(file string) Segment {
	return Segment{Type: "record", Data: map[string]any{"file": file}}
}

func Reply(messageID int64) Segment {
	return Segment{Type: "reply", Data: map[string]any{"id": messageID}}
}

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

func SendGroupForwardAction(groupID int64, nodes []ForwardNode) *ActionRequest {
	return &ActionRequest{
		Action: "send_group_forward_msg",
		Params: map[string]any{"group_id": groupID, "messages": nodes},
	}
}

func SendPrivateForwardAction(userID int64, nodes []ForwardNode) *ActionRequest {
	return &ActionRequest{
		Action: "send_private_forward_msg",
		Params: map[string]any{"user_id": userID, "messages": nodes},
	}
}

func SendForwardAction(e MessageEvent, nodes []ForwardNode) *ActionRequest {
	if e.IsGroup() {
		return SendGroupForwardAction(e.GroupID, WithForwardUIN(nodes, e.SelfID))
	}
	return SendPrivateForwardAction(e.UserID, WithForwardUIN(nodes, e.SelfID))
}

func ReplyText(e MessageEvent, text string, atSender bool) *ActionRequest {
	var msg Message
	if atSender && e.IsGroup() {
		msg = Message{At(e.UserID), Text(" " + text)}
	} else {
		msg = Message{Text(text)}
	}
	return SendMessageAction(e, msg)
}

func ReplyImage(e MessageEvent, b64 string) *ActionRequest {
	return SendMessageAction(e, Message{ImageBytes(b64)})
}

func (a *ActionRequest) Marshal() ([]byte, error) { return json.Marshal(a) }
