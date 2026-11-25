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

type Store struct {
	path string
	mu   sync.Mutex
	db   *sql.DB
}

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

func (s *Store) Close() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.db != nil {
		s.db.Close()
		s.db = nil
	}
}

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
