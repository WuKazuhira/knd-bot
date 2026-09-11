// Package remotelive 读写 remote 自动打歌的 live_records SQLite。
//
// 查询路径保持只读，remote 循环通过 InsertRecord 写入与 Python 兼容的
// data/pjsk/ondemand/database/remote_live/{region}_{account}.db，skme 再从同一库读取。
package remotelive

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"unicode"

	_ "modernc.org/sqlite"
)

// Record 对应 Python _remote_sql.LiveRecord 的持久化字段。
// 可空字段使用指针，避免把数据库 NULL 伪造成 0。
type Record struct {
	ID             int64
	TS             int64
	EventID        *int64
	AccountID      *string
	LiveID         *string
	EventRank      *int
	EventPoint     *int
	WLChapterNo    *int
	WLChapterRank  *int
	WLChapterPoint *int
	Score          *int
}

// Store 读取 remote_live 目录下的账号记录库。
type Store struct {
	root string // data/pjsk/ondemand/database/remote_live
	mu   sync.RWMutex
}

// New 创建只读 remote_live Store。dataDir 为 data/pjsk。
func New(dataDir string) *Store {
	return &Store{root: filepath.Join(dataDir, "ondemand", "database", "remote_live")}
}

func normalizeRegion(region string) (string, error) {
	switch strings.ToLower(strings.TrimSpace(region)) {
	case "jp", "cn", "tw":
		return strings.ToLower(strings.TrimSpace(region)), nil
	default:
		return "", fmt.Errorf("不支持的 remote 区服 %q（仅支持 jp/cn/tw）", region)
	}
}

// safeAccount 与 Python _remote_sql._db_path 保持同一最小安全规则，避免命令参数
// 被解释为路径。remote 账号通常是数字或短字符串；其他字符会被丢弃。
func safeAccount(account string) string {
	var b strings.Builder
	for _, r := range strings.TrimSpace(account) {
		if unicode.IsLetter(r) || unicode.IsDigit(r) || r == '.' || r == '_' || r == '-' {
			b.WriteRune(r)
		}
	}
	if b.Len() == 0 {
		return "unknown"
	}
	return b.String()
}

func (s *Store) dbPath(region, account string) (string, error) {
	region, err := normalizeRegion(region)
	if err != nil {
		return "", err
	}
	return filepath.Join(s.root, region+"_"+safeAccount(account)+".db"), nil
}

func (s *Store) open(region, account string) (*sql.DB, error) {
	path, err := s.dbPath(region, account)
	if err != nil {
		return nil, err
	}
	if _, err := os.Stat(path); err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("检查 remote_live 数据库失败: %w", err)
	}
	// 只读模式：Python 仍可能通过 WAL 写入，SQLite 会在查询时读取一致快照。
	db, err := sql.Open("sqlite", "file:"+path+"?mode=ro")
	if err != nil {
		return nil, fmt.Errorf("打开 remote_live 数据库失败: %w", err)
	}
	return db, nil
}

const liveRecordsSchema = `
CREATE TABLE IF NOT EXISTS live_records (
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

// ensureSchema 创建远程 live 记录库及其索引。该方法只在写入路径调用；查询路径
// 仍保持只读且不会因为数据库不存在而创建文件。
func (s *Store) ensureSchema(ctx context.Context, region, account string) (*sql.DB, error) {
	path, err := s.dbPath(region, account)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, fmt.Errorf("创建 remote_live 目录失败: %w", err)
	}
	db, err := sql.Open("sqlite", "file:"+path)
	if err != nil {
		return nil, fmt.Errorf("打开 remote_live 数据库失败: %w", err)
	}
	closeOnError := true
	defer func() {
		if closeOnError {
			_ = db.Close()
		}
	}()
	if _, err := db.ExecContext(ctx, "PRAGMA journal_mode=WAL"); err != nil {
		return nil, fmt.Errorf("设置 remote_live WAL 失败: %w", err)
	}
	if _, err := db.ExecContext(ctx, liveRecordsSchema); err != nil {
		return nil, fmt.Errorf("创建 live_records 失败: %w", err)
	}
	if _, err := db.ExecContext(ctx, "CREATE INDEX IF NOT EXISTS idx_live_event_ts ON live_records (event_id, ts)"); err != nil {
		return nil, fmt.Errorf("创建 remote_live 活动索引失败: %w", err)
	}
	if _, err := db.ExecContext(ctx, "CREATE INDEX IF NOT EXISTS idx_live_chapter_ts ON live_records (event_id, wl_chapter_no, ts)"); err != nil {
		return nil, fmt.Errorf("创建 remote_live 章节索引失败: %w", err)
	}
	closeOnError = false
	return db, nil
}

// InsertRecord 以 Python _remote_sql.insert_record 的字段布局写入一条记录。
func (s *Store) InsertRecord(ctx context.Context, region, account string, record Record) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	db, err := s.ensureSchema(ctx, region, account)
	if err != nil {
		return err
	}
	defer db.Close()
	_, err = db.ExecContext(ctx, `
		INSERT INTO live_records
			(ts, event_id, account_id, live_id, event_rank, event_point,
			 wl_chapter_no, wl_chapter_rank, wl_chapter_point, score)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		record.TS, record.EventID, record.AccountID, record.LiveID,
		record.EventRank, record.EventPoint, record.WLChapterNo,
		record.WLChapterRank, record.WLChapterPoint, record.Score)
	if err != nil {
		return fmt.Errorf("写入 live_records 失败: %w", err)
	}
	return nil
}

