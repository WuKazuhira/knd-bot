package skforecast

import (
	"os"
	"path/filepath"
	"testing"
)

func TestReadCachedAndDisplayRanks(t *testing.T) {
	dir := t.TempDir()
	r := New(dir)

	// 写一个 local/jp 预测缓存文件
	path := r.savePath("local", "jp", 123)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	content := `{
		"forecast_ts": 1700000000,
		"rank_data": {
			"100": {"final_score": 12000000, "history_final_score": [{"score":100,"ts":1}], "future_rankings": [{"score":200,"ts":2}]},
			"777": {"final_score": 5000000}
		}
	}`
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}

	forecasts := r.ReadCached("jp", 123)
	if len(forecasts) != 1 {
		t.Fatalf("应读到 1 个预测源, got %d", len(forecasts))
	}
	fc := forecasts[0]
	if fc["source"] != "local" || fc["region"] != "jp" || fc["event_id"] != 123 {
		t.Errorf("元数据不符: %v", fc)
	}
	if fc["forecast_ts"].(int64) != 1700000000 {
		t.Errorf("forecast_ts 不符: %v", fc["forecast_ts"])
	}
	rd := fc["rank_data"].(map[string]any)
	r100 := rd["100"].(map[string]any)
	if r100["final_score"].(int64) != 12000000 {
		t.Errorf("final_score 不符: %v", r100["final_score"])
	}
	if len(r100["history_final_score"].([]map[string]any)) != 1 {
		t.Errorf("history 应有 1 条")
	}

	// DisplayRanks 合并配置档位 + 缓存出现的 777
	ranks := DisplayRanks(forecasts)
	found777 := false
	found100 := false
	for _, x := range ranks {
		if x == 777 {
			found777 = true
		}
		if x == 100 {
			found100 = true
		}
	}
	if !found777 || !found100 {
		t.Errorf("DisplayRanks 应含 100 与 777: %v", ranks)
	}
	// 有序
	for i := 1; i < len(ranks); i++ {
		if ranks[i] < ranks[i-1] {
			t.Errorf("DisplayRanks 应升序: %v", ranks)
		}
	}
}

func TestReadCachedRegionFilter(t *testing.T) {
	dir := t.TempDir()
	r := New(dir)
	// 33kit 只支持 jp；给 cn 写文件不应被 cn 查询读到（因为 cn 不在 33kit regions）
	path := r.savePath("33kit", "cn", 1)
	os.MkdirAll(filepath.Dir(path), 0o755)
	os.WriteFile(path, []byte(`{"rank_data":{}}`), 0o644)

	if got := r.ReadCached("cn", 1); len(got) != 0 {
		t.Errorf("cn 不应读到 33kit 源: %v", got)
	}
}

func TestReadCachedMissing(t *testing.T) {
	r := New(t.TempDir())
	if got := r.ReadCached("jp", 999); got != nil {
		t.Errorf("缺失缓存应返回 nil: %v", got)
	}
}
