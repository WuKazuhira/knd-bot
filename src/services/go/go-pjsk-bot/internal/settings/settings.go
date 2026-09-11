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
	haruki    harukiConfig
}

type endpoints struct {
	RankMatchAPIBaseURL string `yaml:"rank_match_api_base_url"`
	MusicAliasAPIURL    string `yaml:"music_alias_api_url"`
	MusicMetasBaseURL   string `yaml:"music_metas_base_url"`
}

type deckConfig struct {
	ServiceURLs            []string `yaml:"service_urls"`
	DefaultAlgorithms      []string `yaml:"default_algorithms"`
	Timeout                int      `yaml:"timeout"`
	TimeoutNoEvent         int      `yaml:"timeout_no_event"`
	TimeoutSingleAlgorithm int      `yaml:"timeout_single_algorithm"`
	TimeoutBonus           int      `yaml:"timeout_bonus"`
	ReturnNumMulti         int      `yaml:"return_num_multi"`
	ReturnNumChallenge     int      `yaml:"return_num_challenge"`
	ReturnNumBonus         int      `yaml:"return_num_bonus"`
}

type harukiConfig struct {
	DeckServiceURLs []string `yaml:"deck_service_urls"`
}

type rawSettings struct {
	Endpoints endpoints    `yaml:"endpoints"`
	Deck      deckConfig   `yaml:"deck"`
	Haruki    harukiConfig `yaml:"haruki"`
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
	return &Settings{endpoints: parsed.Endpoints, deck: parsed.Deck, haruki: parsed.Haruki}, nil
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
	return append([]string(nil), s.deck.ServiceURLs...)
}

// DeckDefaultAlgorithms 返回默认组卡算法列表（如 [dfs, ga]）。
func (s *Settings) DeckDefaultAlgorithms() []string {
	if s == nil || len(s.deck.DefaultAlgorithms) == 0 {
		return []string{"dfs"}
	}
	return append([]string(nil), s.deck.DefaultAlgorithms...)
}

// HarukiDeckServiceURLs 返回 haruki 段配置的组卡服务地址。
func (s *Settings) HarukiDeckServiceURLs() []string {
	if s == nil {
		return nil
	}
	return append([]string(nil), s.haruki.DeckServiceURLs...)
}

func (s *Settings) DeckTimeout() int {
	if s == nil || s.deck.Timeout <= 0 {
		return 30
	}
	return s.deck.Timeout
}

func (s *Settings) DeckTimeoutNoEvent() int {
	if s == nil || s.deck.TimeoutNoEvent <= 0 {
		return 45
	}
	return s.deck.TimeoutNoEvent
}

func (s *Settings) DeckTimeoutSingleAlgorithm() int {
	if s == nil || s.deck.TimeoutSingleAlgorithm <= 0 {
		return 15
	}
	return s.deck.TimeoutSingleAlgorithm
}

func (s *Settings) DeckTimeoutBonus() int {
	if s == nil || s.deck.TimeoutBonus <= 0 {
		return 15
	}
	return s.deck.TimeoutBonus
}

func (s *Settings) DeckReturnNumMulti() int {
	if s == nil || s.deck.ReturnNumMulti <= 0 {
		return 7
	}
	return s.deck.ReturnNumMulti
}

func (s *Settings) DeckReturnNumChallenge() int {
	if s == nil || s.deck.ReturnNumChallenge <= 0 {
		return 3
	}
	return s.deck.ReturnNumChallenge
}

func (s *Settings) DeckReturnNumBonus() int {
	if s == nil || s.deck.ReturnNumBonus <= 0 {
		return 7
	}
	return s.deck.ReturnNumBonus
}
