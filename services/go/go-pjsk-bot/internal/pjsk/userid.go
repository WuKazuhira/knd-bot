package pjsk

import (
	"strconv"
	"time"
)

const (
	ServerJP = 0
	ServerTW = 1
	ServerCN = 2
)

// 日服 uid 为 (ts_ms-1600218000000)<<22，台/国服为 ts_s<<32
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
