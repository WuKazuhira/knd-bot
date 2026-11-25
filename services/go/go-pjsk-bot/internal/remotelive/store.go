package remotelive

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"unicode"

	_ "modernc.org/sqlite"
)

// 指针字段区分 SQL NULL 和 0
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

type Store struct {
	root string
}

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

// 过滤路径特殊字符
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
	// 只读打开，配合 WAL 读一致快照
	db, err := sql.Open("sqlite", "file:"+path+"?mode=ro")
	if err != nil {
		return nil, fmt.Errorf("打开 remote_live 数据库失败: %w", err)
	}
	return db, nil
}

var requiredColumns = []string{
	"id", "ts", "event_id", "account_id", "live_id", "event_rank", "event_point",
	"wl_chapter_no", "wl_chapter_rank", "wl_chapter_point", "score",
}

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
