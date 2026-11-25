package onebot

import (
	"fmt"
	"strings"
	"unicode/utf8"
)

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
