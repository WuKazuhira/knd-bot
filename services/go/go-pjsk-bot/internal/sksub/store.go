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

type Subscription struct {
	ID            int64
	QQID          string
	GroupID       string
	Server        string
	EventID       int
	UID           string
	LastScore     int64
	LastRank      int
	LastCheckTime time.Time
	CreatedAt     time.Time
}

type Store struct {
	path string
	mu   sync.Mutex
	db   *sql.DB
}

func New(dataDir string) *Store {
	return &Store{path: filepath.Join(dataDir, "ondemand", "database", "sk_subscription.db")}
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
	// 兼容缺列的老库
	for _, column := range []struct {
		name, definition string
	}{
		{"group_id", "TEXT"},
		{"last_score", "INTEGER NOT NULL DEFAULT 0"},
		{"last_rank", "INTEGER NOT NULL DEFAULT 0"},
		{"last_check_time", "INTEGER"},
		{"created_at", "INTEGER NOT NULL DEFAULT 0"},
	} {
		if err := ensureColumn(ctx, db, "subscriptions", column.name, column.definition); err != nil {
			db.Close()
			return nil, err
		}
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

// 已存在只改群号和 uid，保留上次分数避免重推
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

func (s *Store) Get(ctx context.Context, qqID, server string, eventID int) (Subscription, bool, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return Subscription{}, false, err
	}
	row := db.QueryRowContext(ctx, `
		SELECT id, qq_id, group_id, server, event_id, uid,
		       last_score, last_rank, last_check_time, created_at
		FROM subscriptions WHERE qq_id = ? AND server = ? AND event_id = ?`,
		qqID, server, eventID)
	sub, err := scanSubscription(row)
	if err == sql.ErrNoRows {
		return Subscription{}, false, nil
	}
	if err != nil {
		return Subscription{}, false, err
	}
	return sub, true, nil
}

func (s *Store) List(ctx context.Context, server string, eventID int) ([]Subscription, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return nil, err
	}
	query := `SELECT id, qq_id, group_id, server, event_id, uid,
	                 last_score, last_rank, last_check_time, created_at
	          FROM subscriptions WHERE 1=1`
	args := make([]any, 0, 2)
	if server != "" {
		query += " AND server = ?"
		args = append(args, server)
	}
	if eventID != 0 {
		query += " AND event_id = ?"
		args = append(args, eventID)
	}
	query += " ORDER BY server, event_id, group_id, qq_id"
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

func (s *Store) UpdateStatus(ctx context.Context, id int64, score int64, rank int, checkedAt time.Time) (bool, error) {
	if checkedAt.IsZero() {
		checkedAt = time.Now()
	}
	db, err := s.conn(ctx)
	if err != nil {
		return false, err
	}
	res, err := db.ExecContext(ctx, `
		UPDATE subscriptions
		SET last_score = ?, last_rank = ?, last_check_time = ?
		WHERE id = ?`, score, rank, checkedAt.Unix(), id)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	return n > 0, nil
}

func (s *Store) UpdateStatuses(ctx context.Context, statuses []StatusUpdate, checkedAt time.Time) error {
	if len(statuses) == 0 {
		return nil
	}
	if checkedAt.IsZero() {
		checkedAt = time.Now()
	}
	db, err := s.conn(ctx)
	if err != nil {
		return err
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	for _, status := range statuses {
		if _, err := tx.ExecContext(ctx, `
			UPDATE subscriptions
			SET last_score = ?, last_rank = ?, last_check_time = ?
			WHERE id = ?`, status.Score, status.Rank, checkedAt.Unix(), status.ID); err != nil {
			_ = tx.Rollback()
			return err
		}
	}
	return tx.Commit()
}

type StatusUpdate struct {
	ID    int64
	Score int64
	Rank  int
}

func (s *Store) RemoveByID(ctx context.Context, id int64) (bool, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return false, err
	}
	res, err := db.ExecContext(ctx, "DELETE FROM subscriptions WHERE id = ?", id)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	return n > 0, nil
}

func (s *Store) RemoveByEvent(ctx context.Context, server string, eventID int) (int, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return 0, err
	}
	res, err := db.ExecContext(ctx, "DELETE FROM subscriptions WHERE server = ? AND event_id = ?", server, eventID)
	if err != nil {
		return 0, err
	}
	n, _ := res.RowsAffected()
	return int(n), nil
}

func scanSubscription(scanner interface{ Scan(...any) error }) (Subscription, error) {
	var sub Subscription
	var groupID sql.NullString
	var lastScore, lastCheck, created sql.NullInt64
	if err := scanner.Scan(
		&sub.ID, &sub.QQID, &groupID, &sub.Server, &sub.EventID, &sub.UID,
		&lastScore, &sub.LastRank, &lastCheck, &created,
	); err != nil {
		return Subscription{}, err
	}
	if groupID.Valid {
		sub.GroupID = groupID.String
	}
	if lastScore.Valid {
		sub.LastScore = lastScore.Int64
	}
	if lastCheck.Valid {
		sub.LastCheckTime = time.Unix(lastCheck.Int64, 0)
	}
	if created.Valid {
		sub.CreatedAt = time.Unix(created.Int64, 0)
	}
	return sub, nil
}
