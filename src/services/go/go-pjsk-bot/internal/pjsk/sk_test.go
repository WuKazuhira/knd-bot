package pjsk

import "testing"

func TestHasWLToken(t *testing.T) {
	cases := []struct {
		in   string
		want bool
	}{
		{"wl", true},
		{"wl2", true},
		{"wl 2", true},
		{"wlmfy", true},
		{"-c mfy", true},
		{"100", false},
		{"1-10", false},
		{"1 2 3", false},
		{"", false},
		{"123456789", false},
	}
	for _, c := range cases {
		if got := hasWLToken(c.in); got != c.want {
			t.Errorf("hasWLToken(%q)=%v want %v", c.in, got, c.want)
		}
	}
}

func TestCfRangeRegex(t *testing.T) {
	if !reCfRange.MatchString("1-10") {
		t.Error("1-10 应匹配范围")
	}
	if reCfRange.MatchString("1 2") {
		t.Error("1 2 不应匹配范围")
	}
	if !reCfMultiRank.MatchString("1 2 3") {
		t.Error("1 2 3 应匹配多排名")
	}
	if reCfMultiRank.MatchString("100") {
		t.Error("单个数字不应匹配多排名")
	}
	if reCfMultiRank.MatchString("1-10") {
		t.Error("范围不应匹配多排名")
	}
}
