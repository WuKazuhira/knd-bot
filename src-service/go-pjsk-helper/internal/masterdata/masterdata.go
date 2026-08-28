// Package masterdata：定时差分下载各服主数据。
package masterdata

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

// Source 是一个 masterdata 下载源。
type Source struct {
	Name       string `yaml:"name"`
	BaseURL    string `yaml:"base_url"`
	VersionURL string `yaml:"version_url"`
}

// RipSource 是游戏资源（rip assets）的下载源。
type RipSource struct {
	Name     string   `yaml:"name"`
	BaseURL  string   `yaml:"base_url"`
	Prefixes []string `yaml:"prefixes"`
}

// RegionConfig 是单个服务器的配置。
type RegionConfig struct {
	API struct {
		ProfileAPIURL           string `yaml:"profile_api_url"`
		SuiteAPIURL             string `yaml:"suite_api_url"`
		RankingTop100NewAPIURL  string `yaml:"ranking_top100_new_api_url"`
		RankingTop100APIURL     string `yaml:"ranking_top100_api_url"`
		RankingBorderAPIURL     string `yaml:"ranking_border_api_url"`
	} `yaml:"api"`
	Masterdata struct {
		Sources []Source `yaml:"sources"`
	} `yaml:"masterdata"`
	Rip struct {
		Sources []RipSource `yaml:"sources"`
	} `yaml:"rip"`
}

// Config 是 servers.yaml 的全量映射。
type Config map[string]RegionConfig

// LoadConfig 读取 servers.yaml。
func LoadConfig(path string) (Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var cfg Config
	if err := yaml.Unmarshal(raw, &cfg); err != nil {
		return nil, err
	}
	return cfg, nil
}

// 与 Python 端定时任务保持一致的常用文件清单。
var defaultFiles = []string{
	"musics.json", "musicDifficulties.json", "musicVocals.json", "musicTags.json",
	"events.json", "eventCards.json", "eventDeckBonuses.json", "eventMusics.json",
	"cards.json", "cardCostume3ds.json", "costume3ds.json", "cardSupplies.json",
	"skills.json", "gameCharacters.json", "gameCharacterUnits.json", "outsideCharacters.json",
	"honors.json", "honorGroups.json", "bondsHonors.json",
	"worldBlooms.json", "rankMatchSeasons.json", "cheerfulCarnivalTeams.json",
	"gachas.json", "characterProfiles.json", "virtualLives.json",
	"mysekaiGateCharacterLotteries.json", "mysekaiSites.json",
}

// Syncer 负责下载与差分写入。
type Syncer struct {
	cfg         Config
	outDir      string
	callbackURL string
	client      *http.Client
}

func NewSyncer(cfg Config, outDir, callbackURL string) *Syncer {
	return &Syncer{
		cfg:         cfg,
		outDir:      outDir,
		callbackURL: callbackURL,
		client:      &http.Client{Timeout: 120 * time.Second},
	}
}

// Run 周期执行全量同步。
func (s *Syncer) Run(ctx context.Context, interval time.Duration) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	s.SyncAll(ctx)
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			s.SyncAll(ctx)
		}
	}
}

// SyncAll 同步所有 region 的所有文件。
func (s *Syncer) SyncAll(ctx context.Context) {
	for region := range s.cfg {
		for _, file := range defaultFiles {
			if ctx.Err() != nil {
				return
			}
			changed, err := s.SyncFile(ctx, region, file)
			if err != nil {
				log.Printf("[masterdata] %s/%s: %v", region, file, err)
				continue
			}
			if changed {
				s.notifyKndbot(ctx, region, file)
			}
			// 温和限速，避免对上游造成压力
			select {
			case <-time.After(500 * time.Millisecond):
			case <-ctx.Done():
				return
			}
		}
	}
}

// SyncFile 下载单个文件，内容变化时原子写入并返回 true。
func (s *Syncer) SyncFile(ctx context.Context, region, file string) (bool, error) {
	rc, ok := s.cfg[region]
	if !ok {
		return false, fmt.Errorf("unknown region %q", region)
	}
	var lastErr error
	for _, src := range rc.Masterdata.Sources {
		for _, u := range candidateURLs(strings.TrimRight(src.BaseURL, "/") + "/" + file) {
			data, err := s.download(ctx, u)
			if err != nil {
				lastErr = err
				continue
			}
			if !json.Valid(data) {
				lastErr = fmt.Errorf("source %s returned invalid JSON", src.Name)
				continue
			}
			return s.writeIfChanged(region, file, data)
		}
	}
	if lastErr == nil {
		lastErr = fmt.Errorf("no sources configured")
	}
	return false, lastErr
}

// candidateURLs 与 Python 端 _iter_masterdata_urls 保持一致：
// GitHub raw 地址优先尝试 ghfast 镜像，失败再直连。
func candidateURLs(rawURL string) []string {
	mirror := os.Getenv("GH_MIRROR_PREFIX")
	if mirror == "" {
		mirror = "https://ghfast.top/"
	}
	if strings.HasPrefix(rawURL, "https://raw.githubusercontent.com/") && mirror != "off" {
		return []string{mirror + rawURL, rawURL}
	}
	return []string{rawURL}
}

func (s *Syncer) download(ctx context.Context, rawURL string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return nil, err
	}
	resp, err := s.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d for %s", resp.StatusCode, rawURL)
	}
	return io.ReadAll(io.LimitReader(resp.Body, 256<<20))
}

func (s *Syncer) writeIfChanged(region, file string, data []byte) (bool, error) {
	dir := filepath.Join(s.outDir, region)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return false, err
	}
	target := filepath.Join(dir, file)
	if old, err := os.ReadFile(target); err == nil {
		if sha256.Sum256(old) == sha256.Sum256(data) {
			return false, nil
		}
	}
	tmp, err := os.CreateTemp(dir, "."+file+".tmp*")
	if err != nil {
		return false, err
	}
	tmpName := tmp.Name()
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		os.Remove(tmpName)
		return false, err
	}
	if err := tmp.Close(); err != nil {
		os.Remove(tmpName)
		return false, err
	}
	if err := os.Rename(tmpName, target); err != nil {
		os.Remove(tmpName)
		return false, err
	}
	log.Printf("[masterdata] updated %s/%s (%d bytes)", region, file, len(data))
	return true, nil
}

// notifyKndbot 通知 kndbot 预热缓存（可选）。
func (s *Syncer) notifyKndbot(ctx context.Context, region, file string) {
	if s.callbackURL == "" {
		return
	}
	body, _ := json.Marshal(map[string]string{"region": region, "file": file})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, s.callbackURL, bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := s.client.Do(req)
	if err != nil {
		log.Printf("[masterdata] callback failed: %v", err)
		return
	}
	resp.Body.Close()
}

// HandleRefresh 处理 POST /masterdata/refresh?region=jp&file=musics.json
func (s *Syncer) HandleRefresh(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	region := q.Get("region")
	file := q.Get("file")
	if region == "" {
		http.Error(w, "region required", http.StatusBadRequest)
		return
	}
	if file == "" {
		go s.SyncAll(context.Background())
		w.WriteHeader(http.StatusAccepted)
		fmt.Fprintln(w, `{"status":"sync_all_started"}`)
		return
	}
	if _, err := url.PathUnescape(file); err != nil || strings.Contains(file, "/") || strings.Contains(file, "..") {
		http.Error(w, "bad file name", http.StatusBadRequest)
		return
	}
	changed, err := s.SyncFile(r.Context(), region, file)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, `{"region":%q,"file":%q,"changed":%v}`+"\n", region, file, changed)
}
