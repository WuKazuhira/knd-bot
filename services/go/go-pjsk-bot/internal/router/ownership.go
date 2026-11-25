package router

import (
	"encoding/json"
	"strings"
)

type Ownership struct {
	owned map[string]struct{}
	all   bool
}

func ParseOwnership(raw string) Ownership {
	raw = strings.TrimSpace(raw)
	o := Ownership{owned: make(map[string]struct{})}
	if raw == "" {
		return o
	}
	var list []string
	if json.Valid([]byte(raw)) {
		// 合法 JSON 必须是数组，避免把对象或标量当 CSV 拆
		var values []any
		if err := json.Unmarshal([]byte(raw), &values); err != nil {
			return o
		}
		for _, value := range values {
			if item, ok := value.(string); ok {
				list = append(list, item)
			}
		}
	} else {
		list = strings.Split(raw, ",")
	}
	for _, item := range list {
		if s := strings.TrimSpace(item); s != "" {
			o.owned[s] = struct{}{}
		}
	}
	return o
}

func All() Ownership {
	return Ownership{owned: make(map[string]struct{}), all: true}
}

func (o Ownership) Owns(command string) bool {
	if o.all {
		return true
	}
	_, ok := o.owned[command]
	return ok
}

func (o Ownership) Empty() bool {
	return !o.all && len(o.owned) == 0
}
