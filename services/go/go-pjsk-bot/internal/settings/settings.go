package settings

import (
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

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
