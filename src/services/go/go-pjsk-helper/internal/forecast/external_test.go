package forecast

import (
	"os"
	"path/filepath"
	"testing"
)

func TestParseJSRows(t *testing.T) {
	rows := parseJSRows(`const data = [["123", "p", "x", "x", "x", "100", "1700000000", "x", "120000", "130000"], ["124", "f"]];`)
	if len(rows) != 2 || len(rows[0]) != 10 || rows[0][5] != `"100"` {
		t.Fatalf("rows=%v", rows)
	}
}

func TestLoadSourceConfigFromSettings(t *testing.T) {
	path := filepath.Join(t.TempDir(), "settings.yaml")
	if err := os.WriteFile(path, []byte("endpoints:\n  forecast_events_api_url: https://example/events?region={region}\n  forecast_latest_api_url: https://example/latest/{event_id}?region={region}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := loadSourceConfig(path)
	if !cfg["moe"].Enabled || cfg["moe"].EventsURL == "" || !cfg["moe"].Regions["cn"] {
		t.Fatalf("moe config=%+v", cfg["moe"])
	}
}
