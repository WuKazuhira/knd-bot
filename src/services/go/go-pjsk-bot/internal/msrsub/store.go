// Package msrsub 读写 MySekai 数据更新自动推送订阅 sqlite 库，对齐 old-python
// mysekai._subscription 的 msr_subscriptions 表。
//
// 与 Python 侧共享同一个 sqlite 文件 ondemand/database/mysekai_msr_subscription.db，
// 使用纯 Go 的 modernc.org/sqlite。订阅增删由 Go 处理；数据更新的定时检测与推送
// 仍由 Python 承担（读同一张表）。
package msrsub

import (
	"context"
	"database/sql"
	"os"
	"path/filepath"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

// Store 打开并操作 MSR 订阅库。
type Store struct {
	path string
	mu   sync.Mutex
	db   *sql.DB
}

// New 创建 Store。dataDir 为 data/pjsk。
func New(dataDir string) *Store {
	return &Store{path: filepath.Join(dataDir, "ondemand", "database", "mysekai_msr_subscription.db")}
}

func (s *Store) conn(ctx context.Context) (*sql.DB, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.db != nil {
		return s.db, nil
	}
	if err := os.MkdirAll(filepath.Dir(s.path), 0o755); err != nil {
		return nil, err
	}
	db, err := sql.Open("sqlite", "file:"+s.path+"?_pragma=busy_timeout(5000)")
	if err != nil {
		return nil, err
	}
	if _, err := db.ExecContext(ctx, `
		CREATE TABLE IF NOT EXISTS msr_subscriptions (
			id              INTEGER PRIMARY KEY AUTOINCREMENT,
			qq_id           TEXT    NOT NULL,
			group_id        TEXT    NOT NULL,
			server          TEXT    NOT NULL,
			uid             TEXT    NOT NULL,
			mode            TEXT    NOT NULL DEFAULT 'latest',
			last_push_time  INTEGER NOT NULL DEFAULT 0,
			created_at      INTEGER NOT NULL,
			UNIQUE(qq_id, server)
		)`); err != nil {
		db.Close()
		return nil, err
	}
	if _, err := db.ExecContext(ctx, `
		CREATE INDEX IF NOT EXISTS idx_msr_subscriptions_server
		ON msr_subscriptions (server)`); err != nil {
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

// Add 添加/更新订阅（qq_id+server 冲突时更新 group_id/uid/mode）。对齐 add_msr_subscription。
func (s *Store) Add(ctx context.Context, qqID, groupID, server, uid, mode string) error {
	db, err := s.conn(ctx)
	if err != nil {
		return err
	}
	if mode == "" {
		mode = "latest"
	}
	_, err = db.ExecContext(ctx, `
		INSERT INTO msr_subscriptions (qq_id, group_id, server, uid, mode, last_push_time, created_at)
		VALUES (?, ?, ?, ?, ?, 0, ?)
		ON CONFLICT(qq_id, server) DO UPDATE SET
			group_id = excluded.group_id,
			uid = excluded.uid,
			mode = excluded.mode`,
		qqID, groupID, server, uid, mode, time.Now().Unix())
	return err
}

// Remove 取消订阅，返回是否确有删除。对齐 remove_msr_subscription。
func (s *Store) Remove(ctx context.Context, qqID, server string) (bool, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return false, err
	}
	res, err := db.ExecContext(ctx,
		"DELETE FROM msr_subscriptions WHERE qq_id = ? AND server = ?", qqID, server)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	return n > 0, nil
}
