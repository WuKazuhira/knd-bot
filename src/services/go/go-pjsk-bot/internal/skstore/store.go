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
	"time"

	_ "modernc.org/sqlite"

	"github.com/kazuhira/go-pjsk-bot/internal/skranking"
)

// Store 打开并查询某活动的榜线时序库。
type Store struct {
	dbRoot string // ondemand/database
}

// New 创建 Store。dataDir 为 data/pjsk。
func New(dataDir string) *Store {
	return &Store{dbRoot: filepath.Join(dataDir, "ondemand", "database")}
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
	// 只读模式打开，避免与采集进程写冲突。
	return sql.Open("sqlite", "file:"+path+"?mode=ro")
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
	defer db.Close()

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
	defer db.Close()

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
	defer db.Close()
	rows, err := db.QueryContext(ctx,
		"SELECT id, uid, name, score, rank, ts FROM ranking WHERE uid = ? ORDER BY ts ASC", uid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanRankings(rows)
}

// QueryRankingByRank 取某名次（rank）的全部历史榜线记录，按时间升序。
// 对齐 query_ranking(rank=..., order_by='ts ASC')。库不存在返回 nil。
func (s *Store) QueryRankingByRank(ctx context.Context, region string, eventID, rank int) ([]skranking.Ranking, error) {
	db, err := s.open(region, eventID)
	if err != nil || db == nil {
		return nil, err
	}
	defer db.Close()
	rows, err := db.QueryContext(ctx,
		"SELECT id, uid, name, score, rank, ts FROM ranking WHERE rank = ? ORDER BY ts ASC", rank)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanRankings(rows)
}
