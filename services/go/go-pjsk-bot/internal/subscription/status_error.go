package subscription

import (
	"errors"
	"fmt"
)

type SourceStatusReason string

const (
	ActivityClosed  SourceStatusReason = "activity_closed"
	PlayerNoRanking SourceStatusReason = "player_no_ranking"
)

type SourceStatusError struct {
	Reason SourceStatusReason
	Err    error
}

func (e SourceStatusError) Error() string {
	if e.Err != nil {
		return fmt.Sprintf("%s: %v", e.Reason, e.Err)
	}
	return string(e.Reason)
}

func (e SourceStatusError) Unwrap() error { return e.Err }

func NewSourceStatusError(reason SourceStatusReason) error {
	return SourceStatusError{Reason: reason}
}

func SourceStatusReasonOf(err error) (SourceStatusReason, bool) {
	if err == nil {
		return "", false
	}
	var pointer *SourceStatusError
	if errors.As(err, &pointer) && pointer != nil {
		return pointer.Reason, true
	}
	var value SourceStatusError
	if errors.As(err, &value) {
		return value.Reason, true
	}
	return "", false
}
