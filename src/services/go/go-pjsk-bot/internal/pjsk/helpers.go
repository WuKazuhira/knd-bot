package pjsk

import (
	"encoding/json"
	"math"
)

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
