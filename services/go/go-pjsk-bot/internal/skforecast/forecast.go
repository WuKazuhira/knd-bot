package skforecast

import (
	"encoding/json"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

type forecastSource struct {
	name    string
	regions map[string]bool
}

var sources = []forecastSource{
	{"local", map[string]bool{"jp": true, "cn": true, "tw": true}},
	{"33kit", map[string]bool{"jp": true}},
	{"moe", map[string]bool{"jp": true, "cn": true}},
	{"sekarun", map[string]bool{"jp": true, "tw": true}},
}

type Reader struct {
	root        string
	helperURL   string
	http        *http.Client
	refreshMu   sync.Mutex
	lastRefresh map[string]time.Time
}

func New(dataDir string) *Reader {
	return NewWithHelper(dataDir, os.Getenv("PJSK_HELPER_URL"))
}

func NewWithHelper(dataDir, helperURL string) *Reader {
	if strings.TrimSpace(helperURL) == "" {
		helperURL = os.Getenv("HELPER_SERVICE_URL")
	}
	return &Reader{root: filepath.Join(dataDir, "ondemand", "forecast"), helperURL: strings.TrimRight(helperURL, "/"), http: &http.Client{Timeout: 10 * time.Second}, lastRefresh: make(map[string]time.Time)}
}

func (r *Reader) savePath(source, region string, eventID int) string {
	return filepath.Join(r.root, source, region, "forecast", itoa(eventID)+".json")
}

type rawForecastFile struct {
	ForecastTS *int64                     `json:"forecast_ts"`
	RankData   map[string]rawRankForecast `json:"rank_data"`
}

type rawRankForecast struct {
	FinalScore        *int64       `json:"final_score"`
	HistoryFinalScore []rawRanking `json:"history_final_score"`
	FutureRankings    []rawRanking `json:"future_rankings"`
}

type rawRanking struct {
	Score *int64 `json:"score"`
	TS    *int64 `json:"ts"`
}

func (r *Reader) ReadCached(region string, eventID int) []map[string]any {
	out := r.readAll(region, eventID)
	if r.helperURL != "" && r.needsRefresh(region, eventID, out) {
		if len(out) > 0 {
			go r.refresh(region, eventID)
		} else {
			r.refresh(region, eventID)
			out = r.readAll(region, eventID)
		}
	}
	return out
}

func (r *Reader) readAll(region string, eventID int) []map[string]any {
	var out []map[string]any
	for _, src := range sources {
		if !src.regions[region] {
			continue
		}
		if fc := r.readOne(src.name, region, eventID); fc != nil {
			out = append(out, fc)
		}
	}
	return out
}

func (r *Reader) needsRefresh(region string, eventID int, cached []map[string]any) bool {
	for _, src := range sources {
		if src.regions[region] {
			found := false
			for _, item := range cached {
				if item["source"] == src.name {
					found = true
					break
				}
			}
			if !found {
				return true
			}
		}
	}
	return false
}

func (r *Reader) refresh(region string, eventID int) {
	key := region + "/" + strconv.Itoa(eventID)
	r.refreshMu.Lock()
	if last := r.lastRefresh[key]; !last.IsZero() && time.Since(last) < 2*time.Minute {
		r.refreshMu.Unlock()
		return
	}
	r.lastRefresh[key] = time.Now()
	r.refreshMu.Unlock()
	endpoint := r.helperURL + "/forecast/refresh?region=" + url.QueryEscape(region) + "&event_id=" + url.QueryEscape(strconv.Itoa(eventID))
	resp, err := r.http.Post(endpoint, "", nil)
	if err == nil {
		resp.Body.Close()
	}
}

func (r *Reader) readOne(source, region string, eventID int) map[string]any {
	path := r.savePath(source, region, eventID)
	info, err := os.Stat(path)
	if err != nil {
		return nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var raw rawForecastFile
	if json.Unmarshal(data, &raw) != nil {
		return nil
	}

	rankData := make(map[string]any, len(raw.RankData))
	for rankStr, ri := range raw.RankData {
		entry := map[string]any{
			"final_score":         nullableInt(ri.FinalScore),
			"history_final_score": convRankings(ri.HistoryFinalScore),
			"future_rankings":     convRankings(ri.FutureRankings),
		}
		rankData[rankStr] = entry
	}

	return map[string]any{
		"source":      source,
		"region":      region,
		"event_id":    eventID,
		"mtime":       info.ModTime().Unix(),
		"forecast_ts": nullableInt(raw.ForecastTS),
		"rank_data":   rankData,
	}
}

func DisplayRanks(forecasts []map[string]any) []int {
	set := map[int]bool{}
	for _, r := range RankLevels {
		set[r] = true
	}
	for _, r := range LiveRanks {
		set[r] = true
	}
	for _, fc := range forecasts {
		rd, _ := fc["rank_data"].(map[string]any)
		for rankStr := range rd {
			if r := atoi(rankStr); r > 0 {
				set[r] = true
			}
		}
	}
	out := make([]int, 0, len(set))
	for r := range set {
		if r > 0 {
			out = append(out, r)
		}
	}
	sort.Ints(out)
	return out
}

var RankLevels = []int{
	1, 2, 3, 4, 5, 10, 20, 30, 40, 50, 100, 200, 300, 400, 500, 1000, 2000, 3000, 4000, 5000, 10000,
	20000, 30000, 40000, 50000, 100000,
}

var LiveRanks = []int{
	10, 20, 30, 40, 50, 100,
	200, 300, 400, 500,
	1000, 2000, 3000, 4000, 5000,
	10000,
}

func convRankings(in []rawRanking) []map[string]any {
	if len(in) == 0 {
		return nil
	}
	out := make([]map[string]any, 0, len(in))
	for _, r := range in {
		if r.Score == nil || r.TS == nil {
			continue
		}
		out = append(out, map[string]any{"score": *r.Score, "ts": *r.TS})
	}
	return out
}

func nullableInt(p *int64) any {
	if p == nil {
		return nil
	}
	return *p
}

func itoa(n int) string { return strconv.Itoa(n) }

func atoi(s string) int {
	n, err := strconv.Atoi(s)
	if err != nil {
		return 0
	}
	return n
}
