// Package suite：Haruki suite/profile 缓存代理与 b30 预计算。
package suite

import (
	"container/list"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/kazuhira/go-pjsk-helper/internal/masterdata"
)

const memCacheLimit = 256

type cacheEntry struct {
	key  string
	data []byte
	at   time.Time
}

// Proxy 提供 suite 数据的多级缓存（内存 LRU → 磁盘 → 上游 API）。
type Proxy struct {
	cfg      masterdata.Config
	suiteDir string // data/pjsk/profile
	mdDir    string // data/pjsk/masterdata
	client   *http.Client

	mu    sync.Mutex
	cache map[string]*list.Element
	lru   *list.List
}

func NewProxy(cfg masterdata.Config, suiteDir, mdDir string) *Proxy {
	return &Proxy{
		cfg:      cfg,
		suiteDir: suiteDir,
		mdDir:    mdDir,
		client:   &http.Client{Timeout: 30 * time.Second},
		cache:    map[string]*list.Element{},
		lru:      list.New(),
	}
}

func (p *Proxy) cacheGet(key string, maxAge time.Duration) []byte {
	p.mu.Lock()
	defer p.mu.Unlock()
	el, ok := p.cache[key]
	if !ok {
		return nil
	}
	ent := el.Value.(*cacheEntry)
	if time.Since(ent.at) > maxAge {
		p.lru.Remove(el)
		delete(p.cache, key)
		return nil
	}
	p.lru.MoveToFront(el)
	return ent.data
}

func (p *Proxy) cachePut(key string, data []byte) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if el, ok := p.cache[key]; ok {
		el.Value.(*cacheEntry).data = data
		el.Value.(*cacheEntry).at = time.Now()
		p.lru.MoveToFront(el)
		return
	}
	el := p.lru.PushFront(&cacheEntry{key: key, data: data, at: time.Now()})
	p.cache[key] = el
	for p.lru.Len() > memCacheLimit {
		last := p.lru.Back()
		p.lru.Remove(last)
		delete(p.cache, last.Value.(*cacheEntry).key)
	}
}

func sanitize(s string) bool {
	return s != "" && !strings.ContainsAny(s, "/\\.")
}

// getSuite 取 suite 数据：内存 → 磁盘 → 上游。
func (p *Proxy) getSuite(region, uid string) ([]byte, error) {
	key := region + "/" + uid
	if data := p.cacheGet(key, 5*time.Minute); data != nil {
		return data, nil
	}
	// 磁盘缓存（与 Python 端共享的 {uid}.json）
	diskPath := filepath.Join(p.suiteDir, region, uid+".json")
	if st, err := os.Stat(diskPath); err == nil && time.Since(st.ModTime()) < 30*time.Minute {
		if data, err := os.ReadFile(diskPath); err == nil && json.Valid(data) {
			p.cachePut(key, data)
			return data, nil
		}
	}
	// 上游
	rc, ok := p.cfg[region]
	if !ok || rc.API.ProfileAPIURL == "" {
		return nil, fmt.Errorf("region %q has no profile api", region)
	}
	url := strings.ReplaceAll(rc.API.ProfileAPIURL, "{uid}", uid)
	resp, err := p.client.Get(url)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("upstream HTTP %d", resp.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, 64<<20))
	if err != nil {
		return nil, err
	}
	if !json.Valid(data) {
		return nil, fmt.Errorf("upstream returned invalid JSON")
	}
	p.cachePut(key, data)
	return data, nil
}

