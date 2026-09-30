package notifysub

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"
)

// NewCardObservation 是一次已提交本地快照中的卡牌及其活动归属。
type NewCardObservation struct {
	CardID  int
	EventID int
}

// NewCardBatch 是等待构建和发送的新卡稳定批次。
type NewCardBatch struct {
	ID           string
	Revision     string
	EventID      int
	CardIDs      []int
	DiscoveredAt time.Time
	Attempts     int
	NextRetryAt  time.Time
	LastError    string
}

func ensureNewCardTables(ctx context.Context, db *sql.DB) error {
	statements := []string{
		`CREATE TABLE IF NOT EXISTS pjsk_new_card_snapshot_state (
			server       TEXT PRIMARY KEY,
			revision     TEXT NOT NULL,
			initialized_at INTEGER NOT NULL,
			updated_at   INTEGER NOT NULL
		)`,
		`CREATE TABLE IF NOT EXISTS pjsk_new_card_seen (
			server       TEXT NOT NULL,
			card_id      INTEGER NOT NULL,
			first_revision TEXT NOT NULL,
			first_seen_at INTEGER NOT NULL,
			PRIMARY KEY(server, card_id)
		)`,
		`CREATE TABLE IF NOT EXISTS pjsk_new_card_pending (
			server        TEXT NOT NULL,
			batch_id      TEXT NOT NULL,
			revision      TEXT NOT NULL,
			event_id      INTEGER NOT NULL DEFAULT 0,
			card_ids_json TEXT NOT NULL,
			discovered_at INTEGER NOT NULL,
			attempts      INTEGER NOT NULL DEFAULT 0,
			next_retry_at INTEGER NOT NULL DEFAULT 0,
			last_error    TEXT NOT NULL DEFAULT '',
			PRIMARY KEY(server, batch_id)
		)`,
		`CREATE INDEX IF NOT EXISTS idx_new_card_pending_ready
		 ON pjsk_new_card_pending (server, next_retry_at, discovered_at)`,
	}
	for _, statement := range statements {
		if _, err := db.ExecContext(ctx, statement); err != nil {
			return err
		}
	}
	return nil
}

// ReconcileNewCardSnapshot 原子地记录快照中的全部卡牌。首次调用只建立基线；
// 后续调用把真正新增的 ID 按活动分组，非活动卡组成独立稳定批次。
func (s *Store) ReconcileNewCardSnapshot(ctx context.Context, server, revision string, observations []NewCardObservation, observedAt time.Time) (bool, int, error) {
	if observedAt.IsZero() {
		observedAt = time.Now()
	}
	server = strings.TrimSpace(server)
	revision = strings.TrimSpace(revision)
	if server == "" || revision == "" {
		return false, 0, fmt.Errorf("server and revision are required")
	}
	observations = normalizeNewCardObservations(observations)
	if len(observations) == 0 {
		return false, 0, fmt.Errorf("new card snapshot contains no cards")
	}

	db, err := s.conn(ctx)
	if err != nil {
		return false, 0, err
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return false, 0, err
	}
	defer tx.Rollback()

	var currentRevision string
	err = tx.QueryRowContext(ctx, `SELECT revision FROM pjsk_new_card_snapshot_state WHERE server = ?`, server).Scan(&currentRevision)
	first := err == sql.ErrNoRows
	if err != nil && err != sql.ErrNoRows {
		return false, 0, err
	}
	if first {
		for _, observation := range observations {
			if _, err := tx.ExecContext(ctx, `
				INSERT OR IGNORE INTO pjsk_new_card_seen (server, card_id, first_revision, first_seen_at)
				VALUES (?, ?, ?, ?)`, server, observation.CardID, revision, observedAt.Unix()); err != nil {
				return false, 0, err
			}
		}
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO pjsk_new_card_snapshot_state (server, revision, initialized_at, updated_at)
			VALUES (?, ?, ?, ?)`, server, revision, observedAt.Unix(), observedAt.Unix()); err != nil {
			return false, 0, err
		}
		if err := tx.Commit(); err != nil {
			return false, 0, err
		}
		return true, 0, nil
	}

	rows, err := tx.QueryContext(ctx, `SELECT card_id FROM pjsk_new_card_seen WHERE server = ?`, server)
	if err != nil {
		return false, 0, err
	}
	seen := make(map[int]struct{})
	for rows.Next() {
		var cardID int
		if err := rows.Scan(&cardID); err != nil {
			rows.Close()
			return false, 0, err
		}
		seen[cardID] = struct{}{}
	}
	if err := rows.Close(); err != nil {
		return false, 0, err
	}

	grouped := make(map[int][]int)
	newCount := 0
	for _, observation := range observations {
		if _, ok := seen[observation.CardID]; ok {
			continue
		}
		newCount++
		grouped[observation.EventID] = append(grouped[observation.EventID], observation.CardID)
		if _, err := tx.ExecContext(ctx, `
			INSERT OR IGNORE INTO pjsk_new_card_seen (server, card_id, first_revision, first_seen_at)
			VALUES (?, ?, ?, ?)`, server, observation.CardID, revision, observedAt.Unix()); err != nil {
			return false, 0, err
		}
	}
	for eventID, cardIDs := range grouped {
		sort.Ints(cardIDs)
		encoded, err := json.Marshal(cardIDs)
		if err != nil {
			return false, 0, err
		}
		batchID := stableNewCardBatchID(eventID, cardIDs)
		if _, err := tx.ExecContext(ctx, `
			INSERT OR IGNORE INTO pjsk_new_card_pending
			(server, batch_id, revision, event_id, card_ids_json, discovered_at, attempts, next_retry_at, last_error)
			VALUES (?, ?, ?, ?, ?, ?, 0, 0, '')`,
			server, batchID, revision, eventID, string(encoded), observedAt.Unix()); err != nil {
			return false, 0, err
		}
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE pjsk_new_card_snapshot_state SET revision = ?, updated_at = ? WHERE server = ?`,
		revision, observedAt.Unix(), server); err != nil {
		return false, 0, err
	}
	if err := tx.Commit(); err != nil {
		return false, 0, err
	}
	return false, newCount, nil
}

