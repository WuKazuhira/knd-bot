package notifysub

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"sort"
	"time"
)

// NewsObservation separates raw master IDs from stable logical outfit identities.
type NewsObservation struct {
	ID       int
	Key      string
	Payload  json.RawMessage
	Category string
	Identity string
}
type NewsPending struct {
	Key       string
	Payload   json.RawMessage
	CreatedAt time.Time
	Attempts  int
}

func newsTables(ctx context.Context, db *sql.DB) error {
	for _, q := range []string{
		`CREATE TABLE IF NOT EXISTS pjsk_news_batches (namespace TEXT NOT NULL, revision TEXT NOT NULL, payload BLOB NOT NULL, discovered_at INTEGER NOT NULL, PRIMARY KEY(namespace,revision))`,
		`CREATE TABLE IF NOT EXISTS pjsk_news_assoc (namespace TEXT NOT NULL, identity TEXT NOT NULL, logical_key TEXT NOT NULL, revision TEXT NOT NULL, PRIMARY KEY(namespace,identity))`,

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
	return s.ReconcileNewsBatch(ctx, ns, "", observations, now)
}
func (s *Store) ReconcileNewsBatch(ctx context.Context, ns, revision string, observations []NewsObservation, now time.Time) error {
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
	var cardMarker string
	cardErr := tx.QueryRowContext(ctx, `SELECT namespace FROM pjsk_news_baseline WHERE namespace=?`, ns+"/cards-v1").Scan(&cardMarker)
	cardFirst := cardErr == sql.ErrNoRows
	if cardErr != nil && !cardFirst {
		return cardErr
	}
	// Existing raw IDs retain their history, while old color-specific logical
	// keys are interpreted through the current canonical observations.
	canonical := map[int]string{}
	for _, o := range observations {
		canonical[o.ID] = o.Key
	}
	keys = map[string]bool{}
	for id := range seen {
		if key := canonical[id]; key != "" {
			keys[key] = true
		}
	}
	var discovered []json.RawMessage
	for _, o := range observations {
		if o.Identity != "" {
			var present string
			e := tx.QueryRowContext(ctx, `SELECT identity FROM pjsk_news_assoc WHERE namespace=? AND identity=?`, ns, o.Identity).Scan(&present)
			if e != nil && e != sql.ErrNoRows {
				return e
			}
			if e == sql.ErrNoRows {
				if !first && !cardFirst && !keys[o.Key] {
					discovered = append(discovered, o.Payload)
					keys[o.Key] = true
					if _, err = tx.ExecContext(ctx, `INSERT OR IGNORE INTO pjsk_news_pending(namespace,logical_key,payload,created_at) VALUES(?,?,?,?)`, ns, o.Key, []byte(o.Payload), now.Unix()); err != nil {
						return err
					}
				}
				if _, err = tx.ExecContext(ctx, `INSERT INTO pjsk_news_assoc(namespace,identity,logical_key,revision) VALUES(?,?,?,?)`, ns, o.Identity, o.Key, revision); err != nil {
					return err
				}
			}
			continue
		}
		if o.ID <= 0 || o.Key == "" {
			return fmt.Errorf("invalid %s observation", ns)
		}
		if seen[o.ID] {
			continue
		}
		if !first && !(o.Category == "card" && cardFirst) && !keys[o.Key] {
			discovered = append(discovered, o.Payload)
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
	if _, err = tx.ExecContext(ctx, `INSERT OR IGNORE INTO pjsk_news_baseline(namespace) VALUES(?)`, ns+"/cards-v1"); err != nil {
		return err
	}
	if len(discovered) > 0 && revision != "" {
		byOutfit := map[string]json.RawMessage{}
		for _, raw := range discovered {
			var entry struct {
				Key    string `json:"key"`
				CardID int    `json:"cardId"`
			}
			if e := json.Unmarshal(raw, &entry); e != nil {
				return e
			}
			previous := byOutfit[entry.Key]
			if previous == nil || entry.CardID > 0 {
				byOutfit[entry.Key] = raw
			}
		}
		outfitKeys := make([]string, 0, len(byOutfit))
		for key := range byOutfit {
			outfitKeys = append(outfitKeys, key)
		}
		sort.Strings(outfitKeys)
		discovered = nil
		for _, key := range outfitKeys {
			discovered = append(discovered, byOutfit[key])
		}
		payload, e := json.Marshal(struct {
			Revision string            `json:"revision"`
			Outfits  []json.RawMessage `json:"outfits"`
		}{revision, discovered})
		if e != nil {
			return e
		}
		if _, err = tx.ExecContext(ctx, `INSERT OR REPLACE INTO pjsk_news_batches(namespace,revision,payload,discovered_at) VALUES(?,?,?,?)`, ns, revision, payload, now.Unix()); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// SaveNewsBatch records the latest discovered masterdata delta for manual lookup.
func (s *Store) SaveNewsBatch(ctx context.Context, ns, revision string, payload json.RawMessage, at time.Time) error {
	db, err := s.conn(ctx)
	if err != nil {
		return err
	}
	if err = newsTables(ctx, db); err != nil {
		return err
	}
	_, err = db.ExecContext(ctx, `INSERT OR REPLACE INTO pjsk_news_batches(namespace,revision,payload,discovered_at) VALUES(?,?,?,?)`, ns, revision, []byte(payload), at.Unix())
	return err
}
func (s *Store) LatestNewsBatch(ctx context.Context, ns string) (json.RawMessage, time.Time, error) {
	db, err := s.conn(ctx)
	if err != nil {
		return nil, time.Time{}, err
	}
	if err = newsTables(ctx, db); err != nil {
		return nil, time.Time{}, err
	}
	var payload []byte
	var at int64
	err = db.QueryRowContext(ctx, `SELECT payload,discovered_at FROM pjsk_news_batches WHERE namespace=? ORDER BY discovered_at DESC,rowid DESC LIMIT 1`, ns).Scan(&payload, &at)
	if err == sql.ErrNoRows {
		return nil, time.Time{}, nil
	}
	return payload, time.Unix(at, 0), err
}

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

// SaveDiscoveredOutfit updates selected models in durable revision history.
func (s *Store) SaveDiscoveredOutfit(ctx context.Context, ns, key string, payload json.RawMessage) error {
	db, err := s.conn(ctx)
	if err != nil {
		return err
	}
	rows, err := db.QueryContext(ctx, `SELECT revision,payload FROM pjsk_news_batches WHERE namespace=?`, ns)
	if err != nil {
		return err
	}
	type update struct {
		revision string
		payload  []byte
	}
	var updates []update
	for rows.Next() {
		var revision string
		var raw []byte
		if err = rows.Scan(&revision, &raw); err != nil {
			rows.Close()
			return err
		}
		var batch struct {
			Revision string            `json:"revision"`
			Outfits  []json.RawMessage `json:"outfits"`
		}
		if err = json.Unmarshal(raw, &batch); err != nil {
			rows.Close()
			return err
		}
		changed := false
		for i, out := range batch.Outfits {
			var current struct {
				Key string `json:"key"`
			}
			if err = json.Unmarshal(out, &current); err != nil {
				rows.Close()
				return err
			}
			if current.Key == key {
				batch.Outfits[i] = payload
				changed = true
			}
		}
		if changed {
			encoded, e := json.Marshal(batch)
			if e != nil {
				rows.Close()
				return e
			}
			updates = append(updates, update{revision, encoded})
		}
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	for _, u := range updates {
		if _, err = db.ExecContext(ctx, `UPDATE pjsk_news_batches SET payload=? WHERE namespace=? AND revision=?`, u.payload, ns, u.revision); err != nil {
			return err
		}
	}
	return nil
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
