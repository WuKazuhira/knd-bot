// Package skstore 读取 sk 榜线时序 sqlite 库（由 go-pjsk-helper 采集写入），
// 对齐 old-python _sk_sql 的 query_latest_ranking / query_first_ranking_after。
//
// 只读：Go 侧不采集榜线（采集由 go-pjsk-helper 负责），这里仅取最新/历史快照
// 供时速与排名线计算使用。使用纯 Go 的 modernc.org/sqlite，无需 cgo。
package skstore

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	_ "modernc.org/sqlite"

	"github.com/kazuhira/go-pjsk-bot/internal/skranking"
)

type dbEntry struct {
	db       *sql.DB
	path     string
	stat     os.FileInfo
	isRLE    bool
	lastUsed time.Time
}

const (
	idleConnTTL   = 10 * time.Minute
	sweepInterval = 2 * time.Minute
)

// Store 打开并查询某活动的榜线时序库，兼容平铺 ranking 表与 rle-v1 无损库（含实时流式与历史归档）。
type Store struct {
	dbRoot      string // ondemand/database
	archiveRoot string // archive/ranking

	mu        sync.Mutex
	dbs       map[string]dbEntry // region/event_id -> 只读连接与格式标记
	lastSweep time.Time
}

// New 创建 Store。dataDir 为 data/pjsk。
func New(dataDir string) *Store {
	return &Store{
		dbRoot:      filepath.Join(dataDir, "ondemand", "database"),
		archiveRoot: filepath.Join(dataDir, "archive", "ranking"),
		dbs:         make(map[string]dbEntry),
	}
}

// dbPath 返回某服/活动的时序库路径：sk_{region}/{event_id}_ranking.db。
func (s *Store) dbPath(region string, eventID int) string {
	return filepath.Join(s.dbRoot, "sk_"+region, fmt.Sprintf("%d_ranking.db", eventID))
}

func (s *Store) archivePath(region string, eventID int) string {
	return filepath.Join(s.archiveRoot, "sk_"+region, fmt.Sprintf("%d_ranking.db", eventID))
}

func (s *Store) resolvePath(region string, eventID int) (string, os.FileInfo, bool) {
	path := s.dbPath(region, eventID)
	if st, err := os.Stat(path); err == nil {
		return path, st, true
	}
	arch := s.archivePath(region, eventID)
	if st, err := os.Stat(arch); err == nil {
		return arch, st, true
	}
	return "", nil, false
}

func (s *Store) sweepIdleLocked(now time.Time) []*sql.DB {
	if now.Sub(s.lastSweep) < sweepInterval {
		return nil
	}
	s.lastSweep = now
	var stale []*sql.DB
	for k, e := range s.dbs {
		if now.Sub(e.lastUsed) > idleConnTTL {
			if e.db != nil {
				stale = append(stale, e.db)
			}
			delete(s.dbs, k)
		}
	}
	return stale
}

func (s *Store) open(region string, eventID int) (dbEntry, bool, error) {
	key := fmt.Sprintf("%s/%d", region, eventID)
	now := time.Now()
	path, curStat, exists := s.resolvePath(region, eventID)

	var toClose []*sql.DB
	s.mu.Lock()
	toClose = append(toClose, s.sweepIdleLocked(now)...)
	if entry, ok := s.dbs[key]; ok && entry.db != nil {
		if exists && entry.path == path && entry.stat != nil && os.SameFile(entry.stat, curStat) {
			entry.lastUsed = now
			s.dbs[key] = entry
			s.mu.Unlock()
			for _, db := range toClose {
				_ = db.Close()
			}
			return entry, true, nil
		}
		// 磁盘文件已替换、归档迁移或删除：释放旧句柄后按新路径重开。
		toClose = append(toClose, entry.db)
		delete(s.dbs, key)
	}
	s.mu.Unlock()
	for _, db := range toClose {
		_ = db.Close()
	}

	if !exists {
		return dbEntry{}, false, nil // 库不存在：视为无数据（对齐 create=False 返回 []）
	}

	// 只读模式打开，避免与采集进程写冲突；busy_timeout 避免 WAL 提交瞬间偶发 SQLITE_BUSY。
	db, err := sql.Open("sqlite", "file:"+path+"?mode=ro&_pragma=busy_timeout(5000)")
	if err != nil {
		return dbEntry{}, false, err
	}
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)

	var hasRuns int
	if err := db.QueryRow("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs' LIMIT 1").Scan(&hasRuns); err != nil && err != sql.ErrNoRows {
		_ = db.Close()
		return dbEntry{}, false, err
	}
	entry := dbEntry{db: db, path: path, stat: curStat, isRLE: hasRuns == 1, lastUsed: now}

	s.mu.Lock()
	if existing, ok := s.dbs[key]; ok && existing.db != nil && existing.path == path && existing.stat != nil && os.SameFile(existing.stat, curStat) {
		existing.lastUsed = now
		s.dbs[key] = existing
		s.mu.Unlock()
		_ = db.Close()
		return existing, true, nil
	}
	if prev, ok := s.dbs[key]; ok && prev.db != nil {
		defer prev.db.Close()
	}
	s.dbs[key] = entry
	s.mu.Unlock()
	return entry, true, nil
}

