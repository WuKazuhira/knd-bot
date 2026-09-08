package pjsk

import "testing"

func TestRegisterTime(t *testing.T) {
	// 国服真实 uid：>> 32 得注册秒级时间戳
	cn := "7486056827726306063"
	got := RegisterTime(cn, ServerCN)
	// 7486056827726306063 / 2^32 = 1743013... （2025 年），应在合理范围
	if got <= 1600000000 || got >= 2000000000 {
		t.Fatalf("CN RegisterTime(%s) = %d, 超出预期范围", cn, got)
	}

	// 非法输入
	if RegisterTime("abc", ServerCN) != 0 {
		t.Fatal("非法 uid 应返回 0")
	}
	// 未知服务器类型
	if RegisterTime("123", 99) != 0 {
		t.Fatal("未知服务器类型应返回 0")
	}
}

func TestVerifyID(t *testing.T) {
	cases := []struct {
		name   string
		uid    string
		server int
		want   bool
	}{
		{"合法国服uid", "7486056827726306063", ServerCN, true},
		{"太短", "123", ServerCN, false},
		{"含非数字", "74860568277263060ab", ServerCN, false},
		{"过长", "123456789012345678901", ServerCN, false},
		{"空", "", ServerJP, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := VerifyID(c.uid, c.server); got != c.want {
				t.Errorf("VerifyID(%q, %d) = %v, want %v", c.uid, c.server, got, c.want)
			}
		})
	}
}

func TestIsDigits(t *testing.T) {
	if !isDigits("12345") {
		t.Fatal("12345 应为纯数字")
	}
	if isDigits("12a45") {
		t.Fatal("12a45 不应为纯数字")
	}
	if isDigits("") {
		t.Fatal("空串不应为纯数字")
	}
}
