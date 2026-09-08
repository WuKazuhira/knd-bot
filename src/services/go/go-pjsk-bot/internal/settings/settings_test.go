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
deck:
  service_urls:
    - http://deck1:45557
    - http://deck2:45557
  default_algorithms: [dfs, ga]
  timeout: 60
  return_num_challenge: 5
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
	if urls := s.DeckServiceURLs(); len(urls) != 2 || urls[0] != "http://deck1:45557" {
		t.Errorf("DeckServiceURLs=%v", urls)
	}
	if algs := s.DeckDefaultAlgorithms(); len(algs) != 2 || algs[0] != "dfs" || algs[1] != "ga" {
		t.Errorf("DeckDefaultAlgorithms=%v", algs)
	}
	if s.DeckTimeout() != 60 {
		t.Errorf("DeckTimeout=%d want 60", s.DeckTimeout())
	}
	if s.DeckReturnNumChallenge() != 5 {
		t.Errorf("DeckReturnNumChallenge=%d want 5", s.DeckReturnNumChallenge())
	}
}

func TestLoadDefaults(t *testing.T) {
	dir := t.TempDir()
	// deck 段缺失 → 各访问器回退默认
	writeSettingsYAML(t, dir, "endpoints:\n  rank_match_api_base_url: https://x\n")
	s, err := Load(dir)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if algs := s.DeckDefaultAlgorithms(); len(algs) != 1 || algs[0] != "dfs" {
		t.Errorf("默认算法应为 [dfs], got %v", algs)
	}
	if s.DeckTimeout() != 30 {
		t.Errorf("默认超时应为 30, got %d", s.DeckTimeout())
	}
	if s.DeckReturnNumChallenge() != 3 {
		t.Errorf("默认挑战返回数应为 3, got %d", s.DeckReturnNumChallenge())
	}
	if urls := s.DeckServiceURLs(); urls != nil {
		t.Errorf("未配置 service_urls 应为 nil, got %v", urls)
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
	if s.DeckServiceURLs() != nil {
		t.Error("nil DeckServiceURLs 应为 nil")
	}
	if algs := s.DeckDefaultAlgorithms(); len(algs) != 1 || algs[0] != "dfs" {
		t.Errorf("nil DeckDefaultAlgorithms 应为 [dfs], got %v", algs)
	}
	if s.DeckTimeout() != 30 {
		t.Error("nil DeckTimeout 应为 30")
	}
	if s.DeckReturnNumChallenge() != 3 {
		t.Error("nil DeckReturnNumChallenge 应为 3")
	}
}