// Close 关闭 Store 持有的所有只读 SQLite 连接。
func (s *Store) Close() {
	s.mu.Lock()
	dbs := s.dbs
	s.dbs = make(map[string]dbEntry)
	s.mu.Unlock()
	for _, entry := range dbs {
		if entry.db != nil {
			_ = entry.db.Close()
		}
	}
}

func scanRankings(rows *sql.Rows) ([]skranking.Ranking, error) {
	var out []skranking.Ranking
	for rows.Next() {
		var id int64
		var uid, name string
		var score int64
		var rank int
		var ts float64
		if err := rows.Scan(&id, &uid, &name, &score, &rank, &ts); err != nil {
			return nil, err
		}
		out = append(out, skranking.Ranking{
			UID: uid, Name: name, Score: score, Rank: rank,
			Time: time.Unix(int64(ts), 0),
		})
	}
	return out, rows.Err()
}

// QueryLatestRanking 取指定名次的最新一条榜线，对齐 query_latest_ranking。
func (s *Store) QueryLatestRanking(ctx context.Context, region string, eventID int, ranks []int) ([]skranking.Ranking, error) {
	entry, ok, err := s.open(region, eventID)
	if err != nil || !ok {
		return nil, err
	}
	db := entry.db

	if entry.isRLE {
		if len(ranks) > 0 {
			values := make([]string, len(ranks))
			args := make([]any, 0, len(ranks)*2)
			for i, rank := range ranks {
				values[i] = "(?, ?)"
				args = append(args, rank, i)
			}
			query := `WITH requested(rank, ordinal) AS (VALUES ` + strings.Join(values, ", ") + `),
max_b(bid) AS (SELECT MAX(batch_id) FROM batches)
SELECT (CASE WHEN r.end_batch > mb.bid THEN mb.bid ELSE r.end_batch END), i.uid, i.name, r.score, r.rank, b.ts
FROM requested AS q
CROSS JOIN max_b AS mb
JOIN runs AS r ON r.rank = q.rank AND r.start_batch = (
    SELECT start_batch FROM runs WHERE rank = q.rank ORDER BY start_batch DESC LIMIT 1
)
JOIN identities AS i ON i.id = r.identity_id
JOIN batches AS b ON b.batch_id = (CASE WHEN r.end_batch > mb.bid THEN mb.bid ELSE r.end_batch END)
ORDER BY q.ordinal`
			rows, err := db.QueryContext(ctx, query, args...)
			if err != nil {
				return nil, err
			}
			defer rows.Close()
			return scanRankings(rows)
		}
		rows, err := db.QueryContext(ctx, `
			WITH max_b(bid) AS (SELECT MAX(batch_id) FROM batches)
			SELECT (CASE WHEN r.end_batch > mb.bid THEN mb.bid ELSE r.end_batch END), i.uid, i.name, r.score, r.rank, b.ts
			FROM runs AS r
			CROSS JOIN max_b AS mb
			JOIN (
				SELECT rank, MAX(start_batch) AS max_sb FROM runs GROUP BY rank
			) AS m ON r.rank = m.rank AND r.start_batch = m.max_sb
			JOIN identities AS i ON i.id = r.identity_id
			JOIN batches AS b ON b.batch_id = (CASE WHEN r.end_batch > mb.bid THEN mb.bid ELSE r.end_batch END)
			ORDER BY r.rank`)
		if err != nil {
			return nil, err
		}
		defer rows.Close()
		return scanRankings(rows)
	}

	if len(ranks) > 0 {
		// 单条 SELECT 保证所有名次读取同一数据库快照；同秒多批次按 ID 取最新。
		values := make([]string, len(ranks))
		args := make([]any, 0, len(ranks)*2)
		for i, rank := range ranks {
			values[i] = "(?, ?)"
			args = append(args, rank, i)
		}
		query := `WITH requested(rank, ordinal) AS (VALUES ` + strings.Join(values, ", ") + `)
SELECT r.id, r.uid, r.name, r.score, r.rank, r.ts
FROM requested AS q
JOIN ranking AS r ON r.id = (
    SELECT id FROM ranking WHERE rank = q.rank ORDER BY ts DESC, id DESC LIMIT 1
)
ORDER BY q.ordinal`
		rows, err := db.QueryContext(ctx, query, args...)
		if err != nil {
			return nil, err
		}
		defer rows.Close()
		return scanRankings(rows)
	}
	rows, err := db.QueryContext(ctx, `
		SELECT id, uid, name, score, rank, ts FROM ranking WHERE id IN (
			SELECT MAX(id) FROM ranking GROUP BY rank
		) ORDER BY rank`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanRankings(rows)
}

// QueryFirstRankingAfter 取指定名次在 after 之后的最早一条榜线，
// 对齐 query_first_ranking_after（用于算时速的历史基准点）。
func (s *Store) QueryFirstRankingAfter(ctx context.Context, region string, eventID int, after time.Time, ranks []int) ([]skranking.Ranking, error) {
	entry, ok, err := s.open(region, eventID)
	if err != nil || !ok {
		return nil, err
	}
	db := entry.db

	afterTS := float64(after.Unix())
	if entry.isRLE {
		var minBatch int64
		if err := db.QueryRowContext(ctx, "SELECT COALESCE(MIN(batch_id), 0) FROM batches WHERE ts > ?", afterTS).Scan(&minBatch); err != nil {
			return nil, err
		}
		if minBatch == 0 {
			return nil, nil
		}
		if len(ranks) > 0 {
			values := make([]string, len(ranks))
			args := make([]any, 0, len(ranks)*2+1)
			for i, rank := range ranks {
				values[i] = "(?, ?)"
				args = append(args, rank, i)
			}
			args = append(args, minBatch)
			query := `WITH requested(rank, ordinal) AS (VALUES ` + strings.Join(values, ", ") + `),
params(min_bid) AS (VALUES (?)),
first_batch AS (
    SELECT q.rank, q.ordinal, (
        SELECT b.batch_id
        FROM sig_ranks AS sr
        JOIN batches AS b ON b.sig_id = sr.sig_id
        WHERE sr.rank = q.rank AND b.batch_id >= (SELECT min_bid FROM params)
        ORDER BY b.batch_id ASC LIMIT 1
    ) AS bid
    FROM requested AS q
)
SELECT fb.bid, i.uid, i.name, r.score, r.rank, b.ts
FROM first_batch AS fb
JOIN batches AS b ON b.batch_id = fb.bid
JOIN runs AS r ON r.rank = fb.rank AND r.start_batch = (
    SELECT start_batch FROM runs
    WHERE rank = fb.rank AND start_batch <= fb.bid
    ORDER BY start_batch DESC LIMIT 1
) AND r.end_batch >= fb.bid
JOIN identities AS i ON i.id = r.identity_id
ORDER BY fb.ordinal`
			rows, err := db.QueryContext(ctx, query, args...)
			if err != nil {
				return nil, err
			}
			defer rows.Close()
			return scanRankings(rows)
		}
		rows, err := db.QueryContext(ctx, `
			WITH first_batch AS (
				SELECT sr.rank, MIN(b.batch_id) AS bid
				FROM sig_ranks AS sr
				JOIN batches AS b ON b.sig_id = sr.sig_id AND b.batch_id >= ?
				GROUP BY sr.rank
			)
			SELECT fb.bid, i.uid, i.name, r.score, r.rank, b.ts
			FROM first_batch AS fb
			JOIN batches AS b ON b.batch_id = fb.bid
			JOIN runs AS r ON r.rank = fb.rank AND r.start_batch = (
				SELECT start_batch FROM runs
				WHERE rank = fb.rank AND start_batch <= fb.bid
				ORDER BY start_batch DESC LIMIT 1
			) AND r.end_batch >= fb.bid
			JOIN identities AS i ON i.id = r.identity_id
			ORDER BY fb.rank`, minBatch)
		if err != nil {
			return nil, err
		}
		defer rows.Close()
		return scanRankings(rows)
	}

	if len(ranks) > 0 {
		// 单条 SELECT 批量获取各档位在 after 之后最早的一条记录，避免 N 次 SQL 往返。
		values := make([]string, len(ranks))
		args := make([]any, 0, len(ranks)*2+1)
		for i, rank := range ranks {
			values[i] = "(?, ?)"
			args = append(args, rank, i)
		}
		args = append(args, afterTS)
		query := `WITH requested(rank, ordinal) AS (VALUES ` + strings.Join(values, ", ") + `)
SELECT r.id, r.uid, r.name, r.score, r.rank, r.ts
FROM requested AS q
JOIN ranking AS r ON r.id = (
    SELECT id FROM ranking WHERE rank = q.rank AND ts > ? ORDER BY ts ASC LIMIT 1
)
ORDER BY q.ordinal`
		rows, err := db.QueryContext(ctx, query, args...)
		if err != nil {
			return nil, err
		}
		defer rows.Close()
		return scanRankings(rows)
	}
	rows, err := db.QueryContext(ctx, `
		SELECT id, uid, name, score, rank, ts FROM ranking WHERE id IN (
			SELECT MIN(id) FROM ranking WHERE ts > ? GROUP BY rank
		) ORDER BY rank`, afterTS)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanRankings(rows)
}

// QueryRankingByUID 取某玩家（uid）的全部历史榜线记录，按时间升序。
// 对齐 query_ranking(uid=...) + history.sort(key=time)。库不存在返回 nil。
func (s *Store) QueryRankingByUID(ctx context.Context, region string, eventID int, uid string) ([]skranking.Ranking, error) {
	entry, ok, err := s.open(region, eventID)
	if err != nil || !ok {
		return nil, err
	}
	db := entry.db
	if entry.isRLE {
		rows, err := db.QueryContext(ctx, `
			SELECT b.batch_id, i.uid, i.name, r.score, r.rank, b.ts
			FROM identities AS i
			JOIN runs AS r ON r.identity_id = i.id
			JOIN sig_ranks AS sr ON sr.rank = r.rank
			JOIN batches AS b ON b.sig_id = sr.sig_id AND b.batch_id BETWEEN r.start_batch AND r.end_batch
			WHERE i.uid = ?
			ORDER BY b.batch_id ASC`, uid)
		if err != nil {
			return nil, err
		}
		defer rows.Close()
		return scanRankings(rows)
	}
	rows, err := db.QueryContext(ctx,
		"SELECT id, uid, name, score, rank, ts FROM ranking WHERE uid = ? ORDER BY ts ASC", uid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanRankings(rows)
}

// QueryRankingTailByUID 读取查房统计所需的最近一小时历史和窗口前最高分，按时间升序返回。
// 额外保留当前分数平台开始前的变分边界，用于计算长时间停车；不再只返回最新
// 分数平台，否则 cf 的近一小时周回数会少算。
func (s *Store) QueryRankingTailByUID(ctx context.Context, region string, eventID int, uid string) ([]skranking.Ranking, int64, error) {
	entry, ok, err := s.open(region, eventID)
	if err != nil || !ok {
		return nil, 0, err
	}
	db := entry.db

	var latestScore int64
	var latestTS float64
	if entry.isRLE {
		err = db.QueryRowContext(ctx, `
			WITH max_b(bid) AS (SELECT MAX(batch_id) FROM batches)
			SELECT r.score, b.ts
			FROM identities AS i
			JOIN runs AS r ON r.identity_id = i.id
			CROSS JOIN max_b AS mb
			JOIN batches AS b ON b.batch_id = (CASE WHEN r.end_batch > mb.bid THEN mb.bid ELSE r.end_batch END)
			WHERE i.uid = ?
			ORDER BY (CASE WHEN r.end_batch > mb.bid THEN mb.bid ELSE r.end_batch END) DESC, r.start_batch DESC LIMIT 1`, uid,
		).Scan(&latestScore, &latestTS)
	} else {
		err = db.QueryRowContext(ctx,
			"SELECT score, ts FROM ranking WHERE uid = ? ORDER BY ts DESC, id DESC LIMIT 1", uid,
		).Scan(&latestScore, &latestTS)
	}
	if err == sql.ErrNoRows {
		return nil, 0, nil
	}
	if err != nil {
		return nil, 0, err
	}

	startTS := latestTS - 3600
	var preWindowMaxScore int64
	if entry.isRLE {
		if err := db.QueryRowContext(ctx, `
			SELECT COALESCE(MAX(r.score), 0)
			FROM identities AS i
			JOIN runs AS r ON r.identity_id = i.id
			JOIN batches AS b_start ON b_start.batch_id = r.start_batch
			WHERE i.uid = ? AND b_start.ts < ?`, uid, startTS,
		).Scan(&preWindowMaxScore); err != nil {
			return nil, 0, err
		}
		var windowStartBatch int64
		_ = db.QueryRowContext(ctx, "SELECT COALESCE(MIN(batch_id), 1) FROM batches WHERE ts >= ?", startTS).Scan(&windowStartBatch)
		rows, err := db.QueryContext(ctx, `
			SELECT b.batch_id, i.uid, i.name, r.score, r.rank, b.ts
			FROM identities AS i
			JOIN runs AS r ON r.identity_id = i.id
			JOIN sig_ranks AS sr ON sr.rank = r.rank
			JOIN batches AS b ON b.sig_id = sr.sig_id AND b.batch_id BETWEEN r.start_batch AND r.end_batch
			WHERE i.uid = ? AND r.end_batch >= ? AND b.ts >= ?
			ORDER BY b.batch_id ASC`, uid, windowStartBatch, startTS,
		)
		if err != nil {
			return nil, 0, err
		}
		out, err := scanRankings(rows)
		rows.Close()
		if err != nil {
			return nil, 0, err
		}

		var boundaryID int64
		var boundary skranking.Ranking
		var boundaryTS float64
		err = db.QueryRowContext(ctx, `
			SELECT b.batch_id, i.uid, i.name, r.score, r.rank, b.ts
			FROM identities AS i
			JOIN runs AS r ON r.identity_id = i.id
			JOIN batches AS b_start ON b_start.batch_id = r.start_batch
			JOIN sig_ranks AS sr ON sr.rank = r.rank
			JOIN batches AS b ON b.sig_id = sr.sig_id AND b.batch_id BETWEEN r.start_batch AND r.end_batch
			WHERE i.uid = ? AND b_start.ts < ? AND r.score != ? AND b.ts < ?
			ORDER BY b.batch_id DESC LIMIT 1`, uid, startTS, latestScore, startTS,
		).Scan(
			&boundaryID, &boundary.UID, &boundary.Name, &boundary.Score, &boundary.Rank, &boundaryTS,
		)
		if err == nil {
			boundary.Time = time.Unix(int64(boundaryTS), 0)
			prefix := []skranking.Ranking{boundary}

			var transition skranking.Ranking
			var transitionTS float64
			transitionErr := db.QueryRowContext(ctx, `
				SELECT b.batch_id, i.uid, i.name, r.score, r.rank, b.ts
				FROM identities AS i
				JOIN runs AS r ON r.identity_id = i.id
				JOIN sig_ranks AS sr ON sr.rank = r.rank
				JOIN batches AS b ON b.sig_id = sr.sig_id AND b.batch_id BETWEEN r.start_batch AND r.end_batch
				WHERE i.uid = ? AND r.end_batch > ? AND b.ts < ? AND r.score = ? AND b.batch_id > ?
				ORDER BY b.batch_id ASC LIMIT 1`, uid, boundaryID, startTS, latestScore, boundaryID,
			).Scan(
				new(int64), &transition.UID, &transition.Name, &transition.Score, &transition.Rank, &transitionTS,
			)
			if transitionErr == nil {
				transition.Time = time.Unix(int64(transitionTS), 0)
				prefix = append(prefix, transition)
			} else if transitionErr != sql.ErrNoRows {
				return nil, 0, transitionErr
			}
			out = append(prefix, out...)
		} else if err != sql.ErrNoRows {
			return nil, 0, err
		}
		return out, preWindowMaxScore, nil
	}

	if err := db.QueryRowContext(ctx,
		"SELECT COALESCE(MAX(score), 0) FROM ranking WHERE uid = ? AND ts < ?", uid, startTS,
	).Scan(&preWindowMaxScore); err != nil {
		return nil, 0, err
	}
	rows, err := db.QueryContext(ctx,
		"SELECT id, uid, name, score, rank, ts FROM ranking WHERE uid = ? AND ts >= ? ORDER BY ts ASC, id ASC",
		uid, startTS,
	)
	if err != nil {
		return nil, 0, err
	}
	out, err := scanRankings(rows)
	rows.Close()
	if err != nil {
		return nil, 0, err
	}

	// 找到当前分数平台开始前的最近异分记录，并尽量补上平台开始的那条记录，
	// 这样 BuildActivityStats 能同时保留近一小时周回和长时间停车边界。
	var boundaryID int64
	var boundary skranking.Ranking
	var boundaryTS float64
	err = db.QueryRowContext(ctx, `
		SELECT id, uid, name, score, rank, ts
		FROM ranking
		WHERE uid = ? AND ts < ? AND score != ?
		ORDER BY ts DESC, id DESC LIMIT 1`, uid, startTS, latestScore,
	).Scan(
		&boundaryID, &boundary.UID, &boundary.Name, &boundary.Score, &boundary.Rank, &boundaryTS,
	)
	if err == nil {
		boundary.Time = time.Unix(int64(boundaryTS), 0)
		prefix := []skranking.Ranking{boundary}

		var transition skranking.Ranking
		var transitionTS float64
		transitionErr := db.QueryRowContext(ctx, `
			SELECT id, uid, name, score, rank, ts
			FROM ranking
			WHERE uid = ? AND ts < ? AND score = ?
			  AND (ts > ? OR (ts = ? AND id > ?))
			ORDER BY ts ASC, id ASC LIMIT 1`,
			uid, startTS, latestScore, boundaryTS, boundaryTS, boundaryID,
		).Scan(
			new(int64), &transition.UID, &transition.Name, &transition.Score, &transition.Rank, &transitionTS,
		)
		if transitionErr == nil {
			transition.Time = time.Unix(int64(transitionTS), 0)
			prefix = append(prefix, transition)
		} else if transitionErr != sql.ErrNoRows {
			return nil, 0, transitionErr
		}
		out = append(prefix, out...)
	} else if err != sql.ErrNoRows {
		return nil, 0, err
	}
	return out, preWindowMaxScore, nil
}

// QueryRankingByRank 取某名次（rank）的全部历史榜线记录，按时间升序。
// 对齐 query_ranking(rank=..., order_by='ts ASC')。库不存在返回 nil。
func (s *Store) QueryRankingByRank(ctx context.Context, region string, eventID, rank int) ([]skranking.Ranking, error) {
	entry, ok, err := s.open(region, eventID)
	if err != nil || !ok {
		return nil, err
	}
	db := entry.db
	if entry.isRLE {
		rows, err := db.QueryContext(ctx, `
			SELECT b.batch_id, i.uid, i.name, r.score, r.rank, b.ts
			FROM runs AS r
			JOIN identities AS i ON i.id = r.identity_id
			JOIN sig_ranks AS sr ON sr.rank = r.rank
			JOIN batches AS b ON b.sig_id = sr.sig_id AND b.batch_id BETWEEN r.start_batch AND r.end_batch
			WHERE r.rank = ?
			ORDER BY b.batch_id ASC`, rank)
		if err != nil {
			return nil, err
		}
		defer rows.Close()
		return scanRankings(rows)
	}
	rows, err := db.QueryContext(ctx,
		"SELECT id, uid, name, score, rank, ts FROM ranking WHERE rank = ? ORDER BY ts ASC", rank)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanRankings(rows)
}
