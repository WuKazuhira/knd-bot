package settings

import (
	"os"
	"path/filepath"
	"testing"
)

func writeSettingsYAML(t *testing.T, configDir, content string) {
	t.Helper()
	dir := filepath.Join(configDir, "pjsk")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "settings.yaml"), []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

const fullYAML = `endpoints:
  rank_match_api_base_url: https://rank.example.com/api/
  music_alias_api_url: https://alias.example.com/{id}
`

func TestLoadFull(t *testing.T) {
	dir := t.TempDir()
	writeSettingsYAML(t, dir, fullYAML)
	s, err := Load(dir)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	// 去尾斜杠
	if got := s.RankMatchAPIBaseURL(); got != "https://rank.example.com/api" {
		t.Errorf("RankMatchAPIBaseURL=%q", got)
	}
	if got := s.MusicAliasAPIURL(); got != "https://alias.example.com/{id}" {
		t.Errorf("MusicAliasAPIURL=%q", got)
	}
}

func TestLoadDefaults(t *testing.T) {
	dir := t.TempDir()
	writeSettingsYAML(t, dir, "endpoints:\n  rank_match_api_base_url: https://x\n")
	s, err := Load(dir)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if got := s.RankMatchAPIBaseURL(); got != "https://x" {
		t.Errorf("RankMatchAPIBaseURL=%q", got)
	}
}

func TestLoadMissingFile(t *testing.T) {
	if _, err := Load(t.TempDir()); err == nil {
		t.Error("缺失 settings.yaml 应返回错误")
	}
}

func TestLoadInvalidYAML(t *testing.T) {
	dir := t.TempDir()
	writeSettingsYAML(t, dir, "endpoints: [不是map")
	if _, err := Load(dir); err == nil {
		t.Error("非法 yaml 应返回错误")
	}
}

func TestNilSettingsSafe(t *testing.T) {
	var s *Settings
	// nil 接收者各方法应安全返回零值/默认值
	if s.RankMatchAPIBaseURL() != "" {
		t.Error("nil RankMatchAPIBaseURL 应为空")
	}
	if s.MusicAliasAPIURL() != "" {
		t.Error("nil MusicAliasAPIURL 应为空")
	}
}
