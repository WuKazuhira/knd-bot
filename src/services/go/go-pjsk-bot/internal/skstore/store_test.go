package skstore

import (
	"context"
	"database/sql"
	"os"
	"path/filepath"
	"testing"
	"time"

	_ "modernc.org/sqlite"
)

// setupDB 建一个临时时序库并写入若干榜线记录。
func setupDB(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	dbDir := filepath.Join(dir, "ondemand", "database", "sk_jp")
	if err := os.MkdirAll(dbDir, 0o755); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dbDir, "100_ranking.db")
	db, err := sql.Open("sqlite", "file:"+path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err := db.Exec(`CREATE TABLE ranking (id INTEGER PRIMARY KEY AUTOINCREMENT,
		uid TEXT, name TEXT, score INTEGER, rank INTEGER, ts REAL)`); err != nil {
		t.Fatal(err)
	}
	base := time.Now()
	// rank1: 两条（旧 10000@-1h、新 20000@now）；rank100: 一条
	insert := func(uid, name string, score, rank int, tsec time.Time) {
		if _, err := db.Exec("INSERT INTO ranking (uid,name,score,rank,ts) VALUES (?,?,?,?,?)",
			uid, name, score, rank, float64(tsec.Unix())); err != nil {
			t.Fatal(err)
		}
	}
	insert("u1", "p1", 10000, 1, base.Add(-3600*time.Second))
	insert("u1", "p1", 20000, 1, base)
	insert("u2", "p2", 5000, 100, base)
	return dir
}

func TestQueryLatestRanking(t *testing.T) {
	dir := setupDB(t)
	s := New(dir)
	ctx := context.Background()

	// 指定名次
	rs, err := s.QueryLatestRanking(ctx, "jp", 100, []int{1, 100})
	if err != nil {
		t.Fatal(err)
	}
	if len(rs) != 2 {
		t.Fatalf("应取 2 条最新, got %d", len(rs))
	}
	// rank1 最新应为 20000
	for _, r := range rs {
		if r.Rank == 1 && r.Score != 20000 {
			t.Errorf("rank1 最新分应为 20000, got %d", r.Score)
		}
	}

	// 全部名次
	all, err := s.QueryLatestRanking(ctx, "jp", 100, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(all) != 2 {
		t.Fatalf("全部最新应有 2 个 rank, got %d", len(all))
	}
}

func TestQueryFirstRankingAfter(t *testing.T) {
	dir := setupDB(t)
	s := New(dir)
	ctx := context.Background()
	after := time.Now().Add(-2 * time.Hour)

	rs, err := s.QueryFirstRankingAfter(ctx, "jp", 100, after, []int{1})
	if err != nil {
		t.Fatal(err)
	}
	if len(rs) != 1 || rs[0].Score != 10000 {
		t.Fatalf("rank1 在 2h 前之后最早应为 10000, got %+v", rs)
	}
}

func TestQueryRankingTailByUID(t *testing.T) {
	dir := setupDB(t)
	s := New(dir)

	rs, err := s.QueryRankingTailByUID(context.Background(), "jp", 100, "u1")
	if err != nil {
		t.Fatal(err)
	}
	if len(rs) != 2 || rs[0].Score != 10000 || rs[1].Score != 20000 {
		t.Fatalf("u1 尾部历史应保留最近一次变分边界, got %+v", rs)
	}
}

func TestMissingDB(t *testing.T) {
	s := New(t.TempDir())
	rs, err := s.QueryLatestRanking(context.Background(), "jp", 999, nil)
	if err != nil {
		t.Fatalf("库不存在应返回空而非错误, err=%v", err)
	}
	if len(rs) != 0 {
		t.Errorf("库不存在应返回空, got %d", len(rs))
	}
}
