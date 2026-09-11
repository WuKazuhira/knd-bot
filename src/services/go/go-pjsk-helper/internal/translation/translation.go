// Package translation 同步 PJSK 翻译文件到共享 ondemand 目录。
package translation

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/kazuhira/go-pjsk-helper/internal/masterdata"
	"gopkg.in/yaml.v3"
)

var defaultFiles = []string{"music_titles", "event_name", "card_prefix", "cheerful_carnival_teams"}

type Syncer struct {
	cfg     masterdata.Config
	outDir  string
	client  *http.Client
	baseURL string
	mu      sync.Mutex
}

func NewSyncer(cfg masterdata.Config, outDir string) *Syncer {
	base := os.Getenv("PJSK_TRANSLATION_BASE_URL")
	if base == "" {
		base = "https://raw.githubusercontent.com/Sekai-World/sekai-i18n/main/zh-TW/{file}.json"
	}
	return &Syncer{cfg: cfg, outDir: outDir, client: &http.Client{Timeout: 45 * time.Second}, baseURL: base}
}

func (s *Syncer) Run(ctx context.Context, interval time.Duration) {
	if interval <= 0 {
		interval = time.Hour
	}
	s.SyncAll(ctx)
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
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
	for region := range s.cfg {
		for _, file := range defaultFiles {
			if ctx.Err() != nil {
				return
			}
			if _, err := s.SyncFile(ctx, region, file); err != nil {
				fmt.Printf("[translation] %s/%s: %v\n", region, file, err)
			}
		}
	}
}

func (s *Syncer) SyncFile(ctx context.Context, region, file string) (bool, error) {
	if _, ok := s.cfg[region]; !ok {
		return false, fmt.Errorf("unknown region %q", region)
	}
	if !safeName(file) {
		return false, fmt.Errorf("bad translation file")
	}
	var lastErr error
	for attempt := 0; attempt < 3; attempt++ {
		data, err := s.fetch(ctx, file)
		if err == nil {
			return s.merge(region, file, data)
		}
		lastErr = err
		if !sleep(ctx, time.Duration(1<<attempt)*time.Second) {
			return false, ctx.Err()
		}
	}
	return false, lastErr
}

func (s *Syncer) fetch(ctx context.Context, file string) ([]byte, error) {
	raw := strings.ReplaceAll(s.baseURL, "{file}", url.PathEscape(file))
	candidates := []string{raw}
	if strings.HasPrefix(raw, "https://raw.githubusercontent.com/") {
		mirror := os.Getenv("GH_MIRROR_PREFIX")
		if mirror == "" {
			mirror = "https://ghfast.top/"
		}
		if mirror != "off" {
			candidates = []string{strings.TrimRight(mirror, "/") + "/" + raw, raw}
		}
	}
	var lastErr error
	for _, candidate := range candidates {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, candidate, nil)
		if err != nil {
			lastErr = err
			continue
		}
		resp, err := s.client.Do(req)
		if err != nil {
			lastErr = err
			continue
		}
		body, readErr := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
		resp.Body.Close()
		if readErr == nil && resp.StatusCode == http.StatusOK {
			return body, nil
		}
		if readErr != nil {
			lastErr = readErr
		} else {
			lastErr = fmt.Errorf("HTTP %d", resp.StatusCode)
		}
	}
	return nil, lastErr
}

func (s *Syncer) merge(region, file string, raw []byte) (bool, error) {
	var incoming map[string]string
	if err := json.Unmarshal(raw, &incoming); err != nil {
		return false, fmt.Errorf("parse %s: %w", file, err)
	}
	path := filepath.Join(s.outDir, region, "translate.yaml")
	s.mu.Lock()
	defer s.mu.Unlock()
	translations := map[string]map[int]string{}
	if old, err := os.ReadFile(path); err == nil && len(old) > 0 {
		if err := yaml.Unmarshal(old, &translations); err != nil {
			return false, fmt.Errorf("parse %s: %w", path, err)
		}
	}
	if translations[file] == nil {
		translations[file] = map[int]string{}
	}
	changed := false
	for key, value := range incoming {
		id, err := strconv.Atoi(key)
		if err != nil || value == "" {
			continue
		}
		if _, exists := translations[file][id]; !exists {
			translations[file][id] = toSimplified(value)
			changed = true
		}
	}
	if !changed && pathExists(path) {
		return false, nil
	}
	data, err := yaml.Marshal(translations)
	if err != nil {
		return false, err
	}
	if err := atomicWrite(path, data); err != nil {
		return false, err
	}
	return true, nil
}

