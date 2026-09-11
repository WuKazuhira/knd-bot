package router

import (
	"encoding/json"
	"strings"
)

// Ownership 表示哪些 pjsk 指令由 Go 接管（与 Python 命令所有权互斥）。
//
// 对齐 old-python services/go_ownership.py：读取 KND_GO_OWNED_COMMANDS，
// 命中的指令由本服务处理，Python 侧对应 matcher 安静退场。
type Ownership struct {
	owned map[string]struct{}
	all   bool
}

// ParseOwnership 解析 KND_GO_OWNED_COMMANDS 的原始值。
// 支持 JSON 数组（如 ["bind","sk"]）或逗号分隔（如 "bind,sk"）；空值表示全部由 Python 处理。
func ParseOwnership(raw string) Ownership {
	raw = strings.TrimSpace(raw)
	o := Ownership{owned: make(map[string]struct{})}
	if raw == "" {
		return o
	}
	var list []string
	if err := json.Unmarshal([]byte(raw), &list); err != nil {
		list = strings.Split(raw, ",")
	}
	for _, item := range list {
		if s := strings.TrimSpace(item); s != "" {
			o.owned[s] = struct{}{}
		}
	}
	return o
}

// All 返回 standalone 模式使用的全量 ownership。
func All() Ownership {
	return Ownership{owned: make(map[string]struct{}), all: true}
}

// Owns 返回指定指令是否由 Go 接管。
func (o Ownership) Owns(command string) bool {
	if o.all {
		return true
	}
	_, ok := o.owned[command]
	return ok
}

// Empty 返回是否未配置任何接管（即全部由 Python 处理）。
func (o Ownership) Empty() bool {
	return !o.all && len(o.owned) == 0
}
