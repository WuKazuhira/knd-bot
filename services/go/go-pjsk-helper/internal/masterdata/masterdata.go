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
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"github.com/kazuhira/go-pjsk-helper/internal/shared"
	"gopkg.in/yaml.v3"
)

type Source struct {
	Name       string `yaml:"name"`
	BaseURL    string `yaml:"base_url"`
	VersionURL string `yaml:"version_url"`
}

type RipSource struct {
	Name     string   `yaml:"name"`
	BaseURL  string   `yaml:"base_url"`
	Prefixes []string `yaml:"prefixes"`
}

type RegionConfig struct {
	API struct {
		ProfileAPIURL          string `yaml:"profile_api_url"`
		SuiteAPIURL            string `yaml:"suite_api_url"`
		RankingTop100NewAPIURL string `yaml:"ranking_top100_new_api_url"`
		RankingTop100APIURL    string `yaml:"ranking_top100_api_url"`
		RankingBorderAPIURL    string `yaml:"ranking_border_api_url"`
		WorldlinkLatestAPIURL  string `yaml:"worldlink_latest_api_url"`
	} `yaml:"api"`
	Masterdata struct {
		Sources []Source `yaml:"sources"`
	} `yaml:"masterdata"`
	Rip struct {
		Sources []RipSource `yaml:"sources"`
	} `yaml:"rip"`
}

type Config map[string]RegionConfig

func IntField(item map[string]any, key string) (int64, bool) {
	value, ok := item[key]
	if !ok {
		return 0, false
	}
	switch n := value.(type) {
	case int:
		return int64(n), true
	case int64:
		return n, true
	case float64:
		return int64(n), n == float64(int64(n))
	case float32:
		return int64(n), n == float32(int64(n))
	case json.Number:
		parsed, err := n.Int64()
		return parsed, err == nil
	case string:
		parsed, err := strconv.ParseInt(strings.TrimSpace(n), 10, 64)
		return parsed, err == nil
	default:
		return 0, false
	}
}

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

var defaultFiles = []string{
	"musics.json", "musicDifficulties.json", "musicVocals.json", "musicTags.json",
	"events.json", "eventCards.json", "eventDeckBonuses.json", "eventMusics.json",
	"cards.json", "cardCostume3ds.json", "costume3ds.json", "cardSupplies.json",
	"skills.json", "gameCharacters.json", "gameCharacterUnits.json", "outsideCharacters.json",
	"honors.json", "honorGroups.json", "bondsHonors.json",
	"worldBlooms.json", "rankMatchSeasons.json", "cheerfulCarnivalTeams.json",
	"gachas.json", "characterProfiles.json", "virtualLives.json",
	"mysekaiGateCharacterLotteries.json", "mysekaiSites.json",
	"playerFrames.json", "playerFrameGroups.json", "playerFrameParts.json",
}

type Syncer struct {
	cfg                 Config
	outDir              string
	callbackURL         string
	client              *http.Client
	localJP             *LocalJPConfig
	lastLocalJPProbe    string
	afterLocalJPCapture func()
	syncing             atomic.Bool
}

func NewSyncer(cfg Config, outDir, callbackURL string) *Syncer {
	return &Syncer{
		cfg:         cfg,
		outDir:      outDir,
		callbackURL: callbackURL,
		client:      &http.Client{Timeout: 120 * time.Second},
	}
}

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

func (s *Syncer) SyncAll(ctx context.Context) {
	if s.syncing.CompareAndSwap(false, true) {
		s.syncAll(ctx)
	}
}

func (s *Syncer) syncAll(ctx context.Context) {
	defer s.syncing.Store(false)
	for region := range s.cfg {
		if region == localJPRegion && s.LocalJPEnabled() {
			continue
		}
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
			select {
			case <-time.After(500 * time.Millisecond):
			case <-ctx.Done():
				return
			}
		}
	}
}

func (s *Syncer) SyncFile(ctx context.Context, region, file string) (bool, error) {
	if region == localJPRegion && s.LocalJPEnabled() {
		return false, fmt.Errorf("remote JP masterdata sync is disabled while local JP mode is enabled")
	}
	rc, ok := s.cfg[region]
	if !ok {
		return false, fmt.Errorf("unknown region %q", region)
	}
	var lastErr error
	for _, src := range rc.Masterdata.Sources {
		for _, u := range candidateURLs(strings.TrimRight(src.BaseURL, "/") + "/" + file) {
			data, err := s.downloadRetry(ctx, u)
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

func candidateURLs(rawURL string) []string {
	return shared.CandidateURLs(rawURL)
}

func (s *Syncer) downloadRetry(ctx context.Context, rawURL string) ([]byte, error) {
	var lastErr error
	for attempt := 0; attempt < 3; attempt++ {
		data, err := s.download(ctx, rawURL)
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
	if err := tmp.Chmod(0o644); err != nil {
		tmp.Close()
		os.Remove(tmpName)
		return false, err
	}
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

func (s *Syncer) HandleRefresh(ctx context.Context, w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	region := q.Get("region")
	file := q.Get("file")
	if file == "" {
		if ctx.Err() != nil {
			http.Error(w, "service shutting down", http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		if !s.syncing.CompareAndSwap(false, true) {
			fmt.Fprintln(w, `{"status":"sync_all_in_progress"}`)
			return
		}
		go s.syncAll(ctx)
		fmt.Fprintln(w, `{"status":"sync_all_started"}`)
		return
	}
	if region == "" {
		http.Error(w, "region required", http.StatusBadRequest)
		return
	}
	if _, err := url.PathUnescape(file); err != nil || !shared.SafeFileName(file) {
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
