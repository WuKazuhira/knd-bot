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
	deck      deckConfig
}

type endpoints struct {
	RankMatchAPIBaseURL string `yaml:"rank_match_api_base_url"`
	MusicAliasAPIURL    string `yaml:"music_alias_api_url"`
	MusicMetasBaseURL   string `yaml:"music_metas_base_url"`
}

type deckConfig struct {
	ServiceURLs        []string `yaml:"service_urls"`
	DefaultAlgorithms  []string `yaml:"default_algorithms"`
	Timeout            int      `yaml:"timeout"`
	ReturnNumMulti     int      `yaml:"return_num_multi"`
	ReturnNumChallenge int      `yaml:"return_num_challenge"`
	ReturnNumBonus     int      `yaml:"return_num_bonus"`
}

type rawSettings struct {
	Endpoints endpoints  `yaml:"endpoints"`
	Deck      deckConfig `yaml:"deck"`
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
	return &Settings{endpoints: parsed.Endpoints, deck: parsed.Deck}, nil
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

// DeckServiceURLs 返回 Rust deck-service 地址列表。
func (s *Settings) DeckServiceURLs() []string {
	if s == nil {
		return nil
	}
	return s.deck.ServiceURLs
}

// DeckDefaultAlgorithms 返回默认组卡算法列表（如 [dfs, ga]）。
func (s *Settings) DeckDefaultAlgorithms() []string {
	if s == nil || len(s.deck.DefaultAlgorithms) == 0 {
		return []string{"dfs"}
	}
	return s.deck.DefaultAlgorithms
}

// DeckTimeout 返回组卡超时（秒），默认 30。
func (s *Settings) DeckTimeout() int {
	if s == nil || s.deck.Timeout <= 0 {
		return 30
	}
	return s.deck.Timeout
}

// DeckReturnNumChallenge 返回挑战组卡返回数量，默认 3。
func (s *Settings) DeckReturnNumChallenge() int {
	if s == nil || s.deck.ReturnNumChallenge <= 0 {
		return 3
	}
	return s.deck.ReturnNumChallenge
}
