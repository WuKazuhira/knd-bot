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

const (
	KindMusic   = "music"
	KindVLive   = "vlive"
	KindNewCard = "new_card"
	KindCostume = "new_costume"
)

type Subscription struct {
	ID        int64
	GroupID   string
	QQID      string // 空表示群订阅
	Server    string
	Kind      string
	CreatedAt time.Time
}

type DeliveryState struct {
	Kind           string
	Server         string
	NotificationID string
	LastSentAt     time.Time
}

type Store struct {
	path string
	mu   sync.Mutex
	db   *sql.DB
}

func New(dataDir string) *Store {
	return &Store{path: filepath.Join(dataDir, "ondemand", "database", "notify_subscription.db")}
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
	// 兼容缺 created_at 列的老库
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
	if err := ensureNewCardTables(ctx, db); err != nil {
		db.Close()
		return nil, err
	}
	if err := newsTables(ctx, db); err != nil {
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

func (s *Store) Close() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.db != nil {
		s.db.Close()
		s.db = nil
	}
}

func nullQQ(qq string) any {
	if qq == "" {
		return nil
	}
	return qq
}

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
		return false, nil
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

func (s *Store) ListAll(ctx context.Context) ([]Subscription, error) {
	return s.List(ctx, "", "")
}

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

func (s *Store) MarkSent(ctx context.Context, kind, server, notificationID string, sentAt time.Time) error {
	return s.MarkSentBatch(ctx, kind, server, []string{notificationID}, sentAt)
}

func (s *Store) MarkSentBatch(ctx context.Context, kind, server string, notificationIDs []string, sentAt time.Time) error {
	if len(notificationIDs) == 0 {
		return nil
	}
	if sentAt.IsZero() {
		sentAt = time.Now()
	}
	db, err := s.conn(ctx)
	if err != nil {
		return err
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	for _, notificationID := range notificationIDs {
		if notificationID == "" {
			continue
		}
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO pjsk_notify_delivery_state (kind, server, notification_id, last_sent_at)
			VALUES (?, ?, ?, ?)
			ON CONFLICT(kind, server, notification_id) DO UPDATE SET
				last_sent_at = excluded.last_sent_at`,
			kind, server, notificationID, sentAt.Unix()); err != nil {
			return err
		}
	}
	return tx.Commit()
}

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
