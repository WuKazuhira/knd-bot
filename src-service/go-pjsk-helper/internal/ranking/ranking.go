// Package ranking：定时抓取排行 API 并写入与 Python 端一致的 sqlite 历史库。
//
// 写入协议与 src/plugins/pjsk/_sk_sql.py 保持一致：
//   - 库文件: {data}/database/sk_{region}/{event_id}_ranking.db（WAL）
//   - 表:     ranking(id, uid, name, score, rank, ts)
//   - WL 分榜使用 encoded_event_id = chapterNo*1000 + baseEventID
//
// 同时维护 Python callapi 读取的 sktop100.json 快照。
package ranking

import (
	"context"
	"database/sql"
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

	_ "modernc.org/sqlite"

	"github.com/kazuhira/go-pjsk-helper/internal/masterdata"
)

const wlEventIDFactor = 1000

// Ranking 是一条榜线记录。
type Ranking struct {
	UID   string
	Name  string
	Score int64
	Rank  int64
}

// Collector 定时抓取各服排行并写入 sqlite。
type Collector struct {
	cfg    masterdata.Config
	mdDir  string // data/pjsk/masterdata
	dbDir  string // data/pjsk/database
	token  string
	client *http.Client

	mu     sync.RWMutex
	latest map[string]json.RawMessage
	dbs    map[string]*sql.DB

	harukiLast time.Time
}

func NewCollector(cfg masterdata.Config, mdDir, dbDir, token string) *Collector {
	return &Collector{
		cfg:    cfg,
		mdDir:  mdDir,
		dbDir:  dbDir,
		token:  token,
		client: &http.Client{Timeout: 20 * time.Second},
		latest: map[string]json.RawMessage{},
		dbs:    map[string]*sql.DB{},
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
			c.closeAll()
			return
		case <-ticker.C:
			c.collectAll(ctx)
		}
	}
}

func (c *Collector) closeAll() {
	c.mu.Lock()
	defer c.mu.Unlock()
	for _, db := range c.dbs {
		_ = db.Close()
	}
	c.dbs = map[string]*sql.DB{}
}

// ---------- masterdata 辅助 ----------

type eventInfo struct {
	ID          int64 `json:"id"`
	StartAt     int64 `json:"startAt"`
	AggregateAt int64 `json:"aggregateAt"`
}

// currentEventID 复刻 Python currentevent 的 going/counting 判定。
func (c *Collector) currentEventID(region string) (int64, bool) {
	raw, err := os.ReadFile(filepath.Join(c.mdDir, region, "events.json"))
	if err != nil {
		return 0, false
	}
	var events []eventInfo
	if err := json.Unmarshal(raw, &events); err != nil {
		return 0, false
	}
	now := time.Now().UnixMilli()
	for _, e := range events {
		if e.StartAt < now && now < e.AggregateAt {
			return e.ID, true // going
		}
	}
	return 0, false
}

type wlChapter struct {
	EventID         int64 `json:"eventId"`
	GameCharacterID int64 `json:"gameCharacterId"`
	ChapterNo       int64 `json:"chapterNo"`
}

func (c *Collector) wlChapters(region string, eventID int64) map[int64]int64 {
	// gameCharacterId -> chapterNo
	raw, err := os.ReadFile(filepath.Join(c.mdDir, region, "worldBlooms.json"))
	if err != nil {
		return nil
	}
	var chapters []wlChapter
	if err := json.Unmarshal(raw, &chapters); err != nil {
		return nil
	}
	out := map[int64]int64{}
	for _, ch := range chapters {
		if ch.EventID == eventID && ch.GameCharacterID != 0 {
			out[ch.GameCharacterID] = ch.ChapterNo
		}
	}
	return out
}

// ---------- sqlite ----------

func (c *Collector) db(region string, eventID int64) (*sql.DB, error) {
	key := fmt.Sprintf("%s/%d", region, eventID)
	c.mu.Lock()
	defer c.mu.Unlock()
	if db, ok := c.dbs[key]; ok {
		return db, nil
	}
	dir := filepath.Join(c.dbDir, "sk_"+region)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	path := filepath.Join(dir, fmt.Sprintf("%d_ranking.db", eventID))
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	stmts := []string{
		"PRAGMA journal_mode=WAL;",
		`CREATE TABLE IF NOT EXISTS ranking (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			uid TEXT, name TEXT, score INTEGER, rank INTEGER, ts INTEGER
		)`,
		"CREATE INDEX IF NOT EXISTS idx_ranking_rank_ts ON ranking (rank, ts)",
		"CREATE INDEX IF NOT EXISTS idx_ranking_uid ON ranking (uid)",
		"CREATE INDEX IF NOT EXISTS idx_ranking_uid_ts ON ranking (uid, ts)",
	}
	for _, s := range stmts {
		if _, err := db.Exec(s); err != nil {
			db.Close()
			return nil, err
		}
	}
	c.dbs[key] = db
	return db, nil
}

