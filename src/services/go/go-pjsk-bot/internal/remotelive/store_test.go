package remotelive

import (
	"context"
	"database/sql"
	"os"
	"path/filepath"
	"strings"
	"testing"

	_ "modernc.org/sqlite"
)

func setupLiveDB(t *testing.T, schema string) string {
	t.Helper()
	root := t.TempDir()
	dir := filepath.Join(root, "ondemand", "database", "remote_live")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "cn_account-1.db")
	db, err := sql.Open("sqlite", "file:"+path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err := db.Exec(schema); err != nil {
		t.Fatal(err)
	}
	return root
}

const liveSchema = `CREATE TABLE live_records (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	ts INTEGER NOT NULL,
	event_id INTEGER,
	account_id TEXT,
	live_id TEXT,
	event_rank INTEGER,
	event_point INTEGER,
	wl_chapter_no INTEGER,
	wl_chapter_rank INTEGER,
	wl_chapter_point INTEGER,
	score INTEGER
)`

func TestQueryRecordsAndChapters(t *testing.T) {
	root := setupLiveDB(t, liveSchema)
	path := filepath.Join(root, "ondemand", "database", "remote_live", "cn_account-1.db")
	db, err := sql.Open("sqlite", "file:"+path)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`INSERT INTO live_records
		(ts,event_id,account_id,live_id,event_rank,event_point,wl_chapter_no,wl_chapter_rank,wl_chapter_point,score)
		VALUES (?,?,?,?,?,?,?,?,?,?)`,
		100, 42, "account-1", "live-1", 120, 100000, 2, 8, 90000, 100)
	if err != nil {
		db.Close()
		t.Fatal(err)
	}
	_, err = db.Exec(`INSERT INTO live_records (ts,event_id,event_rank,event_point) VALUES (?,?,?,?)`,
		200, 42, 100, 120000)
	if err != nil {
		db.Close()
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}

	records, err := New(root).QueryRecords(context.Background(), "cn", "account-1", 42)
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 2 || records[0].TS != 100 || records[1].TS != 200 {
		t.Fatalf("记录排序/数量错误: %+v", records)
	}
	if records[0].EventRank == nil || *records[0].EventRank != 120 {
		t.Fatalf("event_rank 解析错误: %+v", records[0])
	}
	if records[1].WLChapterRank != nil {
		t.Fatalf("NULL WL 字段不应伪造成 0: %+v", records[1])
	}
	chapters := ChapterNumbers(records)
	if len(chapters) != 1 || chapters[0] != 2 {
		t.Fatalf("章节解析错误: %v", chapters)
	}
}

func TestInsertRecordRoundTrip(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	eventID := int64(42)
	rank := 12
	point := 3456
	account := "account-1"
	liveID := "live-7"
	store := New(root)
	if err := store.InsertRecord(ctx, "cn", account, Record{
		TS:         100,
		EventID:    &eventID,
		AccountID:  &account,
		LiveID:     &liveID,
		EventRank:  &rank,
		EventPoint: &point,
	}); err != nil {
		t.Fatal(err)
	}
	records, err := store.QueryRecords(ctx, "cn", account, int(eventID))
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 1 || records[0].TS != 100 {
		t.Fatalf("round trip records=%+v", records)
	}
	if records[0].EventRank == nil || *records[0].EventRank != rank || records[0].LiveID == nil || *records[0].LiveID != liveID {
		t.Fatalf("round trip fields=%+v", records[0])
	}
	if records[0].Score != nil || records[0].WLChapterNo != nil {
		t.Fatalf("NULL fields must remain nil: %+v", records[0])
	}
}

func TestMissingDBReturnsEmpty(t *testing.T) {
	records, err := New(t.TempDir()).QueryRecords(context.Background(), "jp", "account", 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 0 {
		t.Fatalf("不存在的库应返回空, got %d", len(records))
	}
}

func TestIncompatibleSchemaReturnsError(t *testing.T) {
	root := setupLiveDB(t, "CREATE TABLE live_records (id INTEGER PRIMARY KEY, ts INTEGER)")
	_, err := New(root).QueryRecords(context.Background(), "cn", "account-1", 42)
	if err == nil {
		t.Fatal("schema 缺列时应返回明确错误")
	}
	if got := err.Error(); got == "" || !containsAll(got, "live_records", "缺少字段") {
		t.Fatalf("schema 错误信息不清晰: %q", got)
	}
}

func TestInvalidRegion(t *testing.T) {
	_, err := New(t.TempDir()).QueryRecords(context.Background(), "us", "account", 1)
	if err == nil {
		t.Fatal("未知区服应返回错误")
	}
}

func containsAll(text string, parts ...string) bool {
	for _, part := range parts {
		if !strings.Contains(text, part) {
			return false
		}
	}
	return true
}
