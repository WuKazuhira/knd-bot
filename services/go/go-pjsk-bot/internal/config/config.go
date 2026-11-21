// Package config 读取 go-pjsk-bot 的运行配置（全部来自环境变量）。
package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

// Config 是服务运行配置。
type Config struct {
	// PJSKRuntime 是 Python/Go 共用的 PJSK 运行模式：python 或 go。
	PJSKRuntime string
	// OneBot 接入模式：forward 由 Go 主动拨号，reverse 由 OneBotFilter 拨号到 Go。
	OneBotMode string
	// Standalone 为 true 时处理 Go 二进制注册的全部命令，忽略灰度 ownership 清单。
	Standalone bool
	// OneBot 正向 WS 地址与鉴权 token（forward 模式使用；reverse 模式用于可选鉴权）。
	OneBotWSURL string
	OneBotToken string
	// OneBot 反向 WS 监听地址与路径（reverse 模式使用）。
	OneBotListenAddr string
	OneBotPath       string
	// 是否记录未命中普通消息的截断文本；命中/疑似命令始终记录摘要。
	LogMessages bool
	// 是否启用 UNIBOT 群成员检测与 Go 指令拦截，默认关闭。
	UnibotCheck bool

	// 依赖的 Python/Go 微服务地址。
	DrawServiceURL   string // pjsk-draw 出图服务，如 http://pjsk-draw:45560
	HelperServiceURL string // go-pjsk-helper，如 http://pjsk-helper:8000
	SekaiAPIURL      string // sekai-api，如 http://sekai-api:9999

	// pjsk 数据目录（与主进程共享 volume，只读主数据/缓存）。
	DataDir string

	// PostgreSQL 连接串（与 Python 主进程共享同一个库与表），
	// 形如 postgresql://user:pwd@postgres:5432/db。留空则 DB 相关指令不可用。
	DatabaseURL string

	// 游戏 API（Haruki）访问 token，对应 old-python GAMEAPI_TOKEN。
	GameApiToken string

	// sekai-api（远程打歌后端）访问 token，对应 old-python SEKAI_API_TOKEN。
	// 用于远程控制、token 管理与「打歌分数」等远程配置指令。
	SekaiApiToken string
	// 旧部署的宿主控制服务（可选；容器化 sekai-api 时通常留空）。
	SekaiControlURL   string
	SekaiControlToken string

	// remote 自动打歌账号与默认区服；skme 只读这些配置对应的 live_records。
	// 命令显式传入账号时覆盖 RemoteAccount；未传账号时 RemoteRegion 覆盖命令前缀区服。
	RemoteAccount string
	RemoteRegion  string
	LiveInterval  int
	LiveAutoStop  string

	// 配置目录（含 pjsk/servers.yaml 等），与主进程共享。
	ConfigDir string

	// pjsk 静态资源目录（含 character_nicknames.yaml 等），默认 DataDir/static。
	StaticDir string

	// 超级用户 QQ 列表（逗号分隔），对应 nonebot SUPERUSER。用于 CN MSR 白名单等管理指令。
	Superusers []int64
}

func env(key, def string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return def
}

