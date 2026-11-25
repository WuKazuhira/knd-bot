package ranking

import (
	"bytes"
	"context"
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
	"sync"
	"time"

	"github.com/kazuhira/go-pjsk-helper/internal/masterdata"
)

const (
	wlEventIDFactor       = 1000
	harukiRefreshInterval = 180 * time.Second
	wlRefreshInterval     = 30 * time.Second
	endedGracePeriod      = time.Hour
	endedSweepInterval    = 5 * time.Minute
)

type Ranking struct {
	UID   string
	Name  string
	Score int64
	Rank  int64
}

type Collector struct {
	cfg          masterdata.Config
	mdDir        string
	dbDir        string
	token        string
	client       *http.Client
	harukiClient *http.Client

	mu           sync.RWMutex
	latest       map[string]json.RawMessage
	lastSnapshot map[string][]byte
	dbs          map[string]*rankingDB

	harukiLast time.Time
	wlLast     time.Time
	sweepLast  time.Time
}

func NewCollector(cfg masterdata.Config, mdDir, dbDir, token string) *Collector {
	return &Collector{
		cfg:          cfg,
		mdDir:        mdDir,
		dbDir:        dbDir,
		token:        token,
		client:       &http.Client{Timeout: 10 * time.Second},
		harukiClient: &http.Client{Timeout: 15 * time.Second},
		latest:       map[string]json.RawMessage{},
		lastSnapshot: map[string][]byte{},
		dbs:          map[string]*rankingDB{},
	}
}

func (c *Collector) Run(ctx context.Context, interval time.Duration) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	c.sweepEndedDBs(true)
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
	dbs := c.dbs
	c.dbs = map[string]*rankingDB{}
	c.mu.Unlock()
	for _, db := range dbs {
		_ = db.close()
	}
}

type eventInfo struct {
	ID          int64  `json:"id"`
	StartAt     int64  `json:"startAt"`
	AggregateAt int64  `json:"aggregateAt"`
	EventType   string `json:"eventType"`
}

func (c *Collector) loadEvents(region string) ([]eventInfo, error) {
	raw, err := os.ReadFile(filepath.Join(c.mdDir, region, "events.json"))
	if err != nil {
		return nil, err
	}
	var events []eventInfo
	if err := json.Unmarshal(raw, &events); err != nil {
		return nil, err
	}
	return events, nil
}

func (c *Collector) currentEvent(region string) (eventInfo, bool) {
	events, err := c.loadEvents(region)
	if err != nil {
		return eventInfo{}, false
	}
	now := time.Now().UnixMilli()
	for _, e := range events {
		if e.StartAt < now && now < e.AggregateAt {
			return e, true
		}
	}
	return eventInfo{}, false
}

func (c *Collector) apiMode() string {
	raw, err := os.ReadFile(filepath.Join(c.mdDir, "sk_api_state.json"))
	if err == nil {
		var st struct {
			Mode string `json:"mode"`
		}
		if json.Unmarshal(raw, &st) == nil && (st.Mode == "new" || st.Mode == "old") {
			return st.Mode
		}
	}
	return "new"
}

type wlChapter struct {
	EventID         int64 `json:"eventId"`
	GameCharacterID int64 `json:"gameCharacterId"`
	ChapterNo       int64 `json:"chapterNo"`
}

