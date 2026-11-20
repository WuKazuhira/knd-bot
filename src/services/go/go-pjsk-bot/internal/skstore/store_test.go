package skstore

import (
	"context"
	"database/sql"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestQueryFirstRankingAfterBatch(t *testing.T) {
	dir := t.TempDir()
	dbDir := filepath.Join(dir, "ondemand", "database", "sk_jp")
	if err := os.MkdirAll(dbDir, 0o755); err != nil {
		t.Fatal(err)
	}
	dbPath := filepath.Join(dbDir, "100_ranking.db")
	db, err := sql.Open("sqlite", "file:"+dbPath)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`CREATE TABLE ranking (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		uid TEXT NOT NULL,
		name TEXT NOT NULL,
		score INTEGER NOT NULL,
		rank INTEGER NOT NULL,
		ts REAL NOT NULL
	)`)
	if err != nil {
		t.Fatal(err)
	}
	rows := []struct {
		uid, name  string
		score      int64
		rank       int
		ts         float64
	}{
		{"u1", "p1", 1000, 1, 100},
		{"u1", "p1", 2000, 1, 200},
		{"u1", "p1", 3000, 1, 300},
		{"u2", "p2", 500, 2, 150},
		{"u2", "p2", 1500, 2, 250},
	}
	for _, r := range rows {
		if _, err := db.Exec("INSERT INTO ranking(uid,name,score,rank,ts) VALUES(?,?,?,?,?)",
			r.uid, r.name, r.score, r.rank, r.ts); err != nil {
			t.Fatal(err)
		}
	}
	db.Close()

	store := New(dir)
	defer store.Close()

	got, err := store.QueryFirstRankingAfter(context.Background(), "jp", 100, time.Unix(180, 0), []int{2, 1, 999})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 {
		t.Fatalf("expected 2 rows, got %d: %+v", len(got), got)
	}
	// 保持请求顺序: rank 2 在 rank 1 前
	if got[0].Rank != 2 || got[0].Score != 1500 || got[1].Rank != 1 || got[1].Score != 2000 {
		t.Fatalf("unexpected rows: %+v", got)
	}
}
