package ranking

import (
	"bytes"
	"compress/zlib"
	"database/sql"
	"fmt"
	"log"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"

	_ "modernc.org/sqlite"
)

// 活跃中的 run 先把 end_batch 设为 MaxInt64，活动结束封口时再刷成实际的 max_batch
const openRunEndBatch int64 = math.MaxInt64

type identKey struct {
	uid  string
	name string
}

type openRun struct {
	startBatch int64
	identID    int64
	score      int64
}

type rankingDB struct {
	mu       sync.Mutex
	db       *sql.DB
	path     string
	isLegacy bool
	closed   bool

	nextBatch  int64
	nextIdent  int64
	nextSig    int64
	identCache map[identKey]int64
	sigCache   map[string]int64
	activeRuns map[int64]openRun
}

const rleSchemaSQL = `
CREATE TABLE IF NOT EXISTS archive_meta (
	key   TEXT PRIMARY KEY,
	value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS identities (
	id   INTEGER PRIMARY KEY,
	uid  TEXT NOT NULL,
	name TEXT NOT NULL,
	UNIQUE(uid, name)
);
CREATE INDEX IF NOT EXISTS idx_identities_uid ON identities(uid);
CREATE TABLE IF NOT EXISTS batch_sigs (
	sig_id     INTEGER PRIMARY KEY,
	ranks_blob BLOB NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS sig_ranks (
	sig_id  INTEGER NOT NULL,
	rank    INTEGER NOT NULL,
	ordinal INTEGER NOT NULL,
	PRIMARY KEY (rank, sig_id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS batches (
	batch_id INTEGER PRIMARY KEY,
	ts       INTEGER NOT NULL,
	sig_id   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_batches_ts ON batches(ts);
CREATE INDEX IF NOT EXISTS idx_batches_sig ON batches(sig_id, batch_id);
CREATE TABLE IF NOT EXISTS runs (
	rank        INTEGER NOT NULL,
	start_batch INTEGER NOT NULL,
	end_batch   INTEGER NOT NULL,
	identity_id INTEGER NOT NULL,
	score       INTEGER NOT NULL,
	PRIMARY KEY (rank, start_batch)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_runs_ident ON runs(identity_id, start_batch);
`

func openRankingDB(path string) (*rankingDB, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	db, err := sql.Open("sqlite", "file:"+path+"?_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)&_pragma=synchronous(NORMAL)")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)

	var hasRanking, hasRuns int
	if err := db.QueryRow("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ranking' LIMIT 1").Scan(&hasRanking); err != nil && err != sql.ErrNoRows {
		_ = db.Close()
		return nil, err
	}
	if err := db.QueryRow("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs' LIMIT 1").Scan(&hasRuns); err != nil && err != sql.ErrNoRows {
		_ = db.Close()
		return nil, err
	}

	// 旧格式库继续写旧表，避免活动半路切表
	if hasRanking == 1 && hasRuns == 0 {
		for _, s := range []string{
			"CREATE INDEX IF NOT EXISTS idx_ranking_rank_ts ON ranking (rank, ts)",
			"CREATE INDEX IF NOT EXISTS idx_ranking_uid_ts ON ranking (uid, ts)",
		} {
			if _, err := db.Exec(s); err != nil {
				_ = db.Close()
				return nil, err
			}
		}
		return &rankingDB{db: db, path: path, isLegacy: true}, nil
	}

	if _, err := db.Exec(rleSchemaSQL); err != nil {
		_ = db.Close()
		return nil, err
	}
	for _, kv := range [][2]string{
		{"format", "rle-v1"},
		{"source", "go-pjsk-helper-live"},
	} {
		if _, err := db.Exec("INSERT OR IGNORE INTO archive_meta(key, value) VALUES (?, ?)", kv[0], kv[1]); err != nil {
			_ = db.Close()
			return nil, err
		}
	}
	if _, err := db.Exec("INSERT OR REPLACE INTO archive_meta(key, value) VALUES ('sealed', '0')"); err != nil {
		_ = db.Close()
		return nil, err
	}

	r := &rankingDB{
		db:         db,
		path:       path,
		identCache: make(map[identKey]int64, 1024),
		sigCache:   make(map[string]int64, 32),
		activeRuns: make(map[int64]openRun, 128),
	}
	if err := r.hydrate(); err != nil {
		_ = db.Close()
		return nil, err
	}
	return r, nil
}

