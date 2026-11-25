package settings

import (
	"math"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/config"

	"gopkg.in/yaml.v3"
)

type Settings struct {
	endpoints endpoints
	deck      Deck
}

type endpoints struct {
	RankMatchAPIBaseURL string `yaml:"rank_match_api_base_url"`
	MusicAliasAPIURL    string `yaml:"music_alias_api_url"`
	MusicMetasBaseURL   string `yaml:"music_metas_base_url"`
}

type rawSettings struct {
	Endpoints endpoints `yaml:"endpoints"`
	Deck      Deck      `yaml:"deck"`
}

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

func (s *Settings) RankMatchAPIBaseURL() string {
	if s == nil {
		return ""
	}
	return strings.TrimRight(s.endpoints.RankMatchAPIBaseURL, "/")
}

func (s *Settings) MusicAliasAPIURL() string {
	if s == nil {
		return ""
	}
	return s.endpoints.MusicAliasAPIURL
}

type Deck struct {
	ServiceURLs            []string `yaml:"service_urls"`
	ServiceAPI             string   `yaml:"service_api"`
	HTTPTimeoutSeconds     float64  `yaml:"http_timeout"`
	Timeout                int      `yaml:"timeout"`
	TimeoutNoEvent         int      `yaml:"timeout_no_event"`
	TimeoutSingleAlgorithm int      `yaml:"timeout_single_algorithm"`
	TimeoutBonus           int      `yaml:"timeout_bonus"`
	ReturnNumMulti         int      `yaml:"return_num_multi"`
	ReturnNumChallenge     int      `yaml:"return_num_challenge"`
	ReturnNumBonus         int      `yaml:"return_num_bonus"`
}

func (s *Settings) Deck(cfg config.Config) Deck {
	var deck Deck
	if s != nil {
		deck = s.deck
	}
	if cfg.DeckServiceURLs != nil {
		deck.ServiceURLs = cfg.DeckServiceURLs
	}
	if cfg.DeckServiceAPI != "" {
		deck.ServiceAPI = cfg.DeckServiceAPI
	}
	deck.ServiceAPI = strings.ToLower(strings.TrimSpace(deck.ServiceAPI))
	switch deck.ServiceAPI {
	case "v1", "legacy", "auto":
	default:
		deck.ServiceAPI = "v1"
	}
	if cfg.DeckServiceHTTPTimeout > 0 {
		deck.HTTPTimeoutSeconds = cfg.DeckServiceHTTPTimeout.Seconds()
	}
	if math.IsNaN(deck.HTTPTimeoutSeconds) || deck.HTTPTimeoutSeconds <= 0 || deck.HTTPTimeoutSeconds >= float64(time.Duration(1<<63-1))/float64(time.Second) {
		deck.HTTPTimeoutSeconds = 120
	}
	defaults := []struct {
		value    *int
		fallback int
	}{
		{&deck.Timeout, 30}, {&deck.TimeoutNoEvent, 45},
		{&deck.TimeoutSingleAlgorithm, 15}, {&deck.TimeoutBonus, 15},
		{&deck.ReturnNumMulti, 7}, {&deck.ReturnNumChallenge, 3}, {&deck.ReturnNumBonus, 7},
	}
	for _, item := range defaults {
		if *item.value <= 0 {
			*item.value = item.fallback
		}
	}
	return deck
}
