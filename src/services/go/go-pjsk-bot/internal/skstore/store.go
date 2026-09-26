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
	"sync"
	"time"

	_ "modernc.org/sqlite"

	"github.com/kazuhira/go-pjsk-bot/internal/skranking"
)

// Store 打开并查询某活动的榜线时序库。
type Store struct {
	dbRoot string // ondemand/database

	mu  sync.Mutex
	dbs map[string]*sql.DB // region/event_id -> 只读连接池
}

// New 创建 Store。dataDir 为 data/pjsk。
func New(dataDir string) *Store {
	return &Store{
		dbRoot: filepath.Join(dataDir, "ondemand", "database"),
		dbs:    make(map[string]*sql.DB),
	}
}

// dbPath 返回某服/活动的时序库路径：sk_{region}/{event_id}_ranking.db。
func (s *Store) dbPath(region string, eventID int) string {
	return filepath.Join(s.dbRoot, "sk_"+region, fmt.Sprintf("%d_ranking.db", eventID))
}

func (s *Store) open(region string, eventID int) (*sql.DB, error) {
	path := s.dbPath(region, eventID)
	if _, err := os.Stat(path); err != nil {
		return nil, nil // 库不存在：视为无数据（对齐 create=False 返回 []）
	}
	key := fmt.Sprintf("%s/%d", region, eventID)
	s.mu.Lock()
	if db := s.dbs[key]; db != nil {
		s.mu.Unlock()
		return db, nil
	}
	s.mu.Unlock()

	// 只读模式打开，避免与采集进程写冲突；同一活动只保留一个底层连接，
	// 避免每条榜线查询都重复建立 modernc SQLite 连接。
	db, err := sql.Open("sqlite", "file:"+path+"?mode=ro")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)

	s.mu.Lock()
	if existing := s.dbs[key]; existing != nil {
		s.mu.Unlock()
		_ = db.Close()
		return existing, nil
	}
	s.dbs[key] = db
	s.mu.Unlock()
	return db, nil
}

// Close 关闭 Store 持有的所有只读 SQLite 连接。
func (s *Store) Close() {
	s.mu.Lock()
	dbs := s.dbs
	s.dbs = make(map[string]*sql.DB)
	s.mu.Unlock()
	for _, db := range dbs {
		_ = db.Close()
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
	db, err := s.open(region, eventID)
	if err != nil || db == nil {
		return nil, err
	}

	var out []skranking.Ranking
	if len(ranks) > 0 {
		for _, rank := range ranks {
			rows, err := db.QueryContext(ctx,
				"SELECT id, uid, name, score, rank, ts FROM ranking WHERE rank = ? ORDER BY ts DESC LIMIT 1", rank)
			if err != nil {
				return nil, err
			}
			rs, err := scanRankings(rows)
			rows.Close()
			if err != nil {
				return nil, err
			}
			out = append(out, rs...)
		}
		return out, nil
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
	db, err := s.open(region, eventID)
	if err != nil || db == nil {
		return nil, err
	}

	afterTS := float64(after.Unix())
	if len(ranks) > 0 {
		// 每个 rank 分区内取 ts 最早的一条（ts > after）
		var out []skranking.Ranking
		for _, rank := range ranks {
			rows, err := db.QueryContext(ctx,
				"SELECT id, uid, name, score, rank, ts FROM ranking WHERE rank = ? AND ts > ? ORDER BY ts ASC LIMIT 1",
				rank, afterTS)
			if err != nil {
				return nil, err
			}
			rs, err := scanRankings(rows)
			rows.Close()
			if err != nil {
				return nil, err
			}
			out = append(out, rs...)
		}
		return out, nil
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
	db, err := s.open(region, eventID)
	if err != nil || db == nil {
		return nil, err
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
	db, err := s.open(region, eventID)
	if err != nil || db == nil {
		return nil, 0, err
	}

	var latestScore int64
	var latestTS float64
	err = db.QueryRowContext(ctx,
		"SELECT score, ts FROM ranking WHERE uid = ? ORDER BY ts DESC, id DESC LIMIT 1", uid,
	).Scan(&latestScore, &latestTS)
	if err == sql.ErrNoRows {
		return nil, 0, nil
	}
	if err != nil {
		return nil, 0, err
	}

	startTS := latestTS - 3600
	var preWindowMaxScore int64
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
	db, err := s.open(region, eventID)
	if err != nil || db == nil {
		return nil, err
	}
	rows, err := db.QueryContext(ctx,
		"SELECT id, uid, name, score, rank, ts FROM ranking WHERE rank = ? ORDER BY ts ASC", rank)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanRankings(rows)
}
