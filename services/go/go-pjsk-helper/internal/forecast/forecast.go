package forecast

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"sync"
	"time"

	"github.com/kazuhira/go-pjsk-helper/internal/shared"
	_ "modernc.org/sqlite"
)

var regions = []string{"jp", "tw", "cn"}
var ranks = []int{10, 20, 30, 40, 50, 100, 200, 300, 400, 500, 1000, 2000, 3000, 4000, 5000, 10000, 50000, 100000}

type Generator struct {
	dataDir string
	sources map[string]SourceConfig
	client  *http.Client
	errMu   sync.Mutex
	lastErr map[string]time.Time
}

func NewGenerator(dataDir string, settingsPath ...string) *Generator {
	return &Generator{
		dataDir: dataDir,
		sources: loadSourceConfig(settingsPath...),
		client:  &http.Client{Timeout: 15 * time.Second},
		lastErr: make(map[string]time.Time),
	}
}

func (g *Generator) Run(ctx context.Context, interval time.Duration) {
	if interval <= 0 {
		interval = 20 * time.Minute
	}
	g.RefreshAll(ctx)
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			g.RefreshAll(ctx)
		}
	}
}

func (g *Generator) RefreshAll(ctx context.Context) {
	for _, region := range regions {
		if ctx.Err() != nil {
			return
		}
		if eventID, ok := g.currentEvent(region); ok {
			if _, err := g.Generate(ctx, region, eventID); err != nil {
				fmt.Printf("[forecast] local %s/%d: %v\n", region, eventID, err)
			}
			for _, source := range []string{"33kit", "moe", "sekarun"} {
				if _, err := g.GenerateExternal(ctx, source, region, eventID); err != nil {
					fmt.Printf("[forecast] %s %s/%d: %v\n", source, region, eventID, err)
				}
			}
		}
	}
}

func (g *Generator) Generate(ctx context.Context, region string, eventID int64) (int, error) {
	if region != "jp" && region != "tw" && region != "cn" {
		return 0, fmt.Errorf("unknown region %q", region)
	}
	start, end, err := g.eventRange(region, eventID)
	if err != nil {
		return 0, err
	}
	if end <= start {
		return 0, fmt.Errorf("invalid event range")
	}
	dbDir := filepath.Join(g.dataDir, "ondemand", "database", "sk_"+region)
	archiveDir := filepath.Join(g.dataDir, "archive", "ranking", "sk_"+region)
	db, hasRuns, err := g.openRankingDB(eventID, dbDir, archiveDir)
	if err != nil {
		return 0, err
	}
	defer db.Close()

	entries := map[string]map[string]any{}
	now := time.Now().Unix()
	for _, rank := range ranks {
		points, err := queryRankingPoints(db, hasRuns, rank, now, end)
		if err != nil {
			continue
		}
		points = filterPoints(points, start, end)
		if len(points) < 2 {
			continue
		}
		latest := points[len(points)-1]
		if latest.ts >= end {
			continue
		}
		slope := estimateSlope(points)
		if slope < 0 {
			slope = 0
		}
		final := latest.score + int64(slope*float64(end-latest.ts))
		if final < latest.score {
			final = latest.score
		}
		future := make([]map[string]int64, 0, 80)
		for i := 1; i <= 80; i++ {
			ts := latest.ts + int64(float64(end-latest.ts)*float64(i)/80)
			if ts > end {
				ts = end
			}
			score := latest.score + int64(slope*float64(ts-latest.ts))
			future = append(future, map[string]int64{"score": score, "ts": ts})
		}
		history := make([]map[string]int64, 0, len(points)+1)
		for _, p := range points {
			history = append(history, map[string]int64{"score": p.score, "ts": p.ts})
		}
		history = append(history, map[string]int64{"score": final, "ts": now})
		entries[strconv.Itoa(rank)] = map[string]any{"final_score": final, "history_final_score": history, "future_rankings": future}
	}
	if len(entries) == 0 {
		return 0, fmt.Errorf("insufficient ranking history")
	}
	data := map[string]any{"source": "local", "region": region, "event_id": eventID, "forecast_ts": now, "rank_data": entries}
	raw, err := json.MarshalIndent(data, "", "  ")
	if err != nil {
		return 0, err
	}
	raw = append(raw, '\n')
	path := filepath.Join(g.dataDir, "ondemand", "forecast", "local", region, "forecast", strconv.FormatInt(eventID, 10)+".json")
	if err := shared.WriteAtomic(path, raw); err != nil {
		return 0, err
	}
	return len(entries), nil
}

type point struct{ score, ts int64 }

func (g *Generator) openRankingDB(eventID int64, dirs ...string) (*sql.DB, bool, error) {
	fname := strconv.FormatInt(eventID, 10) + "_ranking.db"
	var lastErr error
	for _, d := range dirs {
		if d == "" {
			continue
		}
		path := filepath.Join(d, fname)
		if _, err := os.Stat(path); err != nil {
			lastErr = err
			continue
		}
		db, err := sql.Open("sqlite", "file:"+path+"?mode=ro&_pragma=busy_timeout(5000)")
		if err != nil {
			lastErr = err
			continue
		}
		var hasRuns int
		if err := db.QueryRow("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs' LIMIT 1").Scan(&hasRuns); err != nil && err != sql.ErrNoRows {
			db.Close()
			return nil, false, err
		}
		return db, hasRuns == 1, nil
	}
	if lastErr != nil {
		return nil, false, lastErr
	}
	return nil, false, os.ErrNotExist
}