// ListReadyNewCardBatches 返回已到重试时间的待处理批次。
func (s *Store) ListReadyNewCardBatches(ctx context.Context, server string, now time.Time) ([]NewCardBatch, error) {
	if now.IsZero() {
		now = time.Now()
	}
	db, err := s.conn(ctx)
	if err != nil {
		return nil, err
	}
	rows, err := db.QueryContext(ctx, `
		SELECT batch_id, revision, event_id, card_ids_json, discovered_at, attempts, next_retry_at, last_error
		FROM pjsk_new_card_pending
		WHERE server = ? AND next_retry_at <= ?
		ORDER BY discovered_at, batch_id`, server, now.Unix())
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var batches []NewCardBatch
	for rows.Next() {
		var batch NewCardBatch
		var encoded string
		var discoveredAt, nextRetryAt int64
		if err := rows.Scan(&batch.ID, &batch.Revision, &batch.EventID, &encoded, &discoveredAt, &batch.Attempts, &nextRetryAt, &batch.LastError); err != nil {
			return nil, err
		}
		if err := json.Unmarshal([]byte(encoded), &batch.CardIDs); err != nil {
			return nil, fmt.Errorf("decode new card batch %s: %w", batch.ID, err)
		}
		batch.DiscoveredAt = time.Unix(discoveredAt, 0)
		if nextRetryAt > 0 {
			batch.NextRetryAt = time.Unix(nextRetryAt, 0)
		}
		batches = append(batches, batch)
	}
	return batches, rows.Err()
}

// MarkNewCardBatchRetry 持久化单个批次的失败与下一次重试时间。
func (s *Store) MarkNewCardBatchRetry(ctx context.Context, server, batchID string, attempts int, nextRetryAt time.Time, cause error) error {
	db, err := s.conn(ctx)
	if err != nil {
		return err
	}
	message := ""
	if cause != nil {
		message = cause.Error()
	}
	_, err = db.ExecContext(ctx, `
		UPDATE pjsk_new_card_pending
		SET attempts = ?, next_retry_at = ?, last_error = ?
		WHERE server = ? AND batch_id = ?`, attempts, nextRetryAt.Unix(), message, server, batchID)
	return err
}

// AcknowledgeNewCardBatch 删除已对所有应接收群完成记账的批次。
func (s *Store) AcknowledgeNewCardBatch(ctx context.Context, server, batchID string) error {
	db, err := s.conn(ctx)
	if err != nil {
		return err
	}
	_, err = db.ExecContext(ctx, `DELETE FROM pjsk_new_card_pending WHERE server = ? AND batch_id = ?`, server, batchID)
	return err
}

func normalizeNewCardObservations(values []NewCardObservation) []NewCardObservation {
	byCard := make(map[int]int, len(values))
	for _, value := range values {
		if value.CardID <= 0 {
			continue
		}
		eventID := value.EventID
		if eventID < 0 {
			eventID = 0
		}
		if previous, ok := byCard[value.CardID]; !ok || (eventID > 0 && (previous == 0 || eventID < previous)) {
			byCard[value.CardID] = eventID
		}
	}
	out := make([]NewCardObservation, 0, len(byCard))
	for cardID, eventID := range byCard {
		out = append(out, NewCardObservation{CardID: cardID, EventID: eventID})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].CardID < out[j].CardID })
	return out
}

func stableNewCardBatchID(eventID int, cardIDs []int) string {
	parts := make([]string, 0, len(cardIDs)+1)
	parts = append(parts, strconv.Itoa(eventID))
	for _, cardID := range cardIDs {
		parts = append(parts, strconv.Itoa(cardID))
	}
	sum := sha256.Sum256([]byte(strings.Join(parts, ",")))
	prefix := "standalone"
	if eventID > 0 {
		prefix = "event-" + strconv.Itoa(eventID)
	}
	return prefix + "-" + hex.EncodeToString(sum[:8])
}
