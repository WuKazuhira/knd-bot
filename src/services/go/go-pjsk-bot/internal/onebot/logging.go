package onebot

import (
	"fmt"
	"strings"
	"unicode/utf8"
)

// TruncateText 返回适合日志的单行文本摘要，不记录过长消息。
func TruncateText(text string, limit int) string {
	text = strings.Join(strings.Fields(text), " ")
	if limit <= 0 {
		limit = 120
	}
	if utf8.RuneCountInString(text) <= limit {
		return text
	}
	runes := []rune(text)
	return string(runes[:limit]) + "…"
}

// EventSummary 返回消息事件的安全摘要；只有 includeText=true 时包含消息文本。
func EventSummary(event MessageEvent, includeText bool) string {
	target := fmt.Sprintf("user=%d", event.UserID)
	if event.IsGroup() {
		target = fmt.Sprintf("group=%d user=%d", event.GroupID, event.UserID)
	}
	if !includeText {
		return fmt.Sprintf("type=%s %s", event.MessageType, target)
	}
	return fmt.Sprintf("type=%s %s text=%q", event.MessageType, target, TruncateText(event.Message.PlainText(), 160))
}

// ActionSummary 返回 action 的目标与消息段类型摘要，不展开图片/base64/正文参数。
func ActionSummary(action *ActionRequest) string {
	if action == nil {
		return "action=<nil>"
	}
	if action.Action != "send_msg" {
		return fmt.Sprintf("action=%s", action.Action)
	}
	messageType, _ := action.Params["message_type"].(string)
	target := "target=?"
	if messageType == "group" {
		target = fmt.Sprintf("group=%v", action.Params["group_id"])
	} else if messageType == "private" {
		target = fmt.Sprintf("user=%v", action.Params["user_id"])
	}
	segments := ""
	if message, ok := action.Params["message"].(Message); ok {
		types := make([]string, 0, len(message))
		for _, segment := range message {
			types = append(types, segment.Type)
		}
		segments = strings.Join(types, ",")
	}
	return fmt.Sprintf("action=send_msg %s segments=%s", target, segments)
}

// LooksLikeCommand 用于决定未命中消息是否值得记录文本摘要。
func LooksLikeCommand(text string) bool {
	text = strings.TrimSpace(text)
	if text == "" {
		return false
	}
	if strings.HasPrefix(text, "/") || strings.HasPrefix(text, "!") || strings.HasPrefix(text, "！") {
		return true
	}
	first := strings.ToLower(strings.Fields(text)[0])
	for _, prefix := range []string{"pjsk", "cnpjsk", "twpjsk", "sk", "cnsk", "twsk", "wlsk", "cnwlsk", "twwlsk", "cf", "cncf", "twcf", "bind", "cardbox", "remote", "live"} {
		if first == prefix || strings.HasPrefix(first, prefix) {
			return true
		}
	}
	for _, prefix := range []string{"cn", "tw"} {
		if strings.HasPrefix(first, prefix) {
			first = strings.TrimPrefix(first, prefix)
			break
		}
	}
	for _, marker := range []string{"卡", "查", "绑定", "订阅", "猜", "活动", "组卡", "远程"} {
		if strings.Contains(first, marker) {
			return true
		}
	}
	return false
}