// queryRankingPoints 按分钟桶采样近 3 小时历史点，避免 1s 高频采集下 LIMIT 128 只剩两分钟导致斜率失真。
func queryRankingPoints(db *sql.DB, hasRuns bool, rank int, now, end int64) ([]point, error) {
	windowStart := now - 3*3600
	if hasRuns {
		points, err := scanPoints(db.Query(`
			WITH bucketed AS (
				SELECT MAX(b.batch_id) AS batch_id, MAX(b.ts) AS ts
				FROM sig_ranks AS sr
				JOIN batches AS b ON b.sig_id = sr.sig_id
				WHERE sr.rank = ? AND b.ts >= ? AND b.ts <= ?
				GROUP BY (b.ts / 60)
				ORDER BY batch_id DESC
				LIMIT 180
			)
			SELECT r.score, rc.ts
			FROM bucketed AS rc
			JOIN runs AS r ON r.rank = ? AND r.start_batch = (
				SELECT start_batch FROM runs
				WHERE rank = ? AND start_batch <= rc.batch_id
				ORDER BY start_batch DESC LIMIT 1
			) AND r.end_batch >= rc.batch_id
			ORDER BY rc.batch_id ASC`, rank, windowStart, end, rank, rank))
		if err == nil && len(points) >= 2 {
			return points, nil
		}
		return scanPoints(db.Query(`
			WITH recent AS (
				SELECT b.batch_id, b.ts
				FROM sig_ranks AS sr
				JOIN batches AS b ON b.sig_id = sr.sig_id
				WHERE sr.rank = ? AND b.ts <= ?
				ORDER BY b.batch_id DESC
				LIMIT 128
			)
			SELECT r.score, rc.ts
			FROM recent AS rc
			JOIN runs AS r ON r.rank = ? AND r.start_batch = (
				SELECT start_batch FROM runs
				WHERE rank = ? AND start_batch <= rc.batch_id
				ORDER BY start_batch DESC LIMIT 1
			) AND r.end_batch >= rc.batch_id
			ORDER BY rc.batch_id ASC`, rank, end, rank, rank))
	}
	points, err := scanPoints(db.Query(
		"SELECT MAX(score), MAX(ts) FROM ranking WHERE rank = ? AND ts >= ? AND ts <= ? GROUP BY (ts / 60) ORDER BY MAX(ts) ASC LIMIT 180",
		rank, windowStart, end,
	))
	if err == nil && len(points) >= 2 {
		return points, nil
	}
	return scanPoints(db.Query("SELECT score, ts FROM ranking WHERE rank = ? AND ts <= ? ORDER BY ts DESC LIMIT 128", rank, end))
}

func scanPoints(rows *sql.Rows, err error) ([]point, error) {
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []point
	for rows.Next() {
		var p point
		if err := rows.Scan(&p.score, &p.ts); err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, rows.Err()
}
func filterPoints(in []point, start, end int64) []point {
	out := make([]point, 0, len(in))
	seen := map[int64]bool{}
	for _, p := range in {
		if p.ts >= start && p.ts <= end && !seen[p.ts] {
			seen[p.ts] = true
			out = append(out, p)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ts < out[j].ts })
	return out
}
func estimateSlope(points []point) float64 {
	n := len(points)
	if n > 12 {
		points = points[n-12:]
	}
	if len(points) < 2 {
		return 0
	}
	first, last := points[0], points[len(points)-1]
	dt := last.ts - first.ts
	if dt <= 0 {
		return 0
	}
	ds := last.score - first.score
	if ds < 0 {
		return 0
	}
	return float64(ds) / float64(dt)
}

func (g *Generator) currentEvent(region string) (int64, bool) {
	raw, err := os.ReadFile(filepath.Join(g.dataDir, "ondemand", region, "events.json"))
	if err != nil {
		return 0, false
	}
	var events []struct {
		ID    int64 `json:"id"`
		Start int64 `json:"startAt"`
		End   int64 `json:"aggregateAt"`
	}
	if json.Unmarshal(raw, &events) != nil {
		return 0, false
	}
	now := time.Now().UnixMilli()
	for _, e := range events {
		if e.Start < now && now < e.End {
			return e.ID, true
		}
	}
	return 0, false
}
func (g *Generator) eventRange(region string, eventID int64) (int64, int64, error) {
	raw, err := os.ReadFile(filepath.Join(g.dataDir, "ondemand", region, "events.json"))
	if err != nil {
		return 0, 0, err
	}
	var events []struct {
		ID    int64 `json:"id"`
		Start int64 `json:"startAt"`
		End   int64 `json:"aggregateAt"`
	}
	if err := json.Unmarshal(raw, &events); err != nil {
		return 0, 0, err
	}
	for _, e := range events {
		if e.ID == eventID {
			return e.Start / 1000, e.End / 1000, nil
		}
	}
	return 0, 0, fmt.Errorf("event %d not found", eventID)
}

func (g *Generator) HandleRefresh(w http.ResponseWriter, r *http.Request) {
	region := r.URL.Query().Get("region")
	if region == "" {
		region = "jp"
	}
	id, _ := strconv.ParseInt(r.URL.Query().Get("event_id"), 10, 64)
	if id == 0 {
		var ok bool
		id, ok = g.currentEvent(region)
		if !ok {
			http.Error(w, "no current event", http.StatusNotFound)
			return
		}
	}
	count, err := g.Generate(r.Context(), region, id)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_, _ = fmt.Fprintf(w, `{"ok":true,"region":%q,"event_id":%d,"ranks":%d}\n`, region, id, count)
}
