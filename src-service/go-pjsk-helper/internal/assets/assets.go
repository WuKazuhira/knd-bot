// Package assets：游戏资源（缩略图/卡面/头像等）批量并发下载。
//
// 与 Python 端 _autoask.update_server_assets 的 URL 映射规则保持一致，
// 下载落盘到共享 volume 的 data/pjsk/masterdata/{region}/{path}/{file}。
package assets

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/kazuhira/go-pjsk-helper/internal/masterdata"
)

// 与 Python 端 _RIP_ONDEMAND_PREFIXES / _RIP_STARTAPP_PREFIXES 保持一致。
var ondemandPrefixes = []string{"event", "gacha", "music/long", "mysekai", "virtual_live"}
var startappPrefixes = []string{
	"bonds_honor", "honor", "thumbnail", "character", "music", "rank_live",
	"stamp", "home/banner", "player_frame", "areaitem",
}

// Downloader 并发下载游戏资源。
type Downloader struct {
	cfg         masterdata.Config
	outDir      string
	client      *http.Client
	concurrency int

	mu       sync.Mutex
	inflight map[string]chan struct{} // 同一文件去重
}

func NewDownloader(cfg masterdata.Config, outDir string) *Downloader {
	return &Downloader{
		cfg:         cfg,
		outDir:      outDir,
		client:      &http.Client{Timeout: 60 * time.Second},
		concurrency: 16,
		inflight:    map[string]chan struct{}{},
	}
}

func hasPrefix(relPath string, prefixes []string) bool {
	for _, p := range prefixes {
		if strings.HasPrefix(relPath, p) {
			return true
		}
	}
	return false
}

// candidateURLs 复刻 Python 端 _iter_rip_asset_urls。
func candidateURLs(src masterdata.RipSource, path, raw string) []string {
	baseURL := strings.TrimRight(src.BaseURL, "/") + "/"
	relPath := strings.ReplaceAll(strings.Trim(path, "/")+"/"+strings.TrimLeft(raw, "/"), "_rip", "")
	var urls []string
	// 谱面难度文件已改为 {难度}.txt（无后缀路径返回 404）。
	// 对 music/music_score 下不带扩展名的难度，把 .txt 候选排在前面优先下载，
	// 同时保留无后缀兜底（落盘仍以无后缀 raw 命名），与 Python 端 _iter_rip_asset_urls 保持一致。
	if strings.Contains(strings.Trim(path, "/")+"/", "music/music_score") && !strings.Contains(raw, ".") {
		urls = append(urls, baseURL+strings.Trim(path, "/")+"/"+strings.TrimLeft(raw, "/")+".txt")
	}
	switch src.Name {
	case "haruki":
		if hasPrefix(relPath, ondemandPrefixes) {
			urls = append(urls, baseURL+"ondemand/"+relPath)
		} else if hasPrefix(relPath, startappPrefixes) {
			urls = append(urls, baseURL+"startapp/"+relPath)
		}
	case "sekai.best":
		urls = append(urls, baseURL+relPath)
	}
	urls = append(urls, baseURL+strings.Trim(path, "/")+"/"+strings.TrimLeft(raw, "/"))
	// 去重保持顺序
	seen := map[string]bool{}
	out := urls[:0]
	for _, u := range urls {
		if !seen[u] {
			seen[u] = true
			out = append(out, u)
		}
	}
	return out
}

func safeRel(s string) bool {
	return s != "" && !strings.Contains(s, "..") && !strings.HasPrefix(s, "/")
}

