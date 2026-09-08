package skranking

import (
	"reflect"
	"testing"
)

func TestParseRankArgs(t *testing.T) {
	defaults := []int{1, 100, 1000}

	// 无参数 -> 默认档位
	if got := ParseRankArgs("", defaults, 20); !reflect.DeepEqual(got, defaults) {
		t.Errorf("空参数应返回默认档位, got %v", got)
	}
	// 单个
	if got := ParseRankArgs("100", defaults, 20); !reflect.DeepEqual(got, []int{100}) {
		t.Errorf("单个: got %v", got)
	}
	// 多个（去重保序）
	if got := ParseRankArgs("100 1000 100", defaults, 20); !reflect.DeepEqual(got, []int{100, 1000}) {
		t.Errorf("多个去重: got %v", got)
	}
	// 范围
	if got := ParseRankArgs("100-103", defaults, 20); !reflect.DeepEqual(got, []int{100, 101, 102, 103}) {
		t.Errorf("范围: got %v", got)
	}
	// 范围超限
	if got := ParseRankArgs("1-100", defaults, 20); got != nil {
		t.Errorf("超限范围应返回 nil, got %v", got)
	}
	// 非法
	if got := ParseRankArgs("abc", defaults, 20); got != nil {
		t.Errorf("非法应返回 nil, got %v", got)
	}
	// 范围反向
	if got := ParseRankArgs("100-50", defaults, 20); got != nil {
		t.Errorf("反向范围应返回 nil, got %v", got)
	}
}
