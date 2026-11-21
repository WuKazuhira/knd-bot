// Package sheets 同步 Google Sheets 定数表，生成 Python/Go 共用的 constants.csv。
package sheets

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/kazuhira/go-pjsk-helper/internal/masterdata"
)

const defaultPrimary = "https://docs.google.com/spreadsheets/d/1Yv3GXnCIgEIbHL72EuZ-d5q_l-auPgddWi4Efa14jq0/export?format=csv&gid=182216"
const defaultOverride = "https://docs.google.com/spreadsheets/d/1rtkwNcfqQFoe8wAtD8tOVtm-u7SRGNUeX6faNi51tQI/gviz/tq?tqx=out:csv&gid=1631453602&range=C2:H616"

type Syncer struct {
	cfg      masterdata.Config
	dataDir  string
	client   *http.Client
	primary  string
	override string
}

func NewSyncer(cfg masterdata.Config, dataDir string) *Syncer {
	primary := os.Getenv("PJSK_DIFFICULTY_SHEET_URL")
	if primary == "" {
		primary = defaultPrimary
	}
	override := os.Getenv("PJSK_MASTER_DIFFICULTY_SHEET_URL")
	if override == "" {
		override = defaultOverride
	}
	return &Syncer{cfg: cfg, dataDir: dataDir, client: &http.Client{Timeout: 45 * time.Second}, primary: primary, override: override}
}

func (s *Syncer) Run(ctx context.Context, interval time.Duration) {
	if interval <= 0 {
		interval = 24 * time.Hour
	}
	s.Sync(ctx)
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			s.Sync(ctx)
		}
	}
}

// Sync 只在至少得到一份有效远端数据时覆盖文件，失败保留旧缓存。
func (s *Syncer) Sync(ctx context.Context) (bool, error) {
	primary, err := s.fetchRetry(ctx, s.primary)
	if err != nil {
		return false, err
	}
	rows := parsePrimary(primary)
	if len(rows) == 0 {
		return false, fmt.Errorf("primary sheet contains no constants")
	}
	if s.override != "" {
		if raw, e := s.fetchRetry(ctx, s.override); e == nil {
			rows = mergeOverrides(rows, parseOverride(raw, s.dataDir))
		}
	}
	rows = fillMissingMaster(rows, filepath.Join(s.dataDir, "ondemand", "jp", "musicDifficulties.json"))
	path := filepath.Join(s.dataDir, "ondemand", "jp", "realtime", "constants.csv")
	data := encodeRows(rows)
	if old, e := os.ReadFile(path); e == nil && string(old) == string(data) {
		return false, nil
	}
	if err := atomicWrite(path, data); err != nil {
		return false, err
	}
	return true, nil
}

func (s *Syncer) HandleRefresh(w http.ResponseWriter, r *http.Request) {
	changed, err := s.Sync(r.Context())
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_, _ = fmt.Fprintf(w, `{"ok":true,"changed":%v}\n`, changed)
}

func (s *Syncer) fetchRetry(ctx context.Context, endpoint string) ([]byte, error) {
	var last error
	for attempt := 0; attempt < 3; attempt++ {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
		if err != nil {
			return nil, err
		}
		resp, err := s.client.Do(req)
		if err == nil {
			body, readErr := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
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
		if attempt < 2 && !wait(ctx, time.Duration(1<<attempt)*time.Second) {
			return nil, ctx.Err()
		}
	}
	return nil, last
}

type constantRow struct {
	ID         int
	Difficulty string
	Value      float64
}

func parsePrimary(raw []byte) []constantRow {
	reader := csv.NewReader(strings.NewReader(string(raw)))
	var out []constantRow
	first := true
	for {
		row, err := reader.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			break
		}
		if first {
			first = false
			continue
		}
		if len(row) < 7 {
			continue
		}
		value, e1 := strconv.ParseFloat(strings.TrimSpace(row[2]), 64)
		id, e2 := strconv.Atoi(strings.TrimSpace(row[6]))
		diff := strings.ToLower(strings.TrimSpace(row[5]))
		if e1 != nil || e2 != nil || diff == "" {
			continue
		}
		out = append(out, constantRow{ID: id, Difficulty: diff, Value: value})
	}
	return out
}