// Fetch 确保单个资源存在于磁盘，返回是否新下载。
func (d *Downloader) Fetch(ctx context.Context, region, path, raw string) (bool, error) {
	path = strings.ReplaceAll(path, "\\", "/")
	raw = strings.ReplaceAll(raw, "\\", "/")
	if !safeRel(path) || !safeRel(raw) {
		return false, fmt.Errorf("unsafe path")
	}
	target := filepath.Join(d.outDir, region, filepath.FromSlash(path), filepath.FromSlash(raw))

	// 同一目标文件的并发请求合并等待
	d.mu.Lock()
	if ch, ok := d.inflight[target]; ok {
		d.mu.Unlock()
		select {
		case <-ch:
		case <-ctx.Done():
			return false, ctx.Err()
		}
		_, err := os.Stat(target)
		return false, err
	}
	ch := make(chan struct{})
	d.inflight[target] = ch
	d.mu.Unlock()
	defer func() {
		d.mu.Lock()
		delete(d.inflight, target)
		d.mu.Unlock()
		close(ch)
	}()

	if _, err := os.Stat(target); err == nil {
		return false, nil
	}

	rc, ok := d.cfg[region]
	if !ok {
		return false, fmt.Errorf("unknown region %q", region)
	}
	var lastErr error
	for _, src := range rc.Rip.Sources {
		// 与 Python 端一致：源配置了 prefixes 时只处理匹配的路径
		if len(src.Prefixes) > 0 && !hasPrefix(strings.Trim(path, "/"), src.Prefixes) {
			continue
		}
		for _, u := range candidateURLs(src, path, raw) {
			data, err := d.download(ctx, u)
			if err != nil {
				lastErr = err
				continue
			}
			if err := atomicWrite(target, data); err != nil {
				return false, err
			}
			log.Printf("[assets] downloaded %s/%s/%s (%d bytes, from %s)", region, path, raw, len(data), src.Name)
			return true, nil
		}
	}
	if lastErr == nil {
		lastErr = fmt.Errorf("no rip sources configured for %s", region)
	}
	return false, lastErr
}

func (d *Downloader) download(ctx context.Context, url string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) go-pjsk-helper")
	resp, err := d.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d for %s", resp.StatusCode, url)
	}
	return io.ReadAll(io.LimitReader(resp.Body, 128<<20))
}

func atomicWrite(target string, data []byte) error {
	dir := filepath.Dir(target)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, "."+filepath.Base(target)+".tmp*")
	if err != nil {
		return err
	}
	name := tmp.Name()
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		os.Remove(name)
		return err
	}
	if err := tmp.Close(); err != nil {
		os.Remove(name)
		return err
	}
	return os.Rename(name, target)
}

// prefetchRequest 是批量预取请求体。
type prefetchRequest struct {
	Region string `json:"region"`
	Items  []struct {
		Path string `json:"path"`
		Raw  string `json:"raw"`
	} `json:"items"`
}

// HandleFetch 处理 POST /assets/fetch?region=jp&path=...&raw=...（单个，阻塞等待完成）。
func (d *Downloader) HandleFetch(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	region, path, raw := q.Get("region"), q.Get("path"), q.Get("raw")
	if region == "" || path == "" || raw == "" {
		http.Error(w, "region/path/raw required", http.StatusBadRequest)
		return
	}
	downloaded, err := d.Fetch(r.Context(), region, path, raw)
	w.Header().Set("Content-Type", "application/json")
	if err != nil {
		w.WriteHeader(http.StatusBadGateway)
		_ = json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": err.Error()})
		return
	}
	_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "downloaded": downloaded})
}

// HandlePrefetch 处理 POST /assets/prefetch（批量并发，阻塞到全部完成）。
func (d *Downloader) HandlePrefetch(w http.ResponseWriter, r *http.Request) {
	var req prefetchRequest
	if err := json.NewDecoder(io.LimitReader(r.Body, 4<<20)).Decode(&req); err != nil {
		http.Error(w, "bad json", http.StatusBadRequest)
		return
	}
	if req.Region == "" || len(req.Items) == 0 {
		http.Error(w, "region/items required", http.StatusBadRequest)
		return
	}
	if len(req.Items) > 500 {
		http.Error(w, "too many items", http.StatusBadRequest)
		return
	}

	sem := make(chan struct{}, d.concurrency)
	var wg sync.WaitGroup
	var mu sync.Mutex
	okCount, dlCount := 0, 0
	var errs []string

	for _, item := range req.Items {
		wg.Add(1)
		go func(path, raw string) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			downloaded, err := d.Fetch(r.Context(), req.Region, path, raw)
			mu.Lock()
			defer mu.Unlock()
			if err != nil {
				errs = append(errs, fmt.Sprintf("%s/%s: %v", path, raw, err))
				return
			}
			okCount++
			if downloaded {
				dlCount++
			}
		}(item.Path, item.Raw)
	}
	wg.Wait()

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"ok":         len(errs) == 0,
		"total":      len(req.Items),
		"succeeded":  okCount,
		"downloaded": dlCount,
		"errors":     errs,
	})
}
