package pjsk

import (
	"errors"

	"github.com/kazuhira/go-pjsk-bot/internal/gameapi"
)

// apiErrText 把错误转成面向用户的文案：游戏 API 业务错误用其消息，其余用通用 BUG 提示。
func apiErrText(err error) string {
	var apiErr *gameapi.APIError
	if errors.As(err, &apiErr) {
		return apiErr.Message
	}
	return errBug
}
