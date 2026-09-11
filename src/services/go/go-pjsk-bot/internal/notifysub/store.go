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
	ID        int64
	GroupID   string
	QQID      string // 空 = 群订阅
	Server    string
	Kind      string
	CreatedAt time.Time
}

// DeliveryState 记录某类通知对某目标的最近成功发送状态。
// NotificationID 由 Worker 组合为“源事件 ID/群号”，避免同一事件重复发送。
type DeliveryState struct {
	Kind           string
	Server         string
	NotificationID string
	LastSentAt     time.Time
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
	db, err := sql.Open("sqlite", "file:"+s.path+"?_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)")
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
	// 旧 Python 表可能没有 created_at；只补缺列，不改变已有数据。
	if err := ensureColumn(ctx, db, "pjsk_notify_subscriptions", "created_at", "INTEGER NOT NULL DEFAULT 0"); err != nil {
		db.Close()
		return nil, err
	}
	if _, err := db.ExecContext(ctx, `
		CREATE INDEX IF NOT EXISTS idx_notify_sub_kind_server
		ON pjsk_notify_subscriptions (kind, server)`); err != nil {
		db.Close()
		return nil, err
	}
	// 状态单独存表，不改动 Python 已使用的订阅表结构。
	if _, err := db.ExecContext(ctx, `
		CREATE TABLE IF NOT EXISTS pjsk_notify_delivery_state (
			kind            TEXT NOT NULL,
			server          TEXT NOT NULL,
			notification_id TEXT NOT NULL,
			last_sent_at    INTEGER NOT NULL,
			PRIMARY KEY(kind, server, notification_id)
		)`); err != nil {
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
		var cid int
		var name, typ string
		var notNull, pk int
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
		`SELECT id, group_id, qq_id, server, kind, created_at FROM pjsk_notify_subscriptions
		 WHERE group_id = ? ORDER BY kind, server, qq_id`, groupID)
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

// List 返回指定 kind/server 的全部订阅（群订阅和个人提醒均包含）。
// kind 或 server 为空时不作为过滤条件，便于后台轮询一次遍历所有记录。
func (s *Store) List(ctx context.Context, kind, server string) ([]Subscription, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return nil, err
	}
	query := `SELECT id, group_id, qq_id, server, kind, created_at FROM pjsk_notify_subscriptions WHERE 1=1`
	args := make([]any, 0, 2)
	if kind != "" {
		query += " AND kind = ?"
		args = append(args, kind)
	}
	if server != "" {
		query += " AND server = ?"
		args = append(args, server)
	}
	query += " ORDER BY kind, server, group_id, qq_id"
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

// ListAll 返回所有新曲/虚拟 Live 订阅。
func (s *Store) ListAll(ctx context.Context) ([]Subscription, error) {
	return s.List(ctx, "", "")
}

// ListGroups 只返回群级订阅（qq_id IS NULL），用于决定实际推送目标。
func (s *Store) ListGroups(ctx context.Context, kind, server string) ([]Subscription, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return nil, err
	}
	rows, err := db.QueryContext(ctx,
		`SELECT id, group_id, qq_id, server, kind, created_at
		 FROM pjsk_notify_subscriptions
		 WHERE qq_id IS NULL AND (? = '' OR kind = ?) AND (? = '' OR server = ?)
		 ORDER BY kind, server, group_id`, kind, kind, server, server)
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

// Get 获取一条精确订阅。
func (s *Store) Get(ctx context.Context, groupID, qq, server, kind string) (Subscription, bool, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return Subscription{}, false, err
	}
	row := db.QueryRowContext(ctx,
		`SELECT id, group_id, qq_id, server, kind, created_at
		 FROM pjsk_notify_subscriptions
		 WHERE group_id = ? AND qq_id IS ? AND server = ? AND kind = ?`,
		groupID, nullQQ(qq), server, kind)
	sub, err := scanSubscription(row)
	if err == sql.ErrNoRows {
		return Subscription{}, false, nil
	}
	if err != nil {
		return Subscription{}, false, err
	}
	return sub, true, nil
}

func scanSubscription(scanner interface{ Scan(...any) error }) (Subscription, error) {
	var sub Subscription
	var qq sql.NullString
	var ts sql.NullInt64
	if err := scanner.Scan(&sub.ID, &sub.GroupID, &qq, &sub.Server, &sub.Kind, &ts); err != nil {
		return Subscription{}, err
	}
	if qq.Valid {
		sub.QQID = qq.String
	}
	if ts.Valid {
		sub.CreatedAt = time.Unix(ts.Int64, 0)
	}
	return sub, nil
}

// WasSent 判断某通知是否已经成功发送。
func (s *Store) WasSent(ctx context.Context, kind, server, notificationID string) (bool, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return false, err
	}
	var one int
	err = db.QueryRowContext(ctx, `
		SELECT 1 FROM pjsk_notify_delivery_state
		WHERE kind = ? AND server = ? AND notification_id = ?`,
		kind, server, notificationID).Scan(&one)
	if err == sql.ErrNoRows {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return true, nil
}

// MarkSent 写入某通知的成功发送状态；重复写入只更新时间。
func (s *Store) MarkSent(ctx context.Context, kind, server, notificationID string, sentAt time.Time) error {
	if sentAt.IsZero() {
		sentAt = time.Now()
	}
	db, err := s.conn(ctx)
	if err != nil {
		return err
	}
	_, err = db.ExecContext(ctx, `
		INSERT INTO pjsk_notify_delivery_state (kind, server, notification_id, last_sent_at)
		VALUES (?, ?, ?, ?)
		ON CONFLICT(kind, server, notification_id) DO UPDATE SET
			last_sent_at = excluded.last_sent_at`,
		kind, server, notificationID, sentAt.Unix())
	return err
}

// ClearSent 清除某通知的去重状态，返回是否确有删除。
func (s *Store) ClearSent(ctx context.Context, kind, server, notificationID string) (bool, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return false, err
	}
	res, err := db.ExecContext(ctx, `
		DELETE FROM pjsk_notify_delivery_state
		WHERE kind = ? AND server = ? AND notification_id = ?`, kind, server, notificationID)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	return n > 0, nil
}

// ListSent 返回发送状态，可按 kind/server 过滤。
func (s *Store) ListSent(ctx context.Context, kind, server string) ([]DeliveryState, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return nil, err
	}
	query := `SELECT kind, server, notification_id, last_sent_at FROM pjsk_notify_delivery_state WHERE 1=1`
	args := make([]any, 0, 2)
	if kind != "" {
		query += " AND kind = ?"
		args = append(args, kind)
	}
	if server != "" {
		query += " AND server = ?"
		args = append(args, server)
	}
	query += " ORDER BY kind, server, notification_id"
	rows, err := db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []DeliveryState
	for rows.Next() {
		var state DeliveryState
		var sentAt sql.NullInt64
		if err := rows.Scan(&state.Kind, &state.Server, &state.NotificationID, &sentAt); err != nil {
			return nil, err
		}
		if sentAt.Valid {
			state.LastSentAt = time.Unix(sentAt.Int64, 0)
		}
		out = append(out, state)
	}
	return out, rows.Err()
}
