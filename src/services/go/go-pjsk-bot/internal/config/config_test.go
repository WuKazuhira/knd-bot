package config

import (
	"os"
	"testing"
)

func TestParseBool(t *testing.T) {
	for _, value := range []string{"1", "true", "TRUE", "yes", "on"} {
		if !parseBool(value) {
			t.Errorf("parseBool(%q) should be true", value)
		}
	}
	for _, value := range []string{"", "0", "false", "no", "off", "random"} {
		if parseBool(value) {
			t.Errorf("parseBool(%q) should be false", value)
		}
	}
}

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
		{"10,abc,20", []int64{10, 20}},            // 忽略非法项
		{"[\"111\", \"222\"]", []int64{111, 222}}, // 兼容 Python SUPERUSERS JSON
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
		"PJSKBOT_ONEBOT_MODE", "PJSKBOT_STANDALONE", "PJSKBOT_ONEBOT_WS_URL", "PJSKBOT_ONEBOT_TOKEN",
		"PJSKBOT_ONEBOT_LISTEN_ADDR", "PJSKBOT_ONEBOT_PATH", "PJSKBOT_LOG_MESSAGES", "DATABASE_URL", "GAMEAPI_TOKEN",
		"SEKAI_API_TOKEN", "SEKAI_CONTROL_URL", "SEKAI_CONTROL_TOKEN",
		"SEKAI_REMOTE_ACCOUNT", "SEKAI_REMOTE_REGION", "SEKAI_LIVE_INTERVAL", "SEKAI_LIVE_AUTO_STOP",
		"PJSKBOT_SUPERUSERS", "SUPERUSERS", "PJSK_DATA_DIR",
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
	if cfg.Standalone {
		t.Error("未设置 PJSKBOT_STANDALONE 时应为灰度模式")
	}
	if cfg.OneBotMode != "forward" || cfg.OneBotWSURL != "ws://127.0.0.1:3001" || cfg.OneBotToken != "" {
		t.Errorf("OneBot 正向默认值错误: mode=%q url=%q token=%q", cfg.OneBotMode, cfg.OneBotWSURL, cfg.OneBotToken)
	}
	if cfg.OneBotListenAddr != ":3001" || cfg.OneBotPath != "/onebot/v11/ws" {
		t.Errorf("OneBot 反向默认值错误: addr=%q path=%q", cfg.OneBotListenAddr, cfg.OneBotPath)
	}
	if cfg.LogMessages {
		t.Error("未设置 PJSKBOT_LOG_MESSAGES 时应关闭普通消息日志")
	}
	if cfg.DataDir != "/app/data/pjsk" {
		t.Errorf("DataDir 默认值错误: %q", cfg.DataDir)
	}
	if cfg.DatabaseURL != "" {
		t.Errorf("DatabaseURL 未设置应为空: %q", cfg.DatabaseURL)
	}
	if cfg.RemoteRegion != "cn" || cfg.LiveInterval != 80 || cfg.LiveAutoStop != "03:55" {
		t.Errorf("remote 默认值错误: region=%q interval=%d autoStop=%q", cfg.RemoteRegion, cfg.LiveInterval, cfg.LiveAutoStop)
	}
	if len(cfg.Superusers) != 0 {
		t.Errorf("未设置 superusers 应为空: %v", cfg.Superusers)
	}
}

func TestLoadStandalone(t *testing.T) {
	os.Setenv("PJSKBOT_STANDALONE", "1")
	defer os.Unsetenv("PJSKBOT_STANDALONE")
	if !Load().Standalone {
		t.Error("PJSKBOT_STANDALONE=1 应启用 standalone 模式")
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

func TestLoadSuperusersFallbackToPythonEnv(t *testing.T) {
	os.Unsetenv("PJSKBOT_SUPERUSERS")
	os.Setenv("SUPERUSERS", `["1994226627"]`)
	defer os.Unsetenv("SUPERUSERS")
	cfg := Load()
	if len(cfg.Superusers) != 1 || cfg.Superusers[0] != 1994226627 {
		t.Errorf("应兼容 Python SUPERUSERS JSON 配置: %v", cfg.Superusers)
	}
}