func (r *rankingDB) hydrate() error {
	var maxBatch, maxIdent, maxSig int64
	if err := r.db.QueryRow("SELECT COALESCE(MAX(batch_id), 0) FROM batches").Scan(&maxBatch); err != nil {
		return err
	}
	if err := r.db.QueryRow("SELECT COALESCE(MAX(id), 0) FROM identities").Scan(&maxIdent); err != nil {
		return err
	}
	if err := r.db.QueryRow("SELECT COALESCE(MAX(sig_id), 0) FROM batch_sigs").Scan(&maxSig); err != nil {
		return err
	}
	r.nextBatch = maxBatch + 1
	r.nextIdent = maxIdent + 1
	r.nextSig = maxSig + 1

	identRows, err := r.db.Query("SELECT id, uid, name FROM identities")
	if err != nil {
		return err
	}
	for identRows.Next() {
		var id int64
		var uid, name string
		if err := identRows.Scan(&id, &uid, &name); err != nil {
			_ = identRows.Close()
			return err
		}
		r.identCache[identKey{uid: uid, name: name}] = id
	}
	if err := identRows.Err(); err != nil {
		_ = identRows.Close()
		return err
	}
	_ = identRows.Close()

	sigRows, err := r.db.Query("SELECT sig_id, ranks_blob FROM batch_sigs")
	if err != nil {
		return err
	}
	for sigRows.Next() {
		var sigID int64
		var blob []byte
		if err := sigRows.Scan(&sigID, &blob); err != nil {
			_ = sigRows.Close()
			return err
		}
		r.sigCache[string(blob)] = sigID
	}
	if err := sigRows.Err(); err != nil {
		_ = sigRows.Close()
		return err
	}
	_ = sigRows.Close()

	runRows, err := r.db.Query(
		"SELECT rank, start_batch, identity_id, score FROM runs WHERE end_batch = ?",
		openRunEndBatch,
	)
	if err != nil {
		return err
	}
	for runRows.Next() {
		var rank, sb, identID, score int64
		if err := runRows.Scan(&rank, &sb, &identID, &score); err != nil {
			_ = runRows.Close()
			return err
		}
		r.activeRuns[rank] = openRun{startBatch: sb, identID: identID, score: score}
	}
	if err := runRows.Err(); err != nil {
		_ = runRows.Close()
		return err
	}
	_ = runRows.Close()
	return nil
}

