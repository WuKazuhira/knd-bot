// Package config 读取 go-pjsk-bot 的运行配置（全部来自环境变量）。
package config

import (
	"os"
	"strconv"
	"strings"
)

// Config 是服务运行配置。
type Config struct {
	// OneBot 正向 WS 地址与鉴权 token。
	OneBotWSURL string
	OneBotToken string

	// 依赖的 Python/Go 微服务地址。
	DrawServiceURL string // pjsk-draw 出图服务，如 http://pjsk-draw:45560
	HelperURL      string // go-pjsk-helper，如 http://pjsk-helper:8000
	SekaiAPIURL    string // sekai-api，如 http://sekai-api:9999
	DeckServiceURL string // deck-service，如 http://deck-service:45557

	// pjsk 数据目录（与主进程共享 volume，只读主数据/缓存）。
	DataDir string

	// PostgreSQL 连接串（与 Python 主进程共享同一个库与表），
	// 形如 postgresql://user:pwd@postgres:5432/db。留空则 DB 相关指令不可用。
	DatabaseURL string

	// 游戏 API（Haruki）访问 token，对应 old-python GAMEAPI_TOKEN。
	GameApiToken string

	// sekai-api（远程打歌后端）访问 token，对应 old-python SEKAI_API_TOKEN。
	// 用于「打歌分数」等远程配置指令。
	SekaiApiToken string

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
func Load() Config {
	return Config{
		OneBotWSURL:    env("PJSKBOT_ONEBOT_WS_URL", "ws://127.0.0.1:3001"),
		OneBotToken:    env("PJSKBOT_ONEBOT_TOKEN", ""),
		DrawServiceURL: env("PJSK_DRAW_SERVICE_URL", "http://127.0.0.1:45560"),
		HelperURL:      env("PJSK_HELPER_URL", "http://127.0.0.1:45558"),
		SekaiAPIURL:    env("SEKAI_API_URL", "http://127.0.0.1:9999"),
		DeckServiceURL: env("DECK_SERVICE_URL", "http://127.0.0.1:45557"),
		DataDir:        env("PJSK_DATA_DIR", "/app/data/pjsk"),
		DatabaseURL:    env("DATABASE_URL", ""),
		GameApiToken:   env("GAMEAPI_TOKEN", ""),
		SekaiApiToken:  env("SEKAI_API_TOKEN", ""),
		ConfigDir:      env("PJSK_CONFIG_DIR", "/app/config"),
		StaticDir:      env("PJSK_STATIC_DIR", "/app/data/pjsk/static"),
		Superusers:     parseIDList(env("PJSKBOT_SUPERUSERS", "")),
	}
}

// parseIDList 解析逗号/空格分隔的 QQ 号列表，忽略非法项。
func parseIDList(s string) []int64 {
	if s == "" {
		return nil
	}
	fields := strings.FieldsFunc(s, func(r rune) bool {
		return r == ',' || r == ' ' || r == ';'
	})
	out := make([]int64, 0, len(fields))
	for _, f := range fields {
		if n, err := strconv.ParseInt(strings.TrimSpace(f), 10, 64); err == nil {
			out = append(out, n)
		}
	}
	return out
}
