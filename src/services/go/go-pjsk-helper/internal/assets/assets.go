// Package assets：游戏资源（缩略图/卡面/头像等）批量并发下载。
//
// 与 Python 端 _autoask.update_server_assets 的 URL 映射规则保持一致，
// 下载落盘到共享 volume 的 data/pjsk/ondemand/{region}/{path}/{file}。
package assets

import (
	"context"
	"crypto/sha256"
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

var dedupPrefixes = []string{
	"ondemand/music/long/",
	"music/long/",
	"startapp/music/music_score/",
	"startapp/music/jacket/",
	"startapp/character/member/",
	"startapp/thumbnail/chara/",
	"charts/",
}

func isDedupRegion(region string) bool {
	return region == "cn" || region == "tw"
}

func isDedupCandidate(relative string) bool {
	relative = strings.TrimLeft(filepath.ToSlash(relative), "/")
	for _, prefix := range dedupPrefixes {
		if strings.HasPrefix(relative, prefix) {
			return true
		}
	}
	return false
}

func sameBytesContent(path string, data []byte) bool {
	info, err := os.Stat(path)
	if err != nil || info.Size() != int64(len(data)) {
		return false
	}
	file, err := os.Open(path)
	if err != nil {
		return false
	}
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return false
	}
	var actual [sha256.Size]byte
	copy(actual[:], hash.Sum(nil))
	return actual == sha256.Sum256(data)
}

func replaceWithLink(source, target string) (string, error) {
	sourceInfo, err := os.Stat(source)
	if err != nil || !sourceInfo.Mode().IsRegular() {
		if err == nil {
			err = fmt.Errorf("source is not a regular file: %s", source)
		}
		return "", err
	}
	if targetInfo, statErr := os.Stat(target); statErr == nil && os.SameFile(sourceInfo, targetInfo) {
		return "existing", nil
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return "", err
	}
	tmp, err := os.CreateTemp(filepath.Dir(target), "."+filepath.Base(target)+".dedup-*")
	if err != nil {
		return "", err
	}
	tmpName := tmp.Name()
	if err := tmp.Close(); err != nil {
		_ = os.Remove(tmpName)
		return "", err
	}
	_ = os.Remove(tmpName)
	method := "hardlink"
	if err := os.Link(source, tmpName); err != nil {
		method = "symlink"
		rel, relErr := filepath.Rel(filepath.Dir(target), source)
		if relErr != nil {
			return "", relErr
		}
		if err := os.Symlink(rel, tmpName); err != nil {
			return "", err
		}
	}
	if err := os.Rename(tmpName, target); err != nil {
		_ = os.Remove(tmpName)
		return "", err
	}
	return method, nil
}

func (d *Downloader) storeDownloaded(region, path, raw, target string, data []byte) error {
	relative := filepath.ToSlash(filepath.Join(path, raw))
	if isDedupRegion(region) && isDedupCandidate(relative) {
		jpPath := filepath.Join(d.outDir, "jp", filepath.FromSlash(relative))
		if sameBytesContent(jpPath, data) {
			if method, err := replaceWithLink(jpPath, target); err == nil {
				log.Printf("[assets] reused JP %s/%s/%s via %s", region, path, raw, method)
				return nil
			} else {
				log.Printf("[assets] JP reuse failed for %s/%s/%s, fallback to file: %v", region, path, raw, err)
			}
		}
	}
	return atomicWrite(target, data)
}

func parseBool(s string) bool {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

// Fetch 确保单个资源存在于磁盘，返回是否新下载。
func (d *Downloader) Fetch(ctx context.Context, region, path, raw string) (bool, error) {
	return d.fetch(ctx, region, path, raw, false)
}

// FetchFresh 忽略已有文件，重新下载并原子替换资源。
func (d *Downloader) FetchFresh(ctx context.Context, region, path, raw string) (bool, error) {
	return d.fetch(ctx, region, path, raw, true)
}

func (d *Downloader) fetch(ctx context.Context, region, path, raw string, force bool) (bool, error) {
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

	if !force {
		if _, err := os.Stat(target); err == nil {
			return false, nil
		}
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
			data, err := d.downloadRetry(ctx, u)
			if err != nil {
				lastErr = err
				continue
			}
			if err := d.storeDownloaded(region, path, raw, target, data); err != nil {
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

func (d *Downloader) downloadRetry(ctx context.Context, rawURL string) ([]byte, error) {
	var lastErr error
	for attempt := 0; attempt < 3; attempt++ {
		data, err := d.download(ctx, rawURL)
		if err == nil {
			return data, nil
		}
		lastErr = err
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
	return nil, lastErr
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
	Force  bool   `json:"force"`
	Items  []struct {
		Path string `json:"path"`
		Raw  string `json:"raw"`
	} `json:"items"`
}

// HandleFetch 处理 POST /assets/fetch?region=jp&path=...&raw=...（单个，阻塞等待完成）。
func (d *Downloader) HandleFetch(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	region, path, raw := q.Get("region"), q.Get("path"), q.Get("raw")
	force := parseBool(q.Get("force"))
	if region == "" || path == "" || raw == "" {
		http.Error(w, "region/path/raw required", http.StatusBadRequest)
		return
	}
	var downloaded bool
	var err error
	if force {
		downloaded, err = d.FetchFresh(r.Context(), region, path, raw)
	} else {
		downloaded, err = d.Fetch(r.Context(), region, path, raw)
	}
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
			var downloaded bool
			var err error
			if req.Force {
				downloaded, err = d.FetchFresh(r.Context(), req.Region, path, raw)
			} else {
				downloaded, err = d.Fetch(r.Context(), req.Region, path, raw)
			}
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