func (c *Collector) record(region string, eventID int64, rankings []Ranking) error {
	if len(rankings) == 0 {
		return nil
	}
	db, err := c.db(region, eventID)
	if err != nil {
		return err
	}
	tx, err := db.Begin()
	if err != nil {
		return err
	}
	stmt, err := tx.Prepare("INSERT INTO ranking (uid, name, score, rank, ts) VALUES (?, ?, ?, ?, ?)")
	if err != nil {
		tx.Rollback()
		return err
	}
	defer stmt.Close()
	ts := time.Now().Unix()
	for _, r := range rankings {
		if _, err := stmt.Exec(r.UID, r.Name, r.Score, r.Rank, ts); err != nil {
			tx.Rollback()
			return err
		}
	}
	return tx.Commit()
}

// ---------- API 抓取与解析 ----------

func (c *Collector) getJSON(ctx context.Context, url string, auth bool) (json.RawMessage, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	if auth && c.token != "" {
		req.Header.Set("Authorization", "Bearer "+c.token)
	}
	resp, err := c.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	return io.ReadAll(io.LimitReader(resp.Body, 64 << 20))
}

type rankingItem struct {
	UserID json.Number `json:"userId"`
	Name   string      `json:"name"`
	Score  int64       `json:"score"`
	Rank   int64       `json:"rank"`
}

func toRankings(items []rankingItem) []Ranking {
	out := make([]Ranking, 0, len(items))
	for _, it := range items {
		if it.Rank == 0 {
			continue
		}
		out = append(out, Ranking{UID: it.UserID.String(), Name: it.Name, Score: it.Score, Rank: it.Rank})
	}
	return out
}

// mergeByRank 按排名去重，先到优先（与 Python merge_rankings 一致）。
func mergeByRank(groups ...[]Ranking) []Ranking {
	seen := map[int64]bool{}
	var out []Ranking
	for _, g := range groups {
		for _, r := range g {
			if !seen[r.Rank] {
				seen[r.Rank] = true
				out = append(out, r)
			}
		}
	}
	return out
}

// payload 兼容 {"data": {...}} 包装。
type payload struct {
	Data     json.RawMessage `json:"data"`
	Rankings []rankingItem   `json:"rankings"`
	// Haruki 档线
	BorderRankings []rankingItem `json:"borderRankings"`
	// WL 分组（Haruki 前百 / 档线两种键名）
	UserWorldBloomChapterRankings       []wlGroup `json:"userWorldBloomChapterRankings"`
	WorldBloomChapterRankings           []wlGroup `json:"worldBloomChapterRankings"`
	UserWorldBloomChapterRankingBorders []wlGroup `json:"userWorldBloomChapterRankingBorders"`
	WorldBloomChapterRankingBorders     []wlGroup `json:"worldBloomChapterRankingBorders"`
}

type wlGroup struct {
	GameCharacterID int64         `json:"gameCharacterId"`
	Rankings        []rankingItem `json:"rankings"`
	BorderRankings  []rankingItem `json:"borderRankings"`
	Ranking         []rankingItem `json:"ranking"`
}

func parsePayload(raw json.RawMessage) payload {
	var p payload
	_ = json.Unmarshal(raw, &p)
	if len(p.Data) > 0 {
		var inner payload
		if err := json.Unmarshal(p.Data, &inner); err == nil {
			return inner
		}
	}
	return p
}

func (g wlGroup) items() []rankingItem {
	if len(g.Rankings) > 0 {
		return g.Rankings
	}
	if len(g.BorderRankings) > 0 {
		return g.BorderRankings
	}
	return g.Ranking
}

func wlByCharacter(p payload) map[int64][]Ranking {
	out := map[int64][]Ranking{}
	for _, groups := range [][]wlGroup{
		p.UserWorldBloomChapterRankings, p.WorldBloomChapterRankings,
		p.UserWorldBloomChapterRankingBorders, p.WorldBloomChapterRankingBorders,
	} {
		for _, g := range groups {
			if g.GameCharacterID == 0 {
				continue
			}
			out[g.GameCharacterID] = mergeByRank(out[g.GameCharacterID], toRankings(g.items()))
		}
	}
	return out
}

