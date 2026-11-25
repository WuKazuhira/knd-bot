package pjsk

import (
	"sort"
	"strings"
)

type queryOptions struct {
	Arg     string
	Refresh bool
}

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

// requested < 0 时按 timestampField 升序倒数取值
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