// HandleSuite 处理 GET /suite/{region}/{uid}
func (p *Proxy) HandleSuite(w http.ResponseWriter, r *http.Request) {
	region, uid := r.PathValue("region"), r.PathValue("uid")
	if !sanitize(region) || uid == "" || strings.ContainsAny(uid, "/\\") {
		http.Error(w, "bad path", http.StatusBadRequest)
		return
	}
	data, err := p.getSuite(region, uid)
	if err != nil {
		log.Printf("[suite] %s/%s: %v", region, uid, err)
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write(data)
}

// ---------- b30 ----------

type diffEntry struct {
	MusicID    int     `json:"musicId"`
	Difficulty string  `json:"musicDifficulty"`
	PlayLevel  float64 `json:"playLevel"`
	Result     int     `json:"result"` // 0 无成绩 1 FC 2 AP
	Rank       float64 `json:"rank"`
	APLevel    float64 `json:"aplevel"`
	FCLevel    float64 `json:"fclevel"`
}

// fcRank 对应 Python 端 fcrank：level>=33 减 1，否则减 1.5。
func fcRank(playLevel, apLevel float64) float64 {
	if playLevel >= 33 {
		return apLevel - 1
	}
	return apLevel - 1.5
}

// HandleB30 处理 GET /b30/{region}/{uid}：返回排序后的 b30 结构。
func (p *Proxy) HandleB30(w http.ResponseWriter, r *http.Request) {
	region, uid := r.PathValue("region"), r.PathValue("uid")
	if !sanitize(region) || uid == "" || strings.ContainsAny(uid, "/\\") {
		http.Error(w, "bad path", http.StatusBadRequest)
		return
	}
	suiteData, err := p.getSuite(region, uid)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	diffsRaw, err := os.ReadFile(filepath.Join(p.mdDir, region, "musicDifficulties.json"))
	if err != nil {
		http.Error(w, "musicDifficulties.json unavailable", http.StatusServiceUnavailable)
		return
	}

	var diffs []struct {
		MusicID         int     `json:"musicId"`
		MusicDifficulty string  `json:"musicDifficulty"`
		PlayLevel       float64 `json:"playLevel"`
	}
	if err := json.Unmarshal(diffsRaw, &diffs); err != nil {
		http.Error(w, "bad musicDifficulties.json", http.StatusInternalServerError)
		return
	}

	var suite struct {
		UserMusicResults []struct {
			MusicID             int    `json:"musicId"`
			MusicDifficultyType string `json:"musicDifficultyType"`
			MusicDifficulty     string `json:"musicDifficulty"`
			PlayResult          string `json:"playResult"`
		} `json:"userMusicResults"`
	}
	if err := json.Unmarshal(suiteData, &suite); err != nil || len(suite.UserMusicResults) == 0 {
		http.Error(w, "suite has no userMusicResults", http.StatusNotFound)
		return
	}

	index := map[string]*diffEntry{}
	entries := make([]*diffEntry, 0, len(diffs))
	for _, d := range diffs {
		e := &diffEntry{
			MusicID:    d.MusicID,
			Difficulty: d.MusicDifficulty,
			PlayLevel:  d.PlayLevel,
			APLevel:    d.PlayLevel,
			FCLevel:    fcRank(d.PlayLevel, d.PlayLevel),
		}
		index[fmt.Sprintf("%d:%s", d.MusicID, d.MusicDifficulty)] = e
		entries = append(entries, e)
	}
	for _, mr := range suite.UserMusicResults {
		diff := mr.MusicDifficultyType
		if diff == "" {
			diff = mr.MusicDifficulty
		}
		e, ok := index[fmt.Sprintf("%d:%s", mr.MusicID, diff)]
		if !ok {
			continue
		}
		switch mr.PlayResult {
		case "full_perfect":
			e.Result = 2
			e.Rank = e.APLevel
		case "full_combo":
			if e.Result < 1 {
				e.Result = 1
				e.Rank = e.FCLevel
			}
		}
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].Rank > entries[j].Rank })
	top := entries
	if len(top) > 30 {
		top = top[:30]
	}
	var sum float64
	n := 0
	for _, e := range top {
		if e.Rank > 0 {
			sum += e.Rank
			n++
		}
	}
	avg := 0.0
	if n > 0 {
		avg = sum / float64(n)
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"region": region,
		"uid":    uid,
		"b30":    top,
		"rating": avg,
	})
}
