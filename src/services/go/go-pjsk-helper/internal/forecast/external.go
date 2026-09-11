package forecast

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

type SourceConfig struct {
	Enabled   bool
	Regions   map[string]bool
	URL       string
	EventsURL string
	LatestURL string
	Ranks     map[int]bool
}

func loadSourceConfig(settingsPath ...string) map[string]SourceConfig {
	cfg := map[string]SourceConfig{
		"33kit":   {Enabled: true, Regions: map[string]bool{"jp": true}, URL: "https://sekai-data.3-3.dev/predict.json"},
		"moe":     {Enabled: false, Regions: map[string]bool{"jp": true, "cn": true}, Ranks: rankSet([]int{50, 100, 200, 300, 400, 500, 1000, 2000, 3000, 4000, 5000, 10000})},
		"sekarun": {Enabled: true, Regions: map[string]bool{"jp": true, "tw": true}, URL: "https://jiiku831.github.io/{region}data/sekarun.js", Ranks: rankSet([]int{10, 30, 50, 100, 200, 300, 500, 1000, 2000, 3000, 5000, 10000, 50000, 100000})},
	}
	kit := cfg["33kit"]
	kit.Ranks = rankSet(ranks)
	cfg["33kit"] = kit
	if len(settingsPath) > 0 {
		if raw, err := os.ReadFile(settingsPath[0]); err == nil {
			var settings struct {
				Endpoints struct {
					Events string `yaml:"forecast_events_api_url"`
					Latest string `yaml:"forecast_latest_api_url"`
				} `yaml:"endpoints"`
			}
			if yaml.Unmarshal(raw, &settings) == nil && settings.Endpoints.Events != "" && settings.Endpoints.Latest != "" {
				item := cfg["moe"]
				item.Enabled = true
				item.EventsURL = settings.Endpoints.Events
				item.LatestURL = settings.Endpoints.Latest
				cfg["moe"] = item
			}
		}
	}
	if v := os.Getenv("PJSK_FORECAST_33KIT_URL"); v != "" {
		item := cfg["33kit"]
		item.URL = v
		cfg["33kit"] = item
	}
	if v := os.Getenv("PJSK_FORECAST_MOE_EVENTS_URL"); v != "" {
		item := cfg["moe"]
		item.EventsURL = v
		item.Enabled = item.LatestURL != ""
		cfg["moe"] = item
	}
	if v := os.Getenv("PJSK_FORECAST_MOE_LATEST_URL"); v != "" {
		item := cfg["moe"]
		item.LatestURL = v
		item.Enabled = item.EventsURL != ""
		cfg["moe"] = item
	}
	if v := os.Getenv("PJSK_FORECAST_SEKARUN_URL"); v != "" {
		item := cfg["sekarun"]
		item.URL = v
		cfg["sekarun"] = item
	}
	return cfg
}

func rankSet(values []int) map[int]bool {
	out := make(map[int]bool, len(values))
	for _, value := range values {
		out[value] = true
	}
	return out
}

func (g *Generator) GenerateExternal(ctx context.Context, source, region string, eventID int64) (int, error) {
	cfg, ok := g.sources[source]
	if !ok || !cfg.Enabled || !cfg.Regions[region] {
		return 0, nil
	}
	key := source + "/" + region
	g.errMu.Lock()
	last := g.lastErr[key]
	g.errMu.Unlock()
	if !last.IsZero() && time.Since(last) < 10*time.Minute {
		return 0, fmt.Errorf("source retry cooldown")
	}
	var data map[string]any
	var err error
	switch source {
	case "33kit":
		data, err = g.fetch33Kit(ctx, cfg, region, eventID)
	case "moe":
		data, err = g.fetchMoe(ctx, cfg, region, eventID)
	case "sekarun":
		data, err = g.fetchSekaRun(ctx, cfg, region, eventID)
	default:
		err = fmt.Errorf("unknown source %q", source)
	}
	if err != nil {
		g.errMu.Lock()
		g.lastErr[key] = time.Now()
		g.errMu.Unlock()
		return 0, err
	}
	if err := g.saveExternal(source, region, eventID, data); err != nil {
		return 0, err
	}
	g.errMu.Lock()
	delete(g.lastErr, key)
	g.errMu.Unlock()
	rankData, _ := data["rank_data"].(map[string]any)
	return len(rankData), nil
}