// Load 从环境变量装配配置。
func Load() (Config, error) {
	runtime, standalone, err := resolvePJSKRuntime(
		os.Getenv("KNDBOT_PJSK_RUNTIME"),
		os.Getenv("PJSKBOT_STANDALONE"),
	)
	if err != nil {
		return Config{}, err
	}

	superusers := os.Getenv("PJSKBOT_SUPERUSERS")
	if strings.TrimSpace(superusers) == "" {
		// 与 Python 主进程共用 SUPERUSERS；兼容其 JSON 数组写法。
		superusers = os.Getenv("SUPERUSERS")
	}
	return Config{
		PJSKRuntime:       runtime,
		OneBotMode:        strings.ToLower(env("PJSKBOT_ONEBOT_MODE", "forward")),
		Standalone:        standalone,
		OneBotWSURL:       env("PJSKBOT_ONEBOT_WS_URL", "ws://127.0.0.1:3001"),
		OneBotToken:       env("PJSKBOT_ONEBOT_TOKEN", ""),
		OneBotListenAddr:  env("PJSKBOT_ONEBOT_LISTEN_ADDR", ":3001"),
		OneBotPath:        env("PJSKBOT_ONEBOT_PATH", "/onebot/v11/ws"),
		LogMessages:       parseBool(os.Getenv("PJSKBOT_LOG_MESSAGES")),
		UnibotCheck:       parseBool(os.Getenv("PJSKBOT_UNIBOT_CHECK")),
		DrawServiceURL:    env("PJSK_DRAW_SERVICE_URL", "http://127.0.0.1:45560"),
		HelperServiceURL:  env("PJSK_HELPER_URL", "http://127.0.0.1:45558"),
		SekaiAPIURL:       env("SEKAI_API_URL", "http://127.0.0.1:9999"),
		DataDir:           env("PJSK_DATA_DIR", "/app/data/pjsk"),
		DatabaseURL:       env("DATABASE_URL", ""),
		GameApiToken:      env("GAMEAPI_TOKEN", ""),
		SekaiApiToken:     env("SEKAI_API_TOKEN", ""),
		SekaiControlURL:   env("SEKAI_CONTROL_URL", ""),
		SekaiControlToken: env("SEKAI_CONTROL_TOKEN", ""),
		RemoteAccount:     env("SEKAI_REMOTE_ACCOUNT", ""),
		RemoteRegion:      strings.ToLower(env("SEKAI_REMOTE_REGION", "cn")),
		LiveInterval:      parsePositiveInt(env("SEKAI_LIVE_INTERVAL", "80"), 80),
		LiveAutoStop:      env("SEKAI_LIVE_AUTO_STOP", "03:55"),
		ConfigDir:         env("PJSK_CONFIG_DIR", "/app/config"),
		StaticDir:         env("PJSK_STATIC_DIR", "/app/data/pjsk/static"),
		Superusers:        parseIDList(superusers),
	}, nil
}

func resolvePJSKRuntime(runtimeValue, standaloneValue string) (string, bool, error) {
	runtime := strings.ToLower(strings.TrimSpace(runtimeValue))
	standaloneRaw := strings.TrimSpace(standaloneValue)
	standaloneSet := standaloneRaw != ""
	legacyStandalone := standaloneRaw == "1"

	if runtime == "" {
		if legacyStandalone {
			return "go", true, nil
		}
		return "python", false, nil
	}
	if runtime != "go" && runtime != "python" {
		return "", false, fmt.Errorf("invalid KNDBOT_PJSK_RUNTIME %q (want go or python)", runtimeValue)
	}

	wantStandalone := runtime == "go"
	if standaloneSet && legacyStandalone != wantStandalone {
		return "", false, fmt.Errorf(
			"conflicting PJSK runtime configuration: KNDBOT_PJSK_RUNTIME=%q requires PJSKBOT_STANDALONE=%d when both are set, got %q",
			runtime, boolInt(wantStandalone), standaloneValue,
		)
	}
	return runtime, wantStandalone, nil
}

func boolInt(value bool) int {
	if value {
		return 1
	}
	return 0
}

func parseBool(s string) bool {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

func parsePositiveInt(s string, def int) int {
	value, err := strconv.Atoi(strings.TrimSpace(s))
	if err != nil || value <= 0 {
		return def
	}
	return value
}

// parseIDList 解析逗号/空格分隔的 QQ 号列表，忽略非法项。
func parseIDList(s string) []int64 {
	if s == "" {
		return nil
	}
	fields := strings.FieldsFunc(s, func(r rune) bool {
		return r == ',' || r == ' ' || r == ';' || r == '\t' || r == '\n' || r == '\r' || r == '[' || r == ']' || r == '"' || r == '\''
	})
	out := make([]int64, 0, len(fields))
	for _, f := range fields {
		if n, err := strconv.ParseInt(strings.TrimSpace(f), 10, 64); err == nil {
			out = append(out, n)
		}
	}
	return out
}
