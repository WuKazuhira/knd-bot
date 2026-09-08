package config

import (
	"os"
	"testing"
)

func TestParseIDList(t *testing.T) {
	cases := []struct {
		in   string
		want []int64
	}{
		{"", nil},
		{"123", []int64{123}},
		{"1,2,3", []int64{1, 2, 3}},
		{"1 2 3", []int64{1, 2, 3}},
		{"1;2;3", []int64{1, 2, 3}},
		{"1, 2 ,3", []int64{1, 2, 3}},
		{"10,abc,20", []int64{10, 20}}, // 忽略非法项
		{"  ", nil},
	}
	for _, c := range cases {
		got := parseIDList(c.in)
		if len(got) != len(c.want) {
			t.Errorf("parseIDList(%q)=%v want %v", c.in, got, c.want)
			continue
		}
		for i := range c.want {
			if got[i] != c.want[i] {
				t.Errorf("parseIDList(%q)[%d]=%d want %d", c.in, i, got[i], c.want[i])
			}
		}
	}
}

func TestEnvDefault(t *testing.T) {
	const key = "PJSKBOT_TEST_ENV_XYZ"
	os.Unsetenv(key)
	if got := env(key, "def"); got != "def" {
		t.Errorf("未设置时应返回默认值, got %q", got)
	}
	os.Setenv(key, "  value  ")
	defer os.Unsetenv(key)
	if got := env(key, "def"); got != "value" {
		t.Errorf("应返回 trim 后的值, got %q", got)
	}
	// 空白值视为未设置 → 返回默认
	os.Setenv(key, "   ")
	if got := env(key, "def"); got != "def" {
		t.Errorf("空白值应返回默认, got %q", got)
	}
}

func TestLoadDefaults(t *testing.T) {
	// 清掉相关环境变量，验证默认值
	keys := []string{
		"PJSKBOT_ONEBOT_WS_URL", "DATABASE_URL", "GAMEAPI_TOKEN",
		"SEKAI_API_TOKEN", "PJSKBOT_SUPERUSERS", "PJSK_DATA_DIR",
	}
	saved := map[string]string{}
	for _, k := range keys {
		saved[k] = os.Getenv(k)
		os.Unsetenv(k)
	}
	defer func() {
		for k, v := range saved {
			if v != "" {
				os.Setenv(k, v)
			}
		}
	}()

	cfg := Load()
	if cfg.OneBotWSURL != "ws://127.0.0.1:3001" {
		t.Errorf("OneBotWSURL 默认值错误: %q", cfg.OneBotWSURL)
	}
	if cfg.DataDir != "/app/data/pjsk" {
		t.Errorf("DataDir 默认值错误: %q", cfg.DataDir)
	}
	if cfg.DatabaseURL != "" {
		t.Errorf("DatabaseURL 未设置应为空: %q", cfg.DatabaseURL)
	}
	if len(cfg.Superusers) != 0 {
		t.Errorf("未设置 superusers 应为空: %v", cfg.Superusers)
	}
}

func TestLoadSuperusers(t *testing.T) {
	os.Setenv("PJSKBOT_SUPERUSERS", "111,222,333")
	defer os.Unsetenv("PJSKBOT_SUPERUSERS")
	cfg := Load()
	if len(cfg.Superusers) != 3 || cfg.Superusers[0] != 111 || cfg.Superusers[2] != 333 {
		t.Errorf("Superusers 解析错误: %v", cfg.Superusers)
	}
}
