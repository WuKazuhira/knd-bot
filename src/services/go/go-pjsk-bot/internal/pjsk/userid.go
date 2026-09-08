// Package pjsk 承载 PJSK 各指令的业务逻辑（对齐 old-python src/plugins/pjsk）。
//
// 出图不在此实现：业务组织好数据后调用 internal/draw 委托 pjsk-draw 微服务。
package pjsk

import (
	"strconv"
	"time"
)

// 服务器类型，对齐 Python pjsk_type：0=日服 1=台服 2=国服。
const (
	ServerJP = 0
	ServerTW = 1
	ServerCN = 2
)

// RegisterTime 由 pjsk uid 解算账号注册时间（Unix 秒）。无法解析返回 0。
//
// 对齐 old-python _utils.gettime：
//   - 日服(JP)：id 为 (timestamp_ms - 1600218000000) << 22；(uid/1000) >> 22 + 1600218000
//   - 台/国服(字节)：id 为 timestamp_s << 32；uid >> 32
func RegisterTime(userid string, serverType int) int64 {
	v, err := strconv.ParseInt(userid, 10, 64)
	if err != nil || v < 0 {
		return 0
	}
	switch serverType {
	case ServerJP:
		passtime := (v / 1000) / 4194304 // 2^22
		return 1600218000 + passtime
	case ServerTW, ServerCN:
		return v / 4294967296 // 2^32
	}
	return 0
}

// VerifyID 校验 pjsk uid 合规性：13~20 位数字，且解算出的注册时间在
// [2020-09-16, now+1d] 之间。对齐 old-python _utils.verifyid。
func VerifyID(userid string, serverType int) bool {
	if len(userid) < 13 || len(userid) > 20 {
		return false
	}
	if !isDigits(userid) {
		return false
	}
	rt := RegisterTime(userid, serverType)
	if rt == 0 {
		return false
	}
	dt := time.Unix(rt, 0)
	start := time.Date(2020, 9, 16, 0, 0, 0, 0, time.Local)
	end := time.Now().Add(24 * time.Hour)
	if dt.Before(start) || dt.After(end) {
		return false
	}
	return true
}

func isDigits(s string) bool {
	if s == "" {
		return false
	}
	for i := 0; i < len(s); i++ {
		if s[i] < '0' || s[i] > '9' {
			return false
		}
	}
	return true
}
