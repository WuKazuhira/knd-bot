// Package skforecast 读取 helper 生成的 sk 活动预测缓存 JSON，
// 对齐 old-python sk._forecast 的 get_forecast_data_cached 结构。
//
// Go 侧优先读取共享 forecast JSON；缓存缺失且配置了 helper 时，会触发
// helper /forecast/refresh，再重新读取。这样 sk预测/ycx曲线不依赖 Python
// 进程生成预测，仍保持与 Python asdict(ForecastData) 相同的载荷结构。
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

// forecastSource 描述一个预测源及其支持的服务器。
type forecastSource struct {
	name    string
	regions map[string]bool
}

// sources 对齐 FORECAST_SOURCES 的 name/regions（读取用，忽略网络配置）。
// 顺序即遍历顺序，与 Python FORECAST_GET_FUNCS 保持一致。
var sources = []forecastSource{
	{"local", map[string]bool{"jp": true, "cn": true, "tw": true}},
	{"33kit", map[string]bool{"jp": true}},
	{"moe", map[string]bool{"jp": true, "cn": true}},
	{"sekarun", map[string]bool{"jp": true, "tw": true}},
}

// Reader 读取预测缓存。
type Reader struct {
	root        string // {dataDir}/ondemand/forecast
	helperURL   string
	http        *http.Client
	refreshMu   sync.Mutex
	lastRefresh map[string]time.Time
}

// New 创建 Reader。dataDir 为 data/pjsk；helper 地址读取 PJSK_HELPER_URL。
func New(dataDir string) *Reader {
	return NewWithHelper(dataDir, os.Getenv("PJSK_HELPER_URL"))
}

// NewWithHelper 创建带 helper 地址的 Reader，供 Go sk 命令直接触发预测刷新。
func NewWithHelper(dataDir, helperURL string) *Reader {
	if strings.TrimSpace(helperURL) == "" {
		helperURL = os.Getenv("HELPER_SERVICE_URL")
	}
	return &Reader{root: filepath.Join(dataDir, "ondemand", "forecast"), helperURL: strings.TrimRight(helperURL, "/"), http: &http.Client{Timeout: 10 * time.Second}, lastRefresh: make(map[string]time.Time)}
}

// savePath 对齐 ForecastData.get_save_path。
func (r *Reader) savePath(source, region string, eventID int) string {
	return filepath.Join(r.root, source, region, "forecast", itoa(eventID)+".json")
}

// rawForecastFile 是磁盘 JSON 的结构（对齐 save_to_local 写出的字段）。
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

// ReadCached 读取某服某活动全部可用预测源的缓存，返回可直接作为 pjsk-draw
// forecasts 载荷的列表（每项与 asdict(ForecastData) 同形状）。
// 只读缓存文件，缺失的源跳过，不主动生成或联网。对齐 get_forecast_data_cached。
func (r *Reader) ReadCached(region string, eventID int) []map[string]any {
	out := r.readAll(region, eventID)
	if r.helperURL != "" && r.needsRefresh(region, eventID, out) {
		r.refresh(region, eventID)
		out = r.readAll(region, eventID)
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

// readOne 读取单个源的缓存文件，还原成 asdict 形状；文件缺失/损坏返回 nil。
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

// DisplayRanks 合并配置档位、实时档位与各预测缓存出现的档位，生成展示行。
// 对齐 _resolve_forecast_display_ranks。
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

// RankLevels 对齐 old-python rank_levels。
var RankLevels = []int{
	1, 2, 3, 4, 5, 10, 20, 30, 40, 50, 100, 200, 300, 400, 500, 1000, 2000, 3000, 4000, 5000, 10000,
	20000, 30000, 40000, 50000, 100000,
}

// LiveRanks 对齐 old-python LIVE_RANKS（实时分数/时速展示档位）。
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

// atoi 解析十进制整数，失败返回 0。
func atoi(s string) int {
	n, err := strconv.Atoi(s)
	if err != nil {
		return 0
	}
	return n
}