func (c *Collector) wlChapters(region string, eventID int64) map[int64]int64 {
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

func (c *Collector) db(region string, eventID int64) (*rankingDB, error) {
	key := fmt.Sprintf("%s/%d", region, eventID)
	c.mu.RLock()
	if db, ok := c.dbs[key]; ok {
		c.mu.RUnlock()
		return db, nil
	}
	c.mu.RUnlock()

	c.mu.Lock()
	defer c.mu.Unlock()
	if db, ok := c.dbs[key]; ok {
		return db, nil
	}
	path := filepath.Join(c.dbDir, "sk_"+region, fmt.Sprintf("%d_ranking.db", eventID))
	rdb, err := openRankingDB(path)
	if err != nil {
		return nil, err
	}
	c.dbs[key] = rdb
	return rdb, nil
}

func (c *Collector) record(region string, eventID int64, rankings []Ranking) error {
	if len(rankings) == 0 {
		return nil
	}
	db, err := c.db(region, eventID)
	if err != nil {
		return err
	}
	return db.record(rankings, time.Now().Unix())
}

// 关闭并封口已结束的活动库，防止旧句柄一直占着 WAL 截不断
func (c *Collector) sweepEndedDBs(scanDisk bool) {
	nowMs := time.Now().UnixMilli()
	graceMs := endedGracePeriod.Milliseconds()
	endedByRegion := make(map[string]map[int64]bool, len(c.cfg))
	for region := range c.cfg {
		events, err := c.loadEvents(region)
		if err != nil {
			continue
		}
		ended := make(map[int64]bool, len(events))
		for _, e := range events {
			if e.AggregateAt > 0 && nowMs > e.AggregateAt+graceMs {
				ended[e.ID] = true
			}
		}
		endedByRegion[region] = ended
	}

	baseEventID := func(id int64) int64 {
		if id >= wlEventIDFactor {
			return id % wlEventIDFactor
		}
		return id
	}

	var toClose []*rankingDB
	c.mu.Lock()
	for key, rdb := range c.dbs {
		parts := strings.SplitN(key, "/", 2)
		if len(parts) != 2 {
			continue
		}
		eid, err := strconv.ParseInt(parts[1], 10, 64)
		if err != nil {
			continue
		}
		if endedByRegion[parts[0]][baseEventID(eid)] {
			toClose = append(toClose, rdb)
			delete(c.dbs, key)
		}
	}
	c.mu.Unlock()

	for _, rdb := range toClose {
		log.Printf("[ranking] closing ended event db: %s", rdb.path)
		_ = rdb.sealAndClose()
	}

	if !scanDisk {
		return
	}
	for region, ended := range endedByRegion {
		dir := filepath.Join(c.dbDir, "sk_"+region)
		entries, err := os.ReadDir(dir)
		if err != nil {
			continue
		}
		for _, ent := range entries {
			if ent.IsDir() || !strings.HasSuffix(ent.Name(), "_ranking.db") {
				continue
			}
			stem := strings.TrimSuffix(ent.Name(), "_ranking.db")
			eid, err := strconv.ParseInt(stem, 10, 64)
			if err != nil || !ended[baseEventID(eid)] {
				continue
			}
			key := fmt.Sprintf("%s/%d", region, eid)
			c.mu.RLock()
			_, open := c.dbs[key]
			c.mu.RUnlock()
			if open {
				continue
			}
			if err := sealUnsealedRLEFile(filepath.Join(dir, ent.Name())); err != nil {
				log.Printf("[ranking] %v", err)
			}
		}
	}
}

func (c *Collector) getJSON(ctx context.Context, client *http.Client, url string, auth bool) (json.RawMessage, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	if auth && c.token != "" {
		req.Header.Set("Authorization", "Bearer "+c.token)
		req.Header.Set("X-Haruki-Sekai-Token", c.token)
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	return io.ReadAll(io.LimitReader(resp.Body, 64<<20))
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

type payload struct {
	Data     json.RawMessage `json:"data"`
	Rankings []rankingItem   `json:"rankings"`
	BorderRankings []rankingItem `json:"borderRankings"`
	UserWorldBloomChapterRankings       []wlGroup `json:"userWorldBloomChapterRankings"`
	WorldBloomChapterRankings           []wlGroup `json:"worldBloomChapterRankings"`
	UserWorldBloomChapterRankingBorders []wlGroup `json:"userWorldBloomChapterRankingBorders"`
	WorldBloomChapterRankingBorders     []wlGroup `json:"worldBloomChapterRankingBorders"`
	Groups                              []wlGroup `json:"groups"`
}

type wlGroup struct {
	GameCharacterID      int64         `json:"gameCharacterId"`
	SnakeGameCharacterID int64         `json:"game_character_id"`
	Rankings             []rankingItem `json:"rankings"`
	BorderRankings       []rankingItem `json:"borderRankings"`
	Ranking              []rankingItem `json:"ranking"`
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
		p.Groups,
	} {
		for _, g := range groups {
			if g.GameCharacterID == 0 {
				g.GameCharacterID = g.SnakeGameCharacterID
			}
			if g.GameCharacterID == 0 {
				continue
			}
			out[g.GameCharacterID] = mergeByRank(out[g.GameCharacterID], toRankings(g.items()))
		}
	}
	return out
}

func hasBorderRanks(rankings []Ranking) bool {
	for _, r := range rankings {
		if r.Rank > 100 {
			return true
		}
	}
	return false
}

func (c *Collector) collectAll(ctx context.Context) {
	now := time.Now()
	if c.sweepLast.IsZero() || now.Sub(c.sweepLast) >= endedSweepInterval {
		c.sweepLast = now
		c.sweepEndedDBs(false)
	}

	fetchHaruki := c.harukiLast.IsZero() || now.Sub(c.harukiLast) >= harukiRefreshInterval
	if fetchHaruki {
		c.harukiLast = now
	}
	fetchWL := c.wlLast.IsZero() || now.Sub(c.wlLast) >= wlRefreshInterval
	if fetchWL {
		c.wlLast = now
	}
	mode := c.apiMode()

	var wg sync.WaitGroup
	for region, rc := range c.cfg {
		region, rc := region, rc
		wg.Add(1)
		go func() {
			defer wg.Done()
			c.collectRegion(ctx, region, rc, mode, fetchHaruki, fetchWL)
		}()
	}
	wg.Wait()
}

func (c *Collector) collectRegion(ctx context.Context, region string, rc masterdata.RegionConfig, mode string, fetchHaruki, fetchWL bool) {
	ev, going := c.currentEvent(region)
	if !going {
		return
	}
	eventID := ev.ID
	chapters := c.wlChapters(region, eventID)
	isWL := ev.EventType == "world_bloom" || len(chapters) > 0

	var harukiTop100, harukiBorder payload
	harukiFetched := false
	fetchHarukiSnapshots := func() bool {
		if harukiFetched {
			return len(harukiTop100.Rankings) > 0 || len(harukiBorder.BorderRankings) > 0
		}
		harukiFetched = true
		gotAny := false
		if u := rc.API.RankingTop100APIURL; u != "" {
			if raw, err := c.getJSON(ctx, c.harukiClient, strings.ReplaceAll(u, "{event_id}", fmt.Sprint(eventID)), true); err == nil {
				harukiTop100 = parsePayload(raw)
				gotAny = true
			}
		}
		if u := rc.API.RankingBorderAPIURL; u != "" {
			if raw, err := c.getJSON(ctx, c.harukiClient, strings.ReplaceAll(u, "{event_id}", fmt.Sprint(eventID)), true); err == nil {
				harukiBorder = parsePayload(raw)
				gotAny = true
			}
		}
		return gotAny
	}

	// new 模式直接用 new-api（前百和档线都有），失败或缺档线再低频切 Haruki
	if mode == "new" && rc.API.RankingTop100NewAPIURL != "" {
		rankings, err := c.collectNewAPI(ctx, region, eventID, rc.API.RankingTop100NewAPIURL)
		if err != nil {
			log.Printf("[ranking] %s new-api: %v", region, err)
			if fetchHaruki && fetchHarukiSnapshots() {
				main := mergeByRank(toRankings(harukiTop100.Rankings), toRankings(harukiBorder.BorderRankings))
				if len(main) > 0 {
					if rerr := c.record(region, eventID, main); rerr != nil {
						log.Printf("[ranking] %s haruki fallback record: %v", region, rerr)
					}
					_ = c.writeSnapshot(region, main)
				}
			}
		} else if !hasBorderRanks(rankings) && fetchHaruki {
			if u := rc.API.RankingBorderAPIURL; u != "" {
				if raw, berr := c.getJSON(ctx, c.harukiClient, strings.ReplaceAll(u, "{event_id}", fmt.Sprint(eventID)), true); berr == nil {
					harukiBorder = parsePayload(raw)
					merged := mergeByRank(rankings, toRankings(harukiBorder.BorderRankings))
					if len(merged) > len(rankings) {
						if rerr := c.record(region, eventID, merged); rerr != nil {
							log.Printf("[ranking] %s haruki border supplement: %v", region, rerr)
						}
						_ = c.writeSnapshot(region, merged)
					}
				}
			}
		}
	} else if fetchHaruki {
		if fetchHarukiSnapshots() {
			main := mergeByRank(toRankings(harukiTop100.Rankings), toRankings(harukiBorder.BorderRankings))
			if len(main) > 0 {
				if err := c.record(region, eventID, main); err != nil {
					log.Printf("[ranking] %s haruki record: %v", region, err)
				}
				_ = c.writeSnapshot(region, main)
			}
		} else {
			log.Printf("[ranking] %s Haruki endpoints unavailable", region)
		}
	}

	// WL 角色分榜优先走公开接口，少打 Haruki 防限流
	if !isWL || len(chapters) == 0 {
		return
	}
	if mode == "new" && !fetchWL {
		return
	}
	if mode != "new" && !fetchHaruki {
		return
	}
	c.collectWorldLink(ctx, region, eventID, mode, fetchHaruki, chapters, fetchHarukiSnapshots, &harukiTop100, &harukiBorder)
}

func (c *Collector) collectNewAPI(ctx context.Context, region string, eventID int64, url string) ([]Ranking, error) {
	raw, err := c.getJSON(ctx, c.client, url, true)
	if err != nil {
		return nil, err
	}
	p := parsePayload(raw)
	rankings := toRankings(p.Rankings)
	if len(rankings) == 0 {
		return nil, fmt.Errorf("empty rankings")
	}
	c.mu.Lock()
	c.latest[region] = raw
	c.mu.Unlock()

	if err := c.record(region, eventID, rankings); err != nil {
		return nil, err
	}
	if err := c.writeSnapshot(region, rankings); err != nil {
		return rankings, err
	}
	return rankings, nil
}

func publicWorldLinkURL(region string, rc masterdata.RegionConfig) string {
	if raw := strings.TrimSpace(rc.API.WorldlinkLatestAPIURL); raw != "" {
		return strings.ReplaceAll(raw, "{region}", region)
	}
	scheme, host := "https", "rks-n.exmeaning.com"
	if raw := strings.TrimSpace(rc.API.RankingTop100NewAPIURL); raw != "" {
		if u, err := url.Parse(raw); err == nil && u.Host != "" {
			if u.Scheme != "" {
				scheme = u.Scheme
			}
			host = u.Host
		}
	}
	return fmt.Sprintf("%s://%s/api/public/v2/%s/worldlink-latest", scheme, host, region)
}

func (c *Collector) collectWorldLink(
	ctx context.Context,
	region string,
	eventID int64,
	mode string,
	fetchHaruki bool,
	chapters map[int64]int64,
	fetchHarukiSnapshots func() bool,
	harukiTop100, harukiBorder *payload,
) {
	fetchPublicWL := func() map[int64][]Ranking {
		fallbackURL := publicWorldLinkURL(region, c.cfg[region])
		raw, err := c.getJSON(ctx, c.client, fallbackURL, false)
		if err != nil {
			log.Printf("[ranking] %s public WL failed: %v", region, err)
			return map[int64][]Ranking{}
		}
		return wlByCharacter(parsePayload(raw))
	}

	byChar := map[int64][]Ranking{}
	if mode == "new" {
		byChar = fetchPublicWL()
		if len(byChar) == 0 && fetchHaruki {
			if byChar == nil {
				byChar = map[int64][]Ranking{}
			}
			fetchHarukiSnapshots()
			for cid, rs := range wlByCharacter(*harukiTop100) {
				byChar[cid] = mergeByRank(byChar[cid], rs)
			}
			for cid, rs := range wlByCharacter(*harukiBorder) {
				byChar[cid] = mergeByRank(byChar[cid], rs)
			}
		}
	} else {
		fetchHarukiSnapshots()
		for cid, rs := range wlByCharacter(*harukiTop100) {
			byChar[cid] = mergeByRank(byChar[cid], rs)
		}
		for cid, rs := range wlByCharacter(*harukiBorder) {
			byChar[cid] = mergeByRank(byChar[cid], rs)
		}
		if len(byChar) == 0 {
			byChar = fetchPublicWL()
		}
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
}

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
	c.mu.RLock()
	unchanged := bytes.Equal(c.lastSnapshot[region], data)
	c.mu.RUnlock()
	if unchanged {
		return nil
	}

	dir := filepath.Join(c.mdDir, region)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	tmp := filepath.Join(dir, ".sktop100.json.tmp")
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	if err := os.Rename(tmp, filepath.Join(dir, "sktop100.json")); err != nil {
		return err
	}
	c.mu.Lock()
	c.lastSnapshot[region] = data
	c.mu.Unlock()
	return nil
}

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
