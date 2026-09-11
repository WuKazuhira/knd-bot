package settings

import "testing"

func TestDeckSettingsAndHarukiURLs(t *testing.T) {
	dir := t.TempDir()
	writeSettingsYAML(t, dir, `haruki:
  deck_service_urls: [http://haruki:45557]
deck:
  service_urls: [http://deck:45557]
  default_algorithms: [dfs, ga]
  timeout_no_event: 45
  timeout_single_algorithm: 15
  timeout_bonus: 12
  return_num_multi: 7
  return_num_challenge: 3
  return_num_bonus: 5
`)
	s, err := Load(dir)
	if err != nil {
		t.Fatal(err)
	}
	if got := s.HarukiDeckServiceURLs(); len(got) != 1 || got[0] != "http://haruki:45557" {
		t.Fatalf("haruki urls=%v", got)
	}
	if got := s.DeckServiceURLs(); len(got) != 1 || got[0] != "http://deck:45557" {
		t.Fatalf("deck urls=%v", got)
	}
	if s.DeckTimeoutNoEvent() != 45 || s.DeckTimeoutSingleAlgorithm() != 15 || s.DeckTimeoutBonus() != 12 {
		t.Fatalf("timeouts incorrect")
	}
	if s.DeckReturnNumBonus() != 5 {
		t.Fatalf("bonus return count incorrect")
	}
}