func parseOverride(raw []byte, dataDir string) []constantRow {
	titles := loadTitles(filepath.Join(dataDir, "ondemand", "jp", "musics.json"))
	if len(titles) == 0 {
		return nil
	}
	reader := csv.NewReader(strings.NewReader(string(raw)))
	var out []constantRow
	for {
		row, err := reader.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			break
		}
		if len(row) < 6 {
			continue
		}
		value := firstNumber(row[5])
		if value == 0 {
			continue
		}
		name := normalize(strings.Join(row[:5], " "))
		best, score := 0, 0.0
		for id, title := range titles {
			n := normalize(title)
			if n == "" {
				continue
			}
			if strings.Contains(name, n) {
				best, score = id, 1
				break
			}
			if v := similarity(name, n); v > score {
				best, score = id, v
			}
		}
		if best != 0 && score >= 0.92 {
			out = append(out, constantRow{ID: best, Difficulty: "master", Value: value})
		}
	}
	return out
}

func fillMissingMaster(rows []constantRow, path string) []constantRow {
	raw, err := os.ReadFile(path)
	if err != nil {
		return rows
	}
	var items []map[string]any
	if json.Unmarshal(raw, &items) != nil {
		return rows
	}
	seen := make(map[string]bool, len(rows))
	for _, row := range rows {
		seen[key(row)] = true
	}
	out := append([]constantRow(nil), rows...)
	for _, item := range items {
		id, ok := masterdata.IntField(item, "musicId")
		level, levelOK := numberValue(item["playLevel"])
		if !ok || !levelOK || strings.ToLower(fmt.Sprint(item["musicDifficulty"])) != "master" {
			continue
		}
		row := constantRow{ID: int(id), Difficulty: "master", Value: level}
		if !seen[key(row)] {
			out = append(out, row)
			seen[key(row)] = true
		}
	}
	return out
}

func numberValue(value any) (float64, bool) {
	switch n := value.(type) {
	case float64:
		return n, true
	case float32:
		return float64(n), true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	case json.Number:
		f, err := n.Float64()
		return f, err == nil
	case string:
		f, err := strconv.ParseFloat(strings.TrimSpace(n), 64)
		return f, err == nil
	default:
		return 0, false
	}
}

func mergeOverrides(base, overrides []constantRow) []constantRow {
	m := make(map[string]constantRow, len(base)+len(overrides))
	for _, row := range base {
		m[key(row)] = row
	}
	for _, row := range overrides {
		m[key(row)] = row
	}
	out := make([]constantRow, 0, len(m))
	for _, row := range m {
		out = append(out, row)
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].ID != out[j].ID {
			return out[i].ID < out[j].ID
		}
		return out[i].Difficulty < out[j].Difficulty
	})
	return out
}

func encodeRows(rows []constantRow) []byte {
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].ID != rows[j].ID {
			return rows[i].ID < rows[j].ID
		}
		return rows[i].Difficulty < rows[j].Difficulty
	})
	var b strings.Builder
	w := csv.NewWriter(&b)
	_ = w.Write([]string{"id", "difficulty", "constant"})
	for _, row := range rows {
		_ = w.Write([]string{strconv.Itoa(row.ID), row.Difficulty, strconv.FormatFloat(row.Value, 'f', -1, 64)})
	}
	w.Flush()
	return []byte(b.String())
}
func key(row constantRow) string { return strconv.Itoa(row.ID) + ":" + row.Difficulty }
func firstNumber(s string) float64 {
	re := regexp.MustCompile(`\d+(?:\.\d+)?`)
	m := re.FindString(s)
	if m == "" {
		return 0
	}
	v, _ := strconv.ParseFloat(m, 64)
	return v
}
func normalize(s string) string {
	var b strings.Builder
	for _, r := range strings.ToLower(s) {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			b.WriteRune(r)
		}
	}
	return b.String()
}
func similarity(a, b string) float64 {
	if a == b {
		return 1
	}
	if a == "" || b == "" {
		return 0
	}
	common := 0
	used := make(map[rune]bool)
	for _, r := range a {
		if strings.ContainsRune(b, r) && !used[r] {
			used[r] = true
			common++
		}
	}
	return float64(common*2) / float64(len([]rune(a))+len([]rune(b)))
}
func loadTitles(path string) map[int]string {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var list []map[string]any
	if json.Unmarshal(raw, &list) != nil {
		return nil
	}
	out := map[int]string{}
	for _, item := range list {
		id, ok := masterdata.IntField(item, "id")
		title, okTitle := item["title"].(string)
		if ok && okTitle {
			out[int(id)] = title
		}
	}
	return out
}
func wait(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-t.C:
		return true
	case <-ctx.Done():
		return false
	}
}
func atomicWrite(path string, data []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".constants.csv.tmp*")
	if err != nil {
		return err
	}
	name := tmp.Name()
	if _, err = tmp.Write(data); err != nil {
		tmp.Close()
		_ = os.Remove(name)
		return err
	}
	if err = tmp.Close(); err != nil {
		_ = os.Remove(name)
		return err
	}
	if err = os.Rename(name, path); err != nil {
		_ = os.Remove(name)
		return err
	}
	return nil
}
