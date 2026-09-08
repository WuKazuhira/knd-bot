// Package serverconfig 读取 config/pjsk/servers.yaml，提供各服务器的游戏 API
// 地址（与 old-python SERVER_CONFIG 同源同结构）。
package serverconfig

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

// Config 保存 servers.yaml 解析结果。
type Config struct {
	servers map[string]serverEntry
}

type serverEntry struct {
	API map[string]string `yaml:"api"`
}

// Load 从 configDir/pjsk/servers.yaml 读取配置。
func Load(configDir string) (*Config, error) {
	path := filepath.Join(configDir, "pjsk", "servers.yaml")
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("读取 servers.yaml: %w", err)
	}
	var parsed map[string]serverEntry
	if err := yaml.Unmarshal(raw, &parsed); err != nil {
		return nil, fmt.Errorf("解析 servers.yaml: %w", err)
	}
	return &Config{servers: parsed}, nil
}

// serverName 把 pjsk_type 映射到服务器名。
func serverName(serverType int) string {
	switch serverType {
	case 1:
		return "tw"
	case 2:
		return "cn"
	default:
		return "jp"
	}
}

// apiURL 返回指定服务器的某个 api url 模板（如 suite_api_url）；不存在返回空串。
func (c *Config) apiURL(serverType int, key string) string {
	if c == nil {
		return ""
	}
	entry, ok := c.servers[serverName(serverType)]
	if !ok {
		return ""
	}
	return entry.API[key]
}

// SuiteURL 返回填好 uid 的 suite api 地址。
func (c *Config) SuiteURL(serverType int, uid string) string {
	return fillUID(c.apiURL(serverType, "suite_api_url"), uid)
}

// ProfileURL 返回填好 uid 的 profile api 地址。
func (c *Config) ProfileURL(serverType int, uid string) string {
	return fillUID(c.apiURL(serverType, "profile_api_url"), uid)
}

// RankingBorderURL 返回填好 event_id 的 ranking-border 地址。
func (c *Config) RankingBorderURL(serverType, eventID int) string {
	return fillEvent(c.apiURL(serverType, "ranking_border_api_url"), eventID)
}

// RankingTop100URL 返回填好 event_id 的 ranking-top100 地址。
func (c *Config) RankingTop100URL(serverType, eventID int) string {
	return fillEvent(c.apiURL(serverType, "ranking_top100_api_url"), eventID)
}

func fillUID(tmpl, uid string) string {
	if tmpl == "" {
		return ""
	}
	return strings.ReplaceAll(tmpl, "{uid}", uid)
}

func fillEvent(tmpl string, eventID int) string {
	if tmpl == "" {
		return ""
	}
	return strings.ReplaceAll(tmpl, "{event_id}", fmt.Sprintf("%d", eventID))
}
