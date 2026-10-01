package notifysub

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"time"
)

// NewsObservation separates raw master IDs from stable logical outfit identities.
type NewsObservation struct {
	ID      int
	Key     string
	Payload json.RawMessage
}
type NewsPending struct {
	Key       string
	Payload   json.RawMessage
	CreatedAt time.Time
	Attempts  int
}

func newsTables(ctx context.Context, db *sql.DB) error {
	for _, q := range []string{
		`CREATE TABLE IF NOT EXISTS pjsk_news_baseline (namespace TEXT PRIMARY KEY)`,
		`CREATE TABLE IF NOT EXISTS pjsk_news_seen (namespace TEXT NOT NULL, raw_id INTEGER NOT NULL, logical_key TEXT NOT NULL, PRIMARY KEY(namespace,raw_id))`,
		`CREATE TABLE IF NOT EXISTS pjsk_news_pending (namespace TEXT NOT NULL, logical_key TEXT NOT NULL, payload BLOB NOT NULL, created_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, retry_at INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '', PRIMARY KEY(namespace,logical_key))`,
	} {
		if _, err := db.ExecContext(ctx, q); err != nil {
			return err
		}
	}
	return nil
}

// ReconcileNews atomically baselines all raw IDs and queues only new logical identities.
func (s *Store) ReconcileNews(ctx context.Context, ns string, observations []NewsObservation, now time.Time) error {
	if len(observations) == 0 {
		return fmt.Errorf("empty %s snapshot", ns)
	}
	db, err := s.conn(ctx)
	if err != nil {
		return err
	}
	if err = newsTables(ctx, db); err != nil {
		return err
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var marker string
	err = tx.QueryRowContext(ctx, `SELECT namespace FROM pjsk_news_baseline WHERE namespace=?`, ns).Scan(&marker)
	first := err == sql.ErrNoRows
	if err != nil && !first {
		return err
	}
	rows, err := tx.QueryContext(ctx, `SELECT raw_id,logical_key FROM pjsk_news_seen WHERE namespace=?`, ns)
	if err != nil {
		return err
	}
	seen := map[int]bool{}
	keys := map[string]bool{}
	for rows.Next() {
		var id int
		var key string
		if err = rows.Scan(&id, &key); err != nil {
			rows.Close()
			return err
		}
		seen[id] = true
		keys[key] = true
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	for _, o := range observations {
		if o.ID <= 0 || o.Key == "" {
			return fmt.Errorf("invalid %s observation", ns)
		}
		if seen[o.ID] {
			continue
		}
		if !first && !keys[o.Key] {
			if _, err = tx.ExecContext(ctx, `INSERT OR IGNORE INTO pjsk_news_pending(namespace,logical_key,payload,created_at) VALUES(?,?,?,?)`, ns, o.Key, []byte(o.Payload), now.Unix()); err != nil {
				return err
			}
		}
		if _, err = tx.ExecContext(ctx, `INSERT INTO pjsk_news_seen(namespace,raw_id,logical_key) VALUES(?,?,?)`, ns, o.ID, o.Key); err != nil {
			return err
		}
		keys[o.Key] = true
		seen[o.ID] = true
	}
	if _, err = tx.ExecContext(ctx, `INSERT OR IGNORE INTO pjsk_news_baseline(namespace) VALUES(?)`, ns); err != nil {
		return err
	}
	return tx.Commit()
}

// EnqueueNews persists independently retriable card costume supplements.
func (s *Store) EnqueueNews(ctx context.Context, ns, key string, payload json.RawMessage, at time.Time) error {
	db, err := s.conn(ctx)
	if err != nil {
		return err
	}
	if err = newsTables(ctx, db); err != nil {
		return err
	}
	_, err = db.ExecContext(ctx, `INSERT OR IGNORE INTO pjsk_news_pending(namespace,logical_key,payload,created_at) VALUES(?,?,?,?)`, ns, key, []byte(payload), at.Unix())
	return err
}
func (s *Store) ListNews(ctx context.Context, ns string, now time.Time) ([]NewsPending, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return nil, err
	}
	if err = newsTables(ctx, db); err != nil {
		return nil, err
	}
	rows, err := db.QueryContext(ctx, `SELECT logical_key,payload,created_at,attempts FROM pjsk_news_pending WHERE namespace=? AND retry_at<=? ORDER BY created_at,logical_key`, ns, now.Unix())
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []NewsPending{}
	for rows.Next() {
		var p NewsPending
		var at int64
		if err = rows.Scan(&p.Key, &p.Payload, &at, &p.Attempts); err != nil {
			return nil, err
		}
		p.CreatedAt = time.Unix(at, 0)
		out = append(out, p)
	}
	return out, rows.Err()
}
func (s *Store) SaveNewsPayload(ctx context.Context, ns, key string, payload json.RawMessage) error {
	db, err := s.conn(ctx)
	if err != nil {
		return err
	}
	_, err = db.ExecContext(ctx, `UPDATE pjsk_news_pending SET payload=? WHERE namespace=? AND logical_key=?`, []byte(payload), ns, key)
	return err
}
func (s *Store) RetryNews(ctx context.Context, ns, key string, attempts int, at time.Time, cause error) error {
	db, err := s.conn(ctx)
	if err != nil {
		return err
	}
	message := ""
	if cause != nil {
		message = cause.Error()
	}
	_, err = db.ExecContext(ctx, `UPDATE pjsk_news_pending SET attempts=?,retry_at=?,last_error=? WHERE namespace=? AND logical_key=?`, attempts, at.Unix(), message, ns, key)
	return err
}
func (s *Store) AckNews(ctx context.Context, ns, key string) error {
	db, err := s.conn(ctx)
	if err != nil {
		return err
	}
	_, err = db.ExecContext(ctx, `DELETE FROM pjsk_news_pending WHERE namespace=? AND logical_key=?`, ns, key)
	return err
}
