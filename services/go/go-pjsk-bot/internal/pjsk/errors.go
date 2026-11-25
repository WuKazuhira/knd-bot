package pjsk

import (
	"errors"

	"github.com/kazuhira/go-pjsk-bot/internal/gameapi"
)

func apiErrText(err error) string {
	var apiErr *gameapi.APIError
	if errors.As(err, &apiErr) {
		return apiErr.Message
	}
	return errBug
}
