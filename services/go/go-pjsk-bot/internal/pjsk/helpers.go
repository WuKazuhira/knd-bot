package pjsk

import (
	"encoding/base64"
	"encoding/json"
	"math"
	"strconv"
	"strings"
	"time"
)

func base64Encode(data []byte) string {
	return base64.StdEncoding.EncodeToString(data)
}

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

func strField(m map[string]any, key string) string {
	if v, ok := m[key].(string); ok {
		return v
	}
	return ""
}

// 银行家舍入（四舍六入五成双），和 math.Round 不同
func round2(x float64) float64 {
	return math.RoundToEven(x*100) / 100
}

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

func atoiDefault(s string, def int) int {
	if n, err := strconv.Atoi(s); err == nil {
		return n
	}
	return def
}

func nowMSDefault() int64 {
	return time.Now().UnixMilli()
}

func itoa64(v int64) string {
	return strconv.FormatInt(v, 10)
}

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
