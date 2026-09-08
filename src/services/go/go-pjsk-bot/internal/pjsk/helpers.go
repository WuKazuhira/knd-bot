package pjsk

import (
	"encoding/base64"
	"encoding/json"
	"math"
	"strconv"
	"strings"
	"time"
)

// base64Encode 把图片字节编码为 base64 字符串（供 OneBot image 段）。
func base64Encode(data []byte) string {
	return base64.StdEncoding.EncodeToString(data)
}

// intField 从主数据对象取整型字段（兼容 JSON number 为 float64）。
func intField(m map[string]any, key string) int {
	switch n := m[key].(type) {
	case float64:
		return int(n)
	case json.Number:
		i, _ := n.Int64()
		return int(i)
	case int:
		return n
	case int64:
		return int(n)
	}
	return 0
}

// strField 从主数据对象取字符串字段。
func strField(m map[string]any, key string) string {
	if v, ok := m[key].(string); ok {
		return v
	}
	return ""
}

// round2 保留两位小数（对齐 Python round(x, 2)）。
func round2(x float64) float64 {
	return math.Round(x*100) / 100
}

// floatField 从主数据对象取浮点字段（兼容 int/float64）。
func floatField(m map[string]any, key string) float64 {
	switch n := m[key].(type) {
	case float64:
		return n
	case json.Number:
		f, _ := n.Float64()
		return f
	case int:
		return float64(n)
	case int64:
		return float64(n)
	}
	return 0
}

// sliceOfMap 把 any 断言为 []map[string]any（用于嵌套主数据数组）。
func sliceOfMap(v any) []map[string]any {
	s, ok := v.([]any)
	if !ok {
		return nil
	}
	out := make([]map[string]any, 0, len(s))
	for _, it := range s {
		if m, ok := it.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

// atoiDefault 解析整数，失败返回 def。
func atoiDefault(s string, def int) int {
	if n, err := strconv.Atoi(s); err == nil {
		return n
	}
	return def
}

// nowMSDefault 返回当前毫秒时间戳。
func nowMSDefault() int64 {
	return time.Now().UnixMilli()
}

// itoa64 把 int64 转成十进制字符串。
func itoa64(v int64) string {
	return strconv.FormatInt(v, 10)
}

// parseIntToken 解析可带负号的纯数字 token（对齐 Python t.lstrip("-").isdigit()）。
func parseIntToken(t string) (int, bool) {
	if t == "" {
		return 0, false
	}
	body := strings.TrimPrefix(t, "-")
	if body == "" {
		return 0, false
	}
	for _, r := range body {
		if r < '0' || r > '9' {
			return 0, false
		}
	}
	n, err := strconv.Atoi(t)
	if err != nil {
		return 0, false
	}
	return n, true
}