var requiredColumns = []string{
	"id", "ts", "event_id", "account_id", "live_id", "event_rank", "event_point",
	"wl_chapter_no", "wl_chapter_rank", "wl_chapter_point", "score",
}

// validateSchema 在每次只读查询前确认 Python 当前 live_records schema。
// 这比直接依赖 SELECT 顺序更安全：若共享库被旧版本/其他库替换，返回明确错误。
func validateSchema(ctx context.Context, db *sql.DB) error {
	rows, err := db.QueryContext(ctx, "PRAGMA table_info(live_records)")
	if err != nil {
		return fmt.Errorf("读取 live_records schema 失败: %w", err)
	}
	defer rows.Close()

	columns := make(map[string]struct{}, len(requiredColumns))
	for rows.Next() {
		var cid, notNull, pk int
		var name, typ string
		var defaultValue any
		if err := rows.Scan(&cid, &name, &typ, &notNull, &defaultValue, &pk); err != nil {
			return fmt.Errorf("解析 live_records schema 失败: %w", err)
		}
		columns[strings.ToLower(name)] = struct{}{}
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("读取 live_records schema 失败: %w", err)
	}
	missing := make([]string, 0)
	for _, name := range requiredColumns {
		if _, ok := columns[name]; !ok {
			missing = append(missing, name)
		}
	}
	if len(missing) > 0 {
		return fmt.Errorf("live_records schema 不兼容，缺少字段: %s", strings.Join(missing, ", "))
	}
	return nil
}

// QueryRecords 查询某区服/remote 账号在指定活动中的全部记录，按 ts、id 升序。
// 数据库不存在时返回空列表且不报错，与 Python query_records(create=False) 一致；
// 库存在但 schema 不兼容时返回明确错误，不静默伪造曲线。
func (s *Store) QueryRecords(ctx context.Context, region, account string, eventID int) ([]Record, error) {
	db, err := s.open(region, account)
	if err != nil || db == nil {
		return nil, err
	}
	defer db.Close()
	if err := validateSchema(ctx, db); err != nil {
		return nil, err
	}

	rows, err := db.QueryContext(ctx, `
		SELECT id, ts, event_id, account_id, live_id, event_rank, event_point,
		       wl_chapter_no, wl_chapter_rank, wl_chapter_point, score
		FROM live_records
		WHERE event_id = ?
		ORDER BY ts ASC, id ASC`, eventID)
	if err != nil {
		return nil, fmt.Errorf("查询 live_records 失败: %w", err)
	}
	defer rows.Close()

	var out []Record
	for rows.Next() {
		var (
			r                      Record
			eventID, eventRank     sql.NullInt64
			eventPoint             sql.NullInt64
			chapterNo, chapterRank sql.NullInt64
			chapterPoint, score    sql.NullInt64
			accountID, liveID      sql.NullString
		)
		if err := rows.Scan(
			&r.ID, &r.TS, &eventID, &accountID, &liveID, &eventRank, &eventPoint,
			&chapterNo, &chapterRank, &chapterPoint, &score,
		); err != nil {
			return nil, fmt.Errorf("解析 live_records 失败: %w", err)
		}
		r.EventID = int64Ptr(eventID)
		r.AccountID = stringPtr(accountID)
		r.LiveID = stringPtr(liveID)
		r.EventRank = intPtr(eventRank)
		r.EventPoint = intPtr(eventPoint)
		r.WLChapterNo = intPtr(chapterNo)
		r.WLChapterRank = intPtr(chapterRank)
		r.WLChapterPoint = intPtr(chapterPoint)
		r.Score = intPtr(score)
		out = append(out, r)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("读取 live_records 失败: %w", err)
	}
	return out, nil
}

func int64Ptr(v sql.NullInt64) *int64 {
	if !v.Valid {
		return nil
	}
	n := v.Int64
	return &n
}

func intPtr(v sql.NullInt64) *int {
	if !v.Valid {
		return nil
	}
	n := int(v.Int64)
	return &n
}

func stringPtr(v sql.NullString) *string {
	if !v.Valid {
		return nil
	}
	s := v.String
	return &s
}

// ChapterNumbers 返回记录中出现过的有效 WL 章节号，保持升序且去重。
func ChapterNumbers(records []Record) []int {
	seen := make(map[int]struct{})
	for _, record := range records {
		if record.WLChapterNo != nil && *record.WLChapterNo > 0 {
			seen[*record.WLChapterNo] = struct{}{}
		}
	}
	out := make([]int, 0, len(seen))
	for chapterNo := range seen {
		out = append(out, chapterNo)
	}
	sort.Ints(out)
	return out
}
