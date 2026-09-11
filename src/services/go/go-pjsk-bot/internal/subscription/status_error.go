package subscription

import (
	"errors"
	"fmt"
)

// SourceStatusReason 表示 Source 判定出的业务状态，而非数据源/网络故障。
type SourceStatusReason string

const (
	// ActivityClosed 表示订阅活动已结束，或已经不是当前活动。
	ActivityClosed SourceStatusReason = "activity_closed"
	// PlayerNoRanking 表示玩家没有可用的榜线记录。
	PlayerNoRanking SourceStatusReason = "player_no_ranking"
)

// SourceStatusError 是 Source 返回给 Worker 的可判定业务结果。
// 普通数据源故障应直接返回普通 error，不要使用此类型。
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

// NewSourceStatusError 创建业务状态错误。
func NewSourceStatusError(reason SourceStatusReason) error {
	return SourceStatusError{Reason: reason}
}

// SourceStatusReasonOf 返回错误链中的业务状态原因。
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