func (g *Generator) fetchJSON(ctx context.Context, endpoint string) ([]byte, error) {
	var last error
	for attempt := 0; attempt < 3; attempt++ {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
		if err != nil {
			return nil, err
		}
		resp, err := g.client.Do(req)
		if err == nil {
			body, readErr := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
			resp.Body.Close()
			if readErr == nil && resp.StatusCode == http.StatusOK {
				return body, nil
			}
			if readErr != nil {
				last = readErr
			} else {
				last = fmt.Errorf("HTTP %d", resp.StatusCode)
			}
		} else {
			last = err
		}
		if attempt < 2 {
			timer := time.NewTimer(time.Duration(1<<attempt) * time.Second)
			select {
			case <-timer.C:
			case <-ctx.Done():
				timer.Stop()
				return nil, ctx.Err()
			}
		}
	}
	return nil, last
}

func (g *Generator) fetch33Kit(ctx context.Context, cfg SourceConfig, region string, eventID int64) (map[string]any, error) {
	raw, err := g.fetchJSON(ctx, cfg.URL)
	if err != nil {
		return nil, err
	}
	var payload struct {
		Status string `json:"status"`
		Event  struct {
			ID int64 `json:"id"`
		} `json:"event"`
		Data map[string]json.RawMessage `json:"data"`
	}
	if err := json.Unmarshal(raw, &payload); err != nil {
		return nil, err
	}
	if payload.Status != "success" || payload.Event.ID != eventID {
		return nil, fmt.Errorf("33kit event mismatch")
	}
	entries := map[string]any{}
	var forecastTS int64
	for key, value := range payload.Data {
		if key == "ts" {
			_ = json.Unmarshal(value, &forecastTS)
			continue
		}
		rank, e := strconv.Atoi(key)
		if e != nil || (cfg.Ranks != nil && !cfg.Ranks[rank]) {
			continue
		}
		var score int64
		if json.Unmarshal(value, &score) == nil {
			entries[key] = map[string]any{"final_score": score, "history_final_score": nil, "future_rankings": nil}
		}
	}
	if len(entries) == 0 {
		return nil, fmt.Errorf("33kit returned no configured ranks")
	}
	return map[string]any{"source": "33kit", "region": region, "event_id": eventID, "forecast_ts": forecastTS, "rank_data": entries}, nil
}

func (g *Generator) fetchMoe(ctx context.Context, cfg SourceConfig, region string, eventID int64) (map[string]any, error) {
	eventsURL := strings.ReplaceAll(cfg.EventsURL, "{region}", region)
	latestURL := strings.ReplaceAll(strings.ReplaceAll(cfg.LatestURL, "{region}", region), "{event_id}", strconv.FormatInt(eventID, 10))
	raw, err := g.fetchJSON(ctx, eventsURL)
	if err != nil {
		return nil, err
	}
	var events []map[string]any
	if json.Unmarshal(raw, &events) != nil {
		return nil, fmt.Errorf("invalid moe events")
	}
	present := false
	for _, item := range events {
		if id, ok := number(item["event_id"]); ok && int64(id) == eventID {
			present = true
			break
		}
	}
	if !present {
		return nil, fmt.Errorf("moe event unavailable")
	}
	raw, err = g.fetchJSON(ctx, latestURL)
	if err != nil {
		return nil, err
	}
	var payload struct {
		EventID   int64            `json:"event_id"`
		UpdatedAt string           `json:"updated_at"`
		Items     []map[string]any `json:"items"`
	}
	if err := json.Unmarshal(raw, &payload); err != nil {
		return nil, err
	}
	if payload.EventID != eventID {
		return nil, fmt.Errorf("moe event mismatch")
	}
	ts, err := time.Parse(time.RFC3339, strings.Replace(payload.UpdatedAt, "Z", "+00:00", 1))
	if err != nil {
		return nil, err
	}
	entries := map[string]any{}
	for _, item := range payload.Items {
		rankN, ok := number(item["rank"])
		if !ok || !cfg.Ranks[int(rankN)] {
			continue
		}
		score, ok := number(item["prediction"])
		if !ok {
			score, ok = number(item["score"])
		}
		if ok {
			entries[strconv.Itoa(int(rankN))] = map[string]any{"final_score": int64(score), "history_final_score": nil, "future_rankings": nil}
		}
	}
	if len(entries) == 0 {
		return nil, fmt.Errorf("moe returned no configured ranks")
	}
	return map[string]any{"source": "moe", "region": region, "event_id": eventID, "forecast_ts": ts.Unix(), "rank_data": entries}, nil
}