func (r *rankingDB) record(rankings []Ranking, ts int64) error {
	if len(rankings) == 0 {
		return nil
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.closed {
		return sql.ErrConnDone
	}
	if r.isLegacy {
		return r.recordLegacyLocked(rankings, ts)
	}
	return r.recordRLELocked(rankings, ts)
}

func (r *rankingDB) recordLegacyLocked(rankings []Ranking, ts int64) error {
	tx, err := r.db.Begin()
	if err != nil {
		return err
	}
	stmt, err := tx.Prepare("INSERT INTO ranking (uid, name, score, rank, ts) VALUES (?, ?, ?, ?, ?)")
	if err != nil {
		_ = tx.Rollback()
		return err
	}
	defer stmt.Close()
	for _, rk := range rankings {
		if _, err := stmt.Exec(rk.UID, rk.Name, rk.Score, rk.Rank, ts); err != nil {
			_ = tx.Rollback()
			return err
		}
	}
	return tx.Commit()
}

func encodeRanksBlob(rankings []Ranking) ([]byte, error) {
	parts := make([]string, len(rankings))
	for i, rk := range rankings {
		parts[i] = strconv.FormatInt(rk.Rank, 10)
	}
	var buf bytes.Buffer
	zw, err := zlib.NewWriterLevel(&buf, zlib.DefaultCompression)
	if err != nil {
		return nil, err
	}
	if _, err := zw.Write([]byte(strings.Join(parts, ","))); err != nil {
		_ = zw.Close()
		return nil, err
	}
	if err := zw.Close(); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

func (r *rankingDB) recordRLELocked(rankings []Ranking, ts int64) error {
	// 按 rank 去重，防止 sig_ranks 主键冲突
	dedup := make([]Ranking, 0, len(rankings))
	seen := make(map[int64]struct{}, len(rankings))
	for _, rk := range rankings {
		if rk.Rank <= 0 {
			continue
		}
		if _, dup := seen[rk.Rank]; dup {
			continue
		}
		seen[rk.Rank] = struct{}{}
		dedup = append(dedup, rk)
	}
	if len(dedup) == 0 {
		return nil
	}

	tx, err := r.db.Begin()
	if err != nil {
		return err
	}
	committed := false
	defer func() {
		if !committed {
			_ = tx.Rollback()
		}
	}()

	curBatch := r.nextBatch
	prevBatch := curBatch - 1
	nextIdent := r.nextIdent
	nextSig := r.nextSig

	ranksBlob, err := encodeRanksBlob(dedup)
	if err != nil {
		return err
	}
	sigKey := string(ranksBlob)
	sigID, hasSig := r.sigCache[sigKey]
	var newSigKey string
	var newSigID int64
	if !hasSig {
		sigID = nextSig
		nextSig++
		if _, err := tx.Exec("INSERT INTO batch_sigs(sig_id, ranks_blob) VALUES (?, ?)", sigID, ranksBlob); err != nil {
			return err
		}
		srStmt, err := tx.Prepare("INSERT INTO sig_ranks(sig_id, rank, ordinal) VALUES (?, ?, ?)")
		if err != nil {
			return err
		}
		for ord, rk := range dedup {
			if _, err := srStmt.Exec(sigID, rk.Rank, ord); err != nil {
				_ = srStmt.Close()
				return err
			}
		}
		_ = srStmt.Close()
		newSigKey = sigKey
		newSigID = sigID
	}

	if _, err := tx.Exec("INSERT INTO batches(batch_id, ts, sig_id) VALUES (?, ?, ?)", curBatch, ts, sigID); err != nil {
		return err
	}

	var pendingIdents map[identKey]int64
	var pendingOpen map[int64]openRun
	var pendingClosed []int64

	var identStmt, closeStmt, openStmt *sql.Stmt
	defer func() {
		if identStmt != nil {
			_ = identStmt.Close()
		}
		if closeStmt != nil {
			_ = closeStmt.Close()
		}
		if openStmt != nil {
			_ = openStmt.Close()
		}
	}()

	for _, rk := range dedup {
		ik := identKey{uid: rk.UID, name: rk.Name}
		identID, ok := r.identCache[ik]
		if !ok && pendingIdents != nil {
			identID, ok = pendingIdents[ik]
		}
		if !ok {
			if identStmt == nil {
				identStmt, err = tx.Prepare("INSERT INTO identities(id, uid, name) VALUES (?, ?, ?)")
				if err != nil {
					return err
				}
			}
			identID = nextIdent
			nextIdent++
			if _, err := identStmt.Exec(identID, rk.UID, rk.Name); err != nil {
				return err
			}
			if pendingIdents == nil {
				pendingIdents = make(map[identKey]int64)
			}
			pendingIdents[ik] = identID
		}

		cur, hasOpen := r.activeRuns[rk.Rank]
		if hasOpen && cur.identID == identID && cur.score == rk.Score {
			continue
		}
		if hasOpen {
			if closeStmt == nil {
				closeStmt, err = tx.Prepare("UPDATE runs SET end_batch = ? WHERE rank = ? AND start_batch = ?")
				if err != nil {
					return err
				}
			}
			if _, err := closeStmt.Exec(prevBatch, rk.Rank, cur.startBatch); err != nil {
				return err
			}
		}
		if openStmt == nil {
			openStmt, err = tx.Prepare("INSERT INTO runs(rank, start_batch, end_batch, identity_id, score) VALUES (?, ?, ?, ?, ?)")
			if err != nil {
				return err
			}
		}
		if _, err := openStmt.Exec(rk.Rank, curBatch, openRunEndBatch, identID, rk.Score); err != nil {
			return err
		}
		if pendingOpen == nil {
			pendingOpen = make(map[int64]openRun)
		}
		pendingOpen[rk.Rank] = openRun{startBatch: curBatch, identID: identID, score: rk.Score}
	}

	// 本批没出现的活跃名次在这里封口，避免残留脏状态
	for rank, cur := range r.activeRuns {
		if _, present := seen[rank]; present {
			continue
		}
		if closeStmt == nil {
			closeStmt, err = tx.Prepare("UPDATE runs SET end_batch = ? WHERE rank = ? AND start_batch = ?")
			if err != nil {
				return err
			}
		}
		if _, err := closeStmt.Exec(prevBatch, rank, cur.startBatch); err != nil {
			return err
		}
		pendingClosed = append(pendingClosed, rank)
	}

	if err := tx.Commit(); err != nil {
		return err
	}
	committed = true

	// 事务提交后再更内存缓存，防止回滚后状态对不上
	r.nextBatch = curBatch + 1
	r.nextIdent = nextIdent
	r.nextSig = nextSig
	if newSigID != 0 {
		r.sigCache[newSigKey] = newSigID
	}
	for k, v := range pendingIdents {
		r.identCache[k] = v
	}
	for _, rank := range pendingClosed {
		delete(r.activeRuns, rank)
	}
	for rank, run := range pendingOpen {
		r.activeRuns[rank] = run
	}
	return nil
}

// 常规关闭不封口，重启后接着写
func (r *rankingDB) close() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.closed {
		return nil
	}
	r.closed = true
	_, _ = r.db.Exec("PRAGMA wal_checkpoint(PASSIVE)")
	return r.db.Close()
}

func (r *rankingDB) sealAndClose() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.closed {
		return nil
	}
	r.closed = true
	if !r.isLegacy {
		if err := sealRLEConn(r.db); err != nil {
			log.Printf("[ranking] seal %s failed: %v", r.path, err)
		}
	} else {
		_, _ = r.db.Exec("PRAGMA wal_checkpoint(TRUNCATE)")
	}
	return r.db.Close()
}