// ---------- 主流程 ----------

func (c *Collector) collectAll(ctx context.Context) {
	fetchHaruki := time.Since(c.harukiLast) >= 3*time.Minute
	if fetchHaruki {
		c.harukiLast = time.Now()
	}
	for region, rc := range c.cfg {
		eventID, going := c.currentEventID(region)
		if !going {
			continue
		}
		// 1) 新 API 高频更新总榜
		if url := rc.API.RankingTop100NewAPIURL; url != "" {
			if err := c.collectNewAPI(ctx, region, eventID, url); err != nil {
				log.Printf("[ranking] %s new-api: %v", region, err)
			}
		}
		// 2) Haruki 低频补总榜档线 + WL 分榜
		if fetchHaruki {
			if err := c.collectHaruki(ctx, region, rc, eventID); err != nil {
				log.Printf("[ranking] %s haruki: %v", region, err)
			}
		}
	}
}

func (c *Collector) collectNewAPI(ctx context.Context, region string, eventID int64, url string) error {
	raw, err := c.getJSON(ctx, url, true)
	if err != nil {
		return err
	}
	p := parsePayload(raw)
	rankings := toRankings(p.Rankings)
	if len(rankings) == 0 {
		return fmt.Errorf("empty rankings")
	}
	c.mu.Lock()
	c.latest[region] = raw
	c.mu.Unlock()

	if err := c.record(region, eventID, rankings); err != nil {
		return err
	}
	return c.writeSnapshot(region, rankings)
}

func (c *Collector) collectHaruki(ctx context.Context, region string, rc masterdata.RegionConfig, eventID int64) error {
	var top100, border payload
	gotAny := false
	if u := rc.API.RankingTop100APIURL; u != "" {
		if raw, err := c.getJSON(ctx, strings.ReplaceAll(u, "{event_id}", fmt.Sprint(eventID)), true); err == nil {
			top100 = parsePayload(raw)
			gotAny = true
		}
	}
	if u := rc.API.RankingBorderAPIURL; u != "" {
		if raw, err := c.getJSON(ctx, strings.ReplaceAll(u, "{event_id}", fmt.Sprint(eventID)), true); err == nil {
			border = parsePayload(raw)
			gotAny = true
		}
	}
	if !gotAny {
		return fmt.Errorf("no haruki endpoints reachable")
	}

	main := mergeByRank(toRankings(top100.Rankings), toRankings(border.BorderRankings))
	if len(main) > 0 {
		if err := c.record(region, eventID, main); err != nil {
			return err
		}
	}

	// WL 分榜: encoded id = chapterNo*1000 + baseEventID
	chapters := c.wlChapters(region, eventID)
	if len(chapters) == 0 {
		return nil
	}
	byChar := map[int64][]Ranking{}
	for cid, rs := range wlByCharacter(top100) {
		byChar[cid] = mergeByRank(byChar[cid], rs)
	}
	for cid, rs := range wlByCharacter(border) {
		byChar[cid] = mergeByRank(byChar[cid], rs)
	}
	for cid, rs := range byChar {
		chapterNo, ok := chapters[cid]
		if !ok || len(rs) == 0 {
			continue
		}
		encoded := chapterNo*wlEventIDFactor + eventID
		if err := c.record(region, encoded, rs); err != nil {
			log.Printf("[ranking] %s WL chapter %d: %v", region, chapterNo, err)
		}
	}
	return nil
}

// writeSnapshot 维护 Python callapi 读取的 sktop100.json。
func (c *Collector) writeSnapshot(region string, rankings []Ranking) error {
	items := make([]map[string]any, 0, len(rankings))
	for _, r := range rankings {
		items = append(items, map[string]any{
			"userId": r.UID, "name": r.Name, "score": r.Score, "rank": r.Rank,
		})
	}
	data, err := json.Marshal(map[string]any{"rankings": items})
	if err != nil {
		return err
	}
	dir := filepath.Join(c.mdDir, region)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	tmp := filepath.Join(dir, ".sktop100.json.tmp")
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, filepath.Join(dir, "sktop100.json"))
}

// HandleLatest 处理 GET /ranking/{region}/latest。
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
