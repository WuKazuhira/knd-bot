package profile

import "encoding/json"

// difficultyIndex 把难度名映射到 MusicResult 数组下标，对齐 Python diff_index。
var difficultyIndex = map[string]int{
	"easy": 0, "normal": 1, "hard": 2, "expert": 3, "master": 4, "append": 5,
}

// strGet 从 map 取字符串字段（缺失/类型不符返回空串）。
func strGet(m map[string]any, key string) string {
	if m == nil {
		return ""
	}
	if v, ok := m[key].(string); ok {
		return v
	}
	return ""
}

// intGet 从 map 取整型字段（兼容 JSON number 为 float64）。
func intGet(m map[string]any, key string) int {
	if m == nil {
		return 0
	}
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

// mdInt / mdStr 是 diff 等主数据对象取值的别名，语义同 intGet/strGet。
func mdInt(m map[string]any, key string) int    { return intGet(m, key) }
func mdStr(m map[string]any, key string) string { return strGet(m, key) }

// firstStr 返回第一个非空字符串。
func firstStr(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}

// firstInt 返回第一个非零整数；全零则返回最后一个（作为默认值）。
func firstInt(vals ...int) int {
	for _, v := range vals {
		if v != 0 {
			return v
		}
	}
	if len(vals) > 0 {
		return vals[len(vals)-1]
	}
	return 0
}

// firstNonNil 返回第一个非 nil 值。
func firstNonNil(vals ...any) any {
	for _, v := range vals {
		if v != nil {
			return v
		}
	}
	return nil
}

// sliceOf 把 any 断言为 []any（非切片返回 nil）。
func sliceOf(v any) []any {
	if s, ok := v.([]any); ok {
		return s
	}
	return nil
}
