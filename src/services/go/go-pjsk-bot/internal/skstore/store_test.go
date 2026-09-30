package skstore

import (
	"context"
	"database/sql"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/skranking"
)

func TestQueryRankingTailPreservesOlderHighScore(t *testing.T) {
	dataDir := t.TempDir()
	dbDir := filepath.Join(dataDir, "ondemand", "database", "sk_cn")
	if err := os.MkdirAll(dbDir, 0755); err != nil {
		t.Fatal(err)
	}
	db, err := sql.Open("sqlite", filepath.Join(dbDir, "180_ranking.db"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`CREATE TABLE ranking (id INTEGER PRIMARY KEY, uid TEXT, name TEXT, score INTEGER, rank INTEGER, ts INTEGER)`); err != nil {
		t.Fatal(err)
	}
	base := time.Date(2026, time.September, 26, 10, 0, 0, 0, time.UTC)
	for _, row := range []struct {
		minutesBefore int
		score         int64
	}{
		{70, 1200}, {65, 1000}, {59, 1000}, {50, 1200}, {0, 1250},
	} {
		if _, err := db.Exec("INSERT INTO ranking (uid, name, score, rank, ts) VALUES (?, ?, ?, ?, ?)",
			"user", "test", row.score, 90, base.Add(-time.Duration(row.minutesBefore)*time.Minute).Unix()); err != nil {
			t.Fatal(err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}

	store := New(dataDir)
	defer store.Close()
	history, preWindowMaxScore, err := store.QueryRankingTailByUID(context.Background(), "cn", 180, "user")
	if err != nil {
		t.Fatal(err)
	}
	if preWindowMaxScore != 1200 {
		t.Fatalf("pre-window peak = %d, want 1200", preWindowMaxScore)
	}
	stats := skranking.BuildActivityStats(history, history[len(history)-1], preWindowMaxScore)
	if stats.PlayCount != 1 || stats.LastPt != 50 {
		t.Errorf("count=%d last=%d; want count=1 last=50", stats.PlayCount, stats.LastPt)
	}
}

func TestQueryLatestRankingUsesOneSameSecondBatch(t *testing.T) {
	dataDir := t.TempDir()
	dbDir := filepath.Join(dataDir, "ondemand", "database", "sk_cn")
	if err := os.MkdirAll(dbDir, 0755); err != nil {
		t.Fatal(err)
	}
	db, err := sql.Open("sqlite", filepath.Join(dbDir, "180_ranking.db"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`CREATE TABLE ranking (id INTEGER PRIMARY KEY, uid TEXT, name TEXT, score INTEGER, rank INTEGER, ts INTEGER)`); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`CREATE INDEX idx_ranking_rank_ts ON ranking (rank, ts)`); err != nil {
		t.Fatal(err)
	}
	// 两次完整采集同在一秒，玩家 A 从 T96 移到 T98。
	for _, row := range []struct {
		uid  string
		rank int
	}{
		{"A", 96}, {"B", 98}, // 第一批
		{"B", 96}, {"A", 98}, // 第二批
	} {
		if _, err := db.Exec("INSERT INTO ranking (uid, name, score, rank, ts) VALUES (?, ?, ?, ?, ?)",
			row.uid, row.uid, 1000, row.rank, 1790419297); err != nil {
			t.Fatal(err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}

	store := New(dataDir)
	defer store.Close()
	rows, err := store.QueryLatestRanking(context.Background(), "cn", 180, []int{98, 96, 97})
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 2 || rows[0].Rank != 98 || rows[0].UID != "A" || rows[1].Rank != 96 || rows[1].UID != "B" {
		t.Fatalf("latest rankings = %+v, want T98=A, T96=B", rows)
	}
}