func sealRLEConn(db *sql.DB) error {
	var maxBatch int64
	if err := db.QueryRow("SELECT COALESCE(MAX(batch_id), 0) FROM batches").Scan(&maxBatch); err != nil {
		return err
	}
	if maxBatch > 0 {
		if _, err := db.Exec("UPDATE runs SET end_batch = ? WHERE end_batch > ?", maxBatch, maxBatch); err != nil {
			return err
		}
	}
	if _, err := db.Exec("INSERT OR REPLACE INTO archive_meta(key, value) VALUES ('sealed', '1')"); err != nil {
		return err
	}
	if _, err := db.Exec("PRAGMA wal_checkpoint(TRUNCATE)"); err != nil {
		return err
	}
	if _, err := db.Exec("VACUUM"); err != nil {
		return err
	}
	if _, err := db.Exec("PRAGMA journal_mode=DELETE"); err != nil {
		return err
	}
	return nil
}

func sealUnsealedRLEFile(path string) (bool, error) {
	db, err := sql.Open("sqlite", "file:"+path+"?_pragma=busy_timeout(5000)")
	if err != nil {
		return false, err
	}
	defer db.Close()
	db.SetMaxOpenConns(1)

	var hasRuns int
	if err := db.QueryRow("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs' LIMIT 1").Scan(&hasRuns); err != nil && err != sql.ErrNoRows {
		return false, err
	}
	if hasRuns != 1 {
		return false, nil
	}
	var sealed string
	if err := db.QueryRow("SELECT value FROM archive_meta WHERE key = 'sealed' LIMIT 1").Scan(&sealed); err != nil && err != sql.ErrNoRows {
		return false, err
	}
	var openCount int
	if err := db.QueryRow("SELECT COUNT(*) FROM runs WHERE end_batch = ?", openRunEndBatch).Scan(&openCount); err != nil {
		return false, err
	}
	_, walErr := os.Stat(path + "-wal")
	if sealed == "1" && openCount == 0 && os.IsNotExist(walErr) {
		return true, nil
	}
	if err := sealRLEConn(db); err != nil {
		return false, fmt.Errorf("seal %s: %w", path, err)
	}
	log.Printf("[ranking] sealed ended rle-v1 db: %s", path)
	return true, nil
}