func (s *Syncer) HandleRefresh(w http.ResponseWriter, r *http.Request) {
	region, file := r.URL.Query().Get("region"), r.URL.Query().Get("file")
	if region == "" {
		http.Error(w, "region required", http.StatusBadRequest)
		return
	}
	if file == "" {
		go s.SyncAll(context.Background())
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write([]byte(`{"status":"sync_all_started"}`))
		return
	}
	changed, err := s.SyncFile(r.Context(), region, file)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_, _ = fmt.Fprintf(w, `{"region":%q,"file":%q,"changed":%v}\n`, region, file, changed)
}

func safeName(name string) bool {
	return name != "" && filepath.Base(name) == name && !strings.Contains(name, "..")
}
func pathExists(path string) bool { _, err := os.Stat(path); return err == nil }
func sleep(ctx context.Context, d time.Duration) bool {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-timer.C:
		return true
	case <-ctx.Done():
		return false
	}
}

func atomicWrite(path string, data []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".tmp*")
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

var traditionalToSimplified = strings.NewReplacer(
	"臺", "台", "灣", "湾", "與", "与", "學", "学", "樂", "乐", "書", "书", "車", "车", "國", "国", "會", "会", "時", "时", "間", "间", "發", "发", "現", "现", "進", "进", "場", "场", "開", "开", "關", "关", "閉", "闭", "號", "号", "稱", "称", "個", "个", "隊", "队", "對", "对", "從", "从", "這", "这", "裡", "里", "後", "后", "為", "为", "無", "无", "點", "点", "線", "线", "級", "级", "專", "专", "業", "业", "廣", "广", "東", "东", "龍", "龙", "夢", "梦", "異", "异", "體", "体", "聲", "声", "優", "优", "獎", "奖", "選", "选", "變", "变", "動", "动", "擊", "击", "劍", "剑", "實", "实", "驗", "验", "難", "难", "簡", "简", "單", "单", "風", "风", "雲", "云", "頭", "头", "標", "标", "題", "题", "畫", "画", "節", "节", "號", "号", "貢", "贡", "獻", "献", "滿", "满", "結", "结", "束", "束", "勝", "胜", "敗", "败", "廣", "广", "網", "网", "頁", "页", "檔", "档", "庫", "库", "聽", "听", "讀", "读", "認", "认", "識", "识", "讓", "让", "將", "将", "應", "应", "擇", "择", "機", "机", "處", "处", "類", "类", "傳", "传", "換", "换", "輸", "输", "狀", "状", "態", "态", "獨", "独", "組", "组", "織", "织", "華", "华", "貝", "贝", "漢", "汉", "語", "语", "運", "运", "庫", "库", "線", "线", "標", "标", "準", "准", "擁", "拥", "護", "护", "氣", "气", "鬥", "斗", "總", "总", "彈", "弹", "頁", "页", "顯", "显", "示", "示", "藥", "药", "術", "术", "階", "阶", "級", "级", "經", "经", "濟", "济", "種", "种", "類", "类", "廳", "厅", "館", "馆", "廣", "广", "廢", "废", "復", "复", "蘇", "苏", "劃", "划", "麗", "丽", "萬", "万", "億", "亿", "優", "优", "勝", "胜", "團", "团", "圓", "圆", "臺", "台", "還", "还", "邊", "边", "遠", "远", "連", "连", "選", "选", "過", "过", "並", "并", "較", "较", "於", "于", "內", "内", "兩", "两", "專", "专", "門", "门", "問", "问", "間", "间", "頁", "页", "項", "项", "預", "预", "測", "测", "數", "数", "據", "据", "線", "线", "資", "资", "源", "源", "錯", "错", "誤", "误", "請", "请", "輸", "输", "入", "入", "刪", "删", "除", "除", "增", "增", "加", "加", "記", "记", "錄", "录", "時", "时", "間", "间", "日", "日", "月", "月", "年", "年", "週", "周", "期", "期",
)

func toSimplified(s string) string { return traditionalToSimplified.Replace(s) }
