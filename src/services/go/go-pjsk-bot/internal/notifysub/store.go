// Package notifysub 读写新曲/虚拟Live 群订阅 sqlite 库，对齐 old-python
// subscribe._sub_sql 的 pjsk_notify_subscriptions 表。
//
// 与 Python 侧共享同一个 sqlite 文件 ondemand/database/notify_subscription.db，
// 使用纯 Go 的 modernc.org/sqlite（无需 cgo）。订阅开关为读写操作。
package notifysub

import (
	"context"
	"database/sql"
	"os"
	"path/filepath"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

// 订阅类型，对齐 KIND_MUSIC / KIND_VLIVE。
const (
	KindMusic = "music"
	KindVLive = "vlive"
)

// Subscription 是一条订阅记录。QQID 为空字符串表示群级推送订阅（qq_id IS NULL）。
type Subscription struct {
	GroupID   string
	QQID      string // 空 = 群订阅
	Server    string
	Kind      string
	CreatedAt time.Time
}

// Store 打开并操作订阅库。
type Store struct {
	path string
	mu   sync.Mutex
	db   *sql.DB
}

// New 创建 Store。dataDir 为 data/pjsk。
func New(dataDir string) *Store {
	return &Store{path: filepath.Join(dataDir, "ondemand", "database", "notify_subscription.db")}
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
	db, err := sql.Open("sqlite", "file:"+s.path+"?_pragma=busy_timeout(5000)")
	if err != nil {
		return nil, err
	}
	if _, err := db.ExecContext(ctx, `
		CREATE TABLE IF NOT EXISTS pjsk_notify_subscriptions (
			id         INTEGER PRIMARY KEY AUTOINCREMENT,
			group_id   TEXT NOT NULL,
			qq_id      TEXT,
			server     TEXT NOT NULL,
			kind       TEXT NOT NULL,
			created_at INTEGER NOT NULL,
			UNIQUE(group_id, qq_id, server, kind)
		)`); err != nil {
		db.Close()
		return nil, err
	}
	if _, err := db.ExecContext(ctx, `
		CREATE INDEX IF NOT EXISTS idx_notify_sub_kind_server
		ON pjsk_notify_subscriptions (kind, server)`); err != nil {
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

// nullQQ 把空字符串映射为 SQL NULL，对齐 Python 的 qq_id IS ? 语义。
func nullQQ(qq string) any {
	if qq == "" {
		return nil
	}
	return qq
}

// Add 添加订阅；已存在返回 false。qq 为空表示群订阅。对齐 add_notify_sub。
func (s *Store) Add(ctx context.Context, groupID, qq, server, kind string) (bool, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return false, err
	}
	var id int64
	row := db.QueryRowContext(ctx,
		`SELECT id FROM pjsk_notify_subscriptions WHERE group_id = ? AND qq_id IS ? AND server = ? AND kind = ?`,
		groupID, nullQQ(qq), server, kind)
	if err := row.Scan(&id); err == nil {
		return false, nil // 已存在
	} else if err != sql.ErrNoRows {
		return false, err
	}
	if _, err := db.ExecContext(ctx,
		`INSERT INTO pjsk_notify_subscriptions (group_id, qq_id, server, kind, created_at) VALUES (?, ?, ?, ?, ?)`,
		groupID, nullQQ(qq), server, kind, time.Now().Unix()); err != nil {
		return false, err
	}
	return true, nil
}

// Remove 删除一条订阅，返回是否确有删除。对齐 remove_notify_sub。
func (s *Store) Remove(ctx context.Context, groupID, qq, server, kind string) (bool, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return false, err
	}
	res, err := db.ExecContext(ctx,
		`DELETE FROM pjsk_notify_subscriptions WHERE group_id = ? AND qq_id IS ? AND server = ? AND kind = ?`,
		groupID, nullQQ(qq), server, kind)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	return n > 0, nil
}

// RemoveGroup 关闭群订阅并连带清理该群该类型全部个人提醒，返回删除条数。
// 对齐 remove_group_subs。
func (s *Store) RemoveGroup(ctx context.Context, groupID, server, kind string) (int, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return 0, err
	}
	res, err := db.ExecContext(ctx,
		`DELETE FROM pjsk_notify_subscriptions WHERE group_id = ? AND server = ? AND kind = ?`,
		groupID, server, kind)
	if err != nil {
		return 0, err
	}
	n, _ := res.RowsAffected()
	return int(n), nil
}

// IsGroupSubbed 判断某群某类型是否已开启群订阅（qq_id IS NULL）。对齐 is_group_subbed。
func (s *Store) IsGroupSubbed(ctx context.Context, kind, server, groupID string) (bool, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return false, err
	}
	var one int
	row := db.QueryRowContext(ctx,
		`SELECT 1 FROM pjsk_notify_subscriptions WHERE kind = ? AND server = ? AND group_id = ? AND qq_id IS NULL`,
		kind, server, groupID)
	if err := row.Scan(&one); err == sql.ErrNoRows {
		return false, nil
	} else if err != nil {
		return false, err
	}
	return true, nil
}

// GroupStatus 获取本群全部订阅记录（含个人），按 kind/server/qq_id 排序。
// 对齐 get_group_sub_status。
func (s *Store) GroupStatus(ctx context.Context, groupID string) ([]Subscription, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return nil, err
	}
	rows, err := db.QueryContext(ctx,
		`SELECT group_id, qq_id, server, kind, created_at FROM pjsk_notify_subscriptions
		 WHERE group_id = ? ORDER BY kind, server, qq_id`, groupID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Subscription
	for rows.Next() {
		var sub Subscription
		var qq sql.NullString
		var ts int64
		if err := rows.Scan(&sub.GroupID, &qq, &sub.Server, &sub.Kind, &ts); err != nil {
			return nil, err
		}
		if qq.Valid {
			sub.QQID = qq.String
		}
		sub.CreatedAt = time.Unix(ts, 0)
		out = append(out, sub)
	}
	return out, rows.Err()
}
