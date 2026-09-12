// Package settings 读取 config/pjsk/settings.yaml 中的 endpoints 等运行配置，
// 与 old-python _config._settings 同源。
package settings

import (
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

// Settings 保存 settings.yaml 中本服务需要的字段。
type Settings struct {
	endpoints endpoints
}

type endpoints struct {
	RankMatchAPIBaseURL string `yaml:"rank_match_api_base_url"`
	MusicAliasAPIURL    string `yaml:"music_alias_api_url"`
	MusicMetasBaseURL   string `yaml:"music_metas_base_url"`
}

type rawSettings struct {
	Endpoints endpoints `yaml:"endpoints"`
}

// Load 从 configDir/pjsk/settings.yaml 读取配置。
func Load(configDir string) (*Settings, error) {
	path := filepath.Join(configDir, "pjsk", "settings.yaml")
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var parsed rawSettings
	if err := yaml.Unmarshal(raw, &parsed); err != nil {
		return nil, err
	}
	return &Settings{endpoints: parsed.Endpoints}, nil
}

// RankMatchAPIBaseURL 返回排位赛 API 基址（已去尾斜杠）。
func (s *Settings) RankMatchAPIBaseURL() string {
	if s == nil {
		return ""
	}
	return strings.TrimRight(s.endpoints.RankMatchAPIBaseURL, "/")
}

// MusicAliasAPIURL 返回歌曲别名 API 模板。
func (s *Settings) MusicAliasAPIURL() string {
	if s == nil {
		return ""
	}
	return s.endpoints.MusicAliasAPIURL
}
