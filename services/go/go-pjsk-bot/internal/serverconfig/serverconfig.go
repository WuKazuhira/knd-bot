package serverconfig

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

type Config struct {
	servers map[string]serverEntry
}

type serverEntry struct {
	API map[string]string `yaml:"api"`
	Rip struct {
		Sources []ripSource `yaml:"sources"`
	} `yaml:"rip"`
}

type ripSource struct {
	Name     string   `yaml:"name"`
	BaseURL  string   `yaml:"base_url"`
	Prefixes []string `yaml:"prefixes"`
}

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

func (c *Config) SuiteURL(serverType int, uid string) string {
	return fillUID(c.apiURL(serverType, "suite_api_url"), uid)
}

func (c *Config) ProfileURL(serverType int, uid string) string {
	return fillUID(c.apiURL(serverType, "profile_api_url"), uid)
}

func (c *Config) MysekaiURL(serverType int, uid string) string {
	return fillUID(c.apiURL(serverType, "mysekai_api_url"), uid)
}

func (c *Config) MysekaiPhotoURL(serverType int) string {
	return c.apiURL(serverType, "mysekai_photo_api_url")
}

func (c *Config) AssetURLs(serverType int, path, raw string) []string {
	if c == nil {
		return nil
	}
	entry, ok := c.servers[serverName(serverType)]
	if !ok {
		return nil
	}
	path = strings.Trim(path, "/")
	raw = strings.TrimLeft(raw, "/")
	rel := strings.ReplaceAll(strings.Trim(path+"/"+raw, "/"), "_rip", "")
	isScore := strings.Contains(path+"/", "music/music_score/") && !strings.Contains(raw, ".")
	urls := make([]string, 0, len(entry.Rip.Sources)*3)
	for _, source := range entry.Rip.Sources {
		base := strings.TrimRight(source.BaseURL, "/") + "/"
		allowed := len(source.Prefixes) == 0
		for _, prefix := range source.Prefixes {
			if strings.HasPrefix(path, strings.Trim(prefix, "/")) {
				allowed = true
				break
			}
		}
		if !allowed {
			continue
		}
		if isScore {
			urls = append(urls, base+path+"/"+raw+".txt")
		}
		switch source.Name {
		case "haruki":
			if hasAssetPrefix(rel, "event", "gacha", "music/long", "mysekai", "virtual_live") {
				urls = append(urls, base+"ondemand/"+rel)
			} else if hasAssetPrefix(rel, "bonds_honor", "honor", "thumbnail", "character", "music", "rank_live", "stamp", "home/banner", "player_frame", "areaitem") {
				urls = append(urls, base+"startapp/"+rel)
			}
		case "sekai.best":
			urls = append(urls, base+rel)
		}
		urls = append(urls, base+path+"/"+raw)
	}
	return uniqueStrings(urls)
}

func hasAssetPrefix(path string, prefixes ...string) bool {
	for _, prefix := range prefixes {
		if path == prefix || strings.HasPrefix(path, prefix+"/") {
			return true
		}
	}
	return false
}

func uniqueStrings(values []string) []string {
	seen := make(map[string]struct{}, len(values))
	out := make([]string, 0, len(values))
	for _, value := range values {
		if _, ok := seen[value]; ok || value == "" {
			continue
		}
		seen[value] = struct{}{}
		out = append(out, value)
	}
	return out
}

func (c *Config) MysekaiUploadTimeURL(serverType int) string {
	return c.apiURL(serverType, "mysekai_upload_time_api_url")
}

func (c *Config) RankingBorderURL(serverType, eventID int) string {
	return fillEvent(c.apiURL(serverType, "ranking_border_api_url"), eventID)
}

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
