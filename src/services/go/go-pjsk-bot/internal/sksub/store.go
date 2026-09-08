// Package sksub 读写 sk 分数变动订阅 sqlite 库，对齐 old-python
// _sk_subscription 的 subscriptions 表。
//
// 与 Python 侧共享同一个 sqlite 文件 ondemand/database/sk_subscription.db，
// 使用纯 Go 的 modernc.org/sqlite（无需 cgo）。订阅的增删查由 Go 处理；
// 分数变动的定时检测与推送仍由 Python 承担（读同一张表）。
package sksub

import (
	"context"
	"database/sql"
	"os"
	"path/filepath"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

// Store 打开并操作订阅库。
type Store struct {
	path string
	mu   sync.Mutex
	db   *sql.DB
}

// New 创建 Store。dataDir 为 data/pjsk。
func New(dataDir string) *Store {
	return &Store{path: filepath.Join(dataDir, "ondemand", "database", "sk_subscription.db")}
}

// conn 惰性打开连接并建表（与 Python 建表语句一致）。
func (s *Store) conn(ctx context.Context) (*sql.DB, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.db != nil {
		return s.db, nil
	}
	if err := os.MkdirAll(filepath.Dir(s.path), 0o755); err != nil {
		return nil, err
	}
	db, err := sql.Open("sqlite", "file:"+s.path+"?_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)")
	if err != nil {
		return nil, err
	}
	if _, err := db.ExecContext(ctx, `
		CREATE TABLE IF NOT EXISTS subscriptions (
			id          INTEGER PRIMARY KEY AUTOINCREMENT,
			qq_id       TEXT    NOT NULL,
			group_id    TEXT,
			server      TEXT    NOT NULL,
			event_id    INTEGER NOT NULL,
			uid         TEXT    NOT NULL,
			last_score  INTEGER NOT NULL DEFAULT 0,
			last_rank   INTEGER NOT NULL DEFAULT 0,
			last_check_time INTEGER,
			created_at  INTEGER NOT NULL,
			UNIQUE(qq_id, server, event_id)
		)`); err != nil {
		db.Close()
		return nil, err
	}
	if _, err := db.ExecContext(ctx, `
		CREATE INDEX IF NOT EXISTS idx_subscriptions_server_event
		ON subscriptions (server, event_id)`); err != nil {
		db.Close()
		return nil, err
	}
	s.db = db
	return db, nil
}

// Close 关闭连接。
func (s *Store) Close() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.db != nil {
		s.db.Close()
		s.db = nil
	}
}

// Exists 判断某 QQ 是否已订阅某服某活动。对齐 get_subscription 是否为空。
func (s *Store) Exists(ctx context.Context, qqID, server string, eventID int) (bool, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return false, err
	}
	var id int64
	row := db.QueryRowContext(ctx,
		"SELECT id FROM subscriptions WHERE qq_id = ? AND server = ? AND event_id = ?", qqID, server, eventID)
	if err := row.Scan(&id); err == sql.ErrNoRows {
		return false, nil
	} else if err != nil {
		return false, err
	}
	return true, nil
}

// Add 添加订阅；已存在时只更新 group_id/uid（保留 last_score/last_rank 避免重复推送）。
// 对齐 add_subscription。返回是否成功。
func (s *Store) Add(ctx context.Context, qqID, groupID, server string, eventID int, uid string) error {
	db, err := s.conn(ctx)
	if err != nil {
		return err
	}
	exists, err := s.Exists(ctx, qqID, server, eventID)
	if err != nil {
		return err
	}
	now := time.Now().Unix()
	if exists {
		_, err = db.ExecContext(ctx,
			"UPDATE subscriptions SET group_id = ?, uid = ? WHERE qq_id = ? AND server = ? AND event_id = ?",
			groupID, uid, qqID, server, eventID)
		return err
	}
	_, err = db.ExecContext(ctx, `
		INSERT INTO subscriptions
			(qq_id, group_id, server, event_id, uid, last_score, last_rank, last_check_time, created_at)
		VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)`,
		qqID, groupID, server, eventID, uid, now, now)
	return err
}

// Remove 取消订阅，返回是否确有删除。对齐 remove_subscription。
func (s *Store) Remove(ctx context.Context, qqID, server string, eventID int) (bool, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return false, err
	}
	res, err := db.ExecContext(ctx,
		"DELETE FROM subscriptions WHERE qq_id = ? AND server = ? AND event_id = ?", qqID, server, eventID)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	return n > 0, nil
}

// ClearAll 清空全部订阅，返回删除条数。对齐 clear_all_subscriptions。
func (s *Store) ClearAll(ctx context.Context) (int, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return 0, err
	}
	res, err := db.ExecContext(ctx, "DELETE FROM subscriptions")
	if err != nil {
		return 0, err
	}
	n, _ := res.RowsAffected()
	return int(n), nil
}
