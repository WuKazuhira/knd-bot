// Package msrsub 读写 MySekai 数据更新自动推送订阅 sqlite 库，对齐 old-python
// mysekai._subscription 的 msr_subscriptions 表。
//
// 与 Python 侧共享同一个 sqlite 文件 ondemand/database/mysekai_msr_subscription.db，
// 使用纯 Go 的 modernc.org/sqlite。订阅增删与状态回写由 Go 处理；数据更新的
// 抓取由 subscription.NotifyWorker 通过可注入 Source 提供，未接入时不会伪造推送。
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

// Subscription 是一条 MSR 后台推送订阅。
type Subscription struct {
	ID           int64
	QQID         string
	GroupID      string
	Server       string
	UID          string
	Mode         string
	LastPushTime time.Time
	CreatedAt    time.Time
}

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
	db, err := sql.Open("sqlite", "file:"+s.path+"?_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)")
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
	for _, column := range []struct {
		name, definition string
	}{
		{"mode", "TEXT NOT NULL DEFAULT 'latest'"},
		{"last_push_time", "INTEGER NOT NULL DEFAULT 0"},
		{"created_at", "INTEGER NOT NULL DEFAULT 0"},
	} {
		if err := ensureColumn(ctx, db, "msr_subscriptions", column.name, column.definition); err != nil {
			db.Close()
			return nil, err
		}
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

func ensureColumn(ctx context.Context, db *sql.DB, table, column, definition string) error {
	rows, err := db.QueryContext(ctx, "PRAGMA table_info("+table+")")
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var cid, notNull, pk int
		var name, typ string
		var defaultValue sql.NullString
		if err := rows.Scan(&cid, &name, &typ, &notNull, &defaultValue, &pk); err != nil {
			return err
		}
		if name == column {
			return rows.Err()
		}
	}
	if err := rows.Err(); err != nil {
		return err
	}
	_, err = db.ExecContext(ctx, "ALTER TABLE "+table+" ADD COLUMN "+column+" "+definition)
	return err
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

// Get 获取一条指定 QQ/服务器的 MSR 订阅。
func (s *Store) Get(ctx context.Context, qqID, server string) (Subscription, bool, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return Subscription{}, false, err
	}
	row := db.QueryRowContext(ctx, `
		SELECT id, qq_id, group_id, server, uid, mode, last_push_time, created_at
		FROM msr_subscriptions WHERE qq_id = ? AND server = ?`, qqID, server)
	sub, err := scanSubscription(row)
	if err == sql.ErrNoRows {
		return Subscription{}, false, nil
	}
	if err != nil {
		return Subscription{}, false, err
	}
	return sub, true, nil
}

// List 返回 MSR 订阅；server 为空时返回所有服务器。
func (s *Store) List(ctx context.Context, server string) ([]Subscription, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return nil, err
	}
	query := `SELECT id, qq_id, group_id, server, uid, mode, last_push_time, created_at FROM msr_subscriptions`
	args := []any(nil)
	if server != "" {
		query += " WHERE server = ?"
		args = []any{server}
	}
	query += " ORDER BY server, group_id, qq_id"
	rows, err := db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Subscription
	for rows.Next() {
		sub, err := scanSubscription(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, sub)
	}
	return out, rows.Err()
}

// UpdateLastPush 更新某条订阅的最近推送时间。
func (s *Store) UpdateLastPush(ctx context.Context, id int64, timestamp time.Time) (bool, error) {
	if timestamp.IsZero() {
		timestamp = time.Now()
	}
	db, err := s.conn(ctx)
	if err != nil {
		return false, err
	}
	res, err := db.ExecContext(ctx, `UPDATE msr_subscriptions SET last_push_time = ? WHERE id = ?`, timestamp.Unix(), id)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	return n > 0, nil
}

// UpdateLastPushNow 是 UpdateLastPush 的当前时间便捷形式。
func (s *Store) UpdateLastPushNow(ctx context.Context, id int64) (bool, error) {
	return s.UpdateLastPush(ctx, id, time.Now())
}

func scanSubscription(scanner interface{ Scan(...any) error }) (Subscription, error) {
	var sub Subscription
	var lastPush, created sql.NullInt64
	var mode sql.NullString
	if err := scanner.Scan(&sub.ID, &sub.QQID, &sub.GroupID, &sub.Server, &sub.UID, &mode, &lastPush, &created); err != nil {
		return Subscription{}, err
	}
	if mode.Valid && mode.String != "" {
		sub.Mode = mode.String
	} else {
		sub.Mode = "latest"
	}
	if lastPush.Valid {
		sub.LastPushTime = time.Unix(lastPush.Int64, 0)
	}
	if created.Valid {
		sub.CreatedAt = time.Unix(created.Int64, 0)
	}
	return sub, nil
}
