package pjsk

import (
	"sort"
	"strings"
)

// queryOptions 是查卡/查活动共用的查询开关。
type queryOptions struct {
	Arg     string
	Refresh bool
}

// parseQueryOptions 从参数中移除显式刷新开关。
// -1 等负数 ID 不会被误判为刷新参数。
func parseQueryOptions(arg string) queryOptions {
	parts := strings.Fields(arg)
	kept := make([]string, 0, len(parts))
	var opts queryOptions
	for _, part := range parts {
		switch strings.ToLower(part) {
		case "-refresh", "--refresh":
			opts.Refresh = true
		default:
			kept = append(kept, part)
		}
	}
	opts.Arg = strings.TrimSpace(strings.Join(kept, " "))
	return opts
}

// orderedMasterItem 按真实 ID 或发布时间倒数索引选择主数据对象。
// requested > 0 按真实 id 查询；requested < 0 按 timestampField 升序后的末尾索引查询。
func orderedMasterItem(items []map[string]any, requested int, timestampField string) (map[string]any, bool) {
	if requested > 0 {
		for _, item := range items {
			if intField(item, "id") == requested {
				return item, true
			}
		}
		return nil, false
	}
	if requested >= 0 {
		return nil, false
	}
	ordered := append([]map[string]any(nil), items...)
	sort.SliceStable(ordered, func(i, j int) bool {
		left := int64(intField(ordered[i], timestampField))
		right := int64(intField(ordered[j], timestampField))
		if left != right {
			return left < right
		}
		return intField(ordered[i], "id") < intField(ordered[j], "id")
	})
	index := len(ordered) + requested
	if index < 0 || index >= len(ordered) {
		return nil, false
	}
	return ordered[index], true
}
