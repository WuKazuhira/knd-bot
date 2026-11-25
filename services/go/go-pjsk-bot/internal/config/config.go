package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

type Config struct {
	PJSKRuntime                string
	OneBotMode                 string
	Standalone                 bool
	OneBotWSURL                string
	OneBotToken                string
	AllowUnauthenticatedOneBot bool
	GroupManagerPath           string
	OneBotListenAddr           string
	OneBotPath                 string
	LogMessages                bool
	UnibotCheck                bool

	DrawServiceURL   string
	HelperServiceURL string
	DataDir          string
	DatabaseURL      string
	GameApiToken     string

	RemoteAccount string
	RemoteRegion  string

	ConfigDir  string
	StaticDir  string
	Superusers []int64
}

func env(key, def string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return def
}

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
		// 没配 PJSKBOT_SUPERUSERS 时复用主进程的 SUPERUSERS
		superusers = os.Getenv("SUPERUSERS")
	}
	return Config{
		PJSKRuntime:                runtime,
		OneBotMode:                 strings.ToLower(env("PJSKBOT_ONEBOT_MODE", "forward")),
		Standalone:                 standalone,
		OneBotWSURL:                env("PJSKBOT_ONEBOT_WS_URL", "ws://127.0.0.1:3001"),
		OneBotToken:                env("PJSKBOT_ONEBOT_TOKEN", ""),
		AllowUnauthenticatedOneBot: os.Getenv("PJSKBOT_ALLOW_UNAUTHENTICATED_ONEBOT") == "1",
		GroupManagerPath:           env("PJSKBOT_GROUP_MANAGER_PATH", "/app/data/manager/group_manager.json"),
		OneBotListenAddr:           env("PJSKBOT_ONEBOT_LISTEN_ADDR", ":3001"),
		OneBotPath:                 env("PJSKBOT_ONEBOT_PATH", "/onebot/v11/ws"),
		LogMessages:                parseBool(os.Getenv("PJSKBOT_LOG_MESSAGES")),
		UnibotCheck:                parseBool(os.Getenv("PJSKBOT_UNIBOT_CHECK")),
		DrawServiceURL:             env("PJSK_DRAW_SERVICE_URL", "http://127.0.0.1:45560"),
		HelperServiceURL:           env("PJSK_HELPER_URL", "http://127.0.0.1:45558"),
		DataDir:                    env("PJSK_DATA_DIR", "/app/data/pjsk"),
		DatabaseURL:                env("DATABASE_URL", ""),
		GameApiToken:               env("GAMEAPI_TOKEN", ""),
		RemoteAccount:              env("SEKAI_REMOTE_ACCOUNT", ""),
		RemoteRegion:               strings.ToLower(env("SEKAI_REMOTE_REGION", "cn")),
		ConfigDir:                  env("PJSK_CONFIG_DIR", "/app/config"),
		StaticDir:                  env("PJSK_STATIC_DIR", "/app/data/pjsk/static"),
		Superusers:                 parseIDList(superusers),
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