func (g *Generator) fetchSekaRun(ctx context.Context, cfg SourceConfig, region string, eventID int64) (map[string]any, error) {
	endpoint := strings.ReplaceAll(cfg.URL, "{region}", region+"/")
	if region == "jp" {
		endpoint = strings.ReplaceAll(cfg.URL, "{region}", "")
	}
	raw, err := g.fetchJSON(ctx, endpoint)
	if err != nil {
		return nil, err
	}
	text := string(raw)
	rows := parseJSRows(text)
	entries := map[string]any{}
	var forecastTS int64
	for _, row := range rows {
		if len(row) <= 9 || strings.Trim(row[0], " '\"") != strconv.FormatInt(eventID, 10) || strings.Trim(row[1], " '\"") != "p" {
			continue
		}
		rank, e1 := strconv.Atoi(strings.Trim(row[5], " '\""))
		ts, e2 := strconv.ParseInt(strings.Trim(row[6], " '\""), 10, 64)
		lower, e3 := strconv.ParseFloat(strings.Trim(row[8], " '\""), 64)
		upper, e4 := strconv.ParseFloat(strings.Trim(row[9], " '\""), 64)
		if e1 != nil || e2 != nil || e3 != nil || e4 != nil || !cfg.Ranks[rank] {
			continue
		}
		if ts > forecastTS {
			forecastTS = ts
		}
		score := int64((lower + upper) / 2)
		key := strconv.Itoa(rank)
		old, exists := entries[key]
		if !exists || score > old.(map[string]any)["final_score"].(int64) {
			entries[key] = map[string]any{"final_score": score, "history_final_score": nil, "future_rankings": nil}
		}
	}
	if forecastTS == 0 || len(entries) == 0 {
		return nil, fmt.Errorf("sekarun returned no configured ranks")
	}
	return map[string]any{"source": "sekarun", "region": region, "event_id": eventID, "forecast_ts": forecastTS, "rank_data": entries}, nil
}

func parseJSRows(text string) [][]string {
	start := strings.Index(text, "[[")
	end := strings.LastIndex(text, "]]")
	if start < 0 || end <= start {
		return nil
	}
	body := text[start : end+2]
	var rows [][]string
	depth, begin := 0, -1
	quote := rune(0)
	for i, r := range body {
		if quote != 0 {
			if r == quote {
				quote = 0
			}
			continue
		}
		if r == '\'' || r == '"' {
			quote = r
			continue
		}
		if r == '[' {
			depth++
			if depth == 2 {
				begin = i + 1
			}
		}
		if r == ']' {
			if depth == 2 && begin >= 0 {
				rows = append(rows, splitJSFields(body[begin:i]))
				begin = -1
			}
			depth--
		}
	}
	return rows
}
func splitJSFields(s string) []string {
	var out []string
	start := 0
	quote := rune(0)
	for i, r := range s {
		if quote != 0 {
			if r == quote {
				quote = 0
			}
			continue
		}
		if r == '\'' || r == '"' {
			quote = r
		} else if r == ',' {
			out = append(out, strings.TrimSpace(s[start:i]))
			start = i + 1
		}
	}
	out = append(out, strings.TrimSpace(s[start:]))
	return out
}
func number(value any) (float64, bool) {
	switch n := value.(type) {
	case float64:
		return n, true
	case int64:
		return float64(n), true
	case json.Number:
		f, e := n.Float64()
		return f, e == nil
	case string:
		f, e := strconv.ParseFloat(strings.TrimSpace(n), 64)
		return f, e == nil
	default:
		return 0, false
	}
}

func (g *Generator) saveExternal(source, region string, eventID int64, data map[string]any) error {
	raw, err := json.MarshalIndent(data, "", "  ")
	if err != nil {
		return err
	}
	raw = append(raw, '\n')
	return atomicWrite(filepath.Join(g.dataDir, "ondemand", "forecast", source, region, "forecast", strconv.FormatInt(eventID, 10)+".json"), raw)
}
