// Package ranking：定时抓取排行 API 并写入 sktop100.json（与 Python 端共享文件协议）。
package ranking

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/kazuhira/go-pjsk-helper/internal/masterdata"
)

// Collector 定时抓取各服新排行 API 的 top100 数据。
type Collector struct {
	cfg    masterdata.Config
	outDir string
	client *http.Client

	mu     sync.RWMutex
	latest map[string]json.RawMessage // region -> 最近一次抓取的原始响应
}

func NewCollector(cfg masterdata.Config, outDir string) *Collector {
	return &Collector{
		cfg:    cfg,
		outDir: outDir,
		client: &http.Client{Timeout: 20 * time.Second},
		latest: map[string]json.RawMessage{},
	}
}

// Run 周期抓取。
func (c *Collector) Run(ctx context.Context, interval time.Duration) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	c.collectAll(ctx)
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			c.collectAll(ctx)
		}
	}
}

func (c *Collector) collectAll(ctx context.Context) {
	for region, rc := range c.cfg {
		url := rc.API.RankingTop100NewAPIURL
		if url == "" {
			continue
		}
		if err := c.collectOne(ctx, region, url); err != nil {
			log.Printf("[ranking] %s: %v", region, err)
		}
	}
}

// rankingPayload 兼容 {"data": {...}} 包装。
type rankingPayload struct {
	Data *struct {
		Rankings []json.RawMessage `json:"rankings"`
	} `json:"data"`
	Rankings []json.RawMessage `json:"rankings"`
}

func (c *Collector) collectOne(ctx context.Context, region, url string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	resp, err := c.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	if err != nil {
		return err
	}

	var payload rankingPayload
	if err := json.Unmarshal(raw, &payload); err != nil {
		return fmt.Errorf("decode: %w", err)
	}
	rankings := payload.Rankings
	if payload.Data != nil && len(payload.Data.Rankings) > 0 {
		rankings = payload.Data.Rankings
	}
	if len(rankings) == 0 {
		return fmt.Errorf("empty rankings")
	}

	c.mu.Lock()
	c.latest[region] = json.RawMessage(raw)
	c.mu.Unlock()

	// 写入 Python 端 callapi 使用的本地缓存文件格式：{"rankings": [...]}
	out := map[string]any{"rankings": rankings}
	data, err := json.Marshal(out)
	if err != nil {
		return err
	}
	dir := filepath.Join(c.outDir, region)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	tmp := filepath.Join(dir, ".sktop100.json.tmp")
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, filepath.Join(dir, "sktop100.json"))
}

// HandleLatest 处理 GET /ranking/{region}/latest，返回最近一次抓取的原始响应。
func (c *Collector) HandleLatest(w http.ResponseWriter, r *http.Request) {
	region := r.PathValue("region")
	c.mu.RLock()
	raw, ok := c.latest[region]
	c.mu.RUnlock()
	if !ok {
		http.Error(w, "no data yet", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write(raw)
}
