// Package skforecast 读取 sk 活动预测缓存 JSON（由 Python 定时任务生成），
// 对齐 old-python sk._forecast 的 get_forecast_data_cached 读取路径。
//
// Go 侧只做「读现有 forecast JSON」：遍历各预测源，从
// {dataDir}/ondemand/forecast/{source}/{region}/forecast/{event_id}.json
// 读取缓存并还原成与 asdict(ForecastData) 一致的结构，供 pjsk-draw 的
// sk_forecast 渲染器使用。预测数据的生成/联网获取仍由 Python 承担。
package skforecast

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strconv"
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
	root string // {dataDir}/ondemand/forecast
}

// New 创建 Reader。dataDir 为 data/pjsk。
func New(dataDir string) *Reader {
	return &Reader{root: filepath.Join(dataDir, "ondemand", "forecast")}
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
	var out []map[string]any
	for _, src := range sources {
		if !src.regions[region] {
			continue
		}
		fc := r.readOne(src.name, region, eventID)
		if fc != nil {
			out = append(out, fc)
		}
	}
	return out
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
