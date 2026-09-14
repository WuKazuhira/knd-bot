package pjsk

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
)

// QueryRefreshKind 表示需要刷新的查询数据集合。
type QueryRefreshKind string

const (
	QueryRefreshCards  QueryRefreshKind = "cards"
	QueryRefreshEvents QueryRefreshKind = "events"
)

var queryRefreshFiles = map[QueryRefreshKind][]string{
	QueryRefreshCards: {
		"cards.json",
		"eventCards.json",
		"cardCostume3ds.json",
		"costume3ds.json",
		"skills.json",
		"gameCharacters.json",
		"cardSupplies.json",
		"gameCharacterUnits.json",
	},
	QueryRefreshEvents: {
		"events.json",
		"eventCards.json",
		"eventDeckBonuses.json",
		"gameCharacterUnits.json",
		"cards.json",
		"cheerfulCarnivalTeams.json",
	},
}

// QueryRefresher 负责查卡/查活动的显式远端刷新。
type QueryRefresher struct {
	md     *masterdata.Loader
	draw   *draw.Client
	helper string
	http   *http.Client
}

// NewQueryRefresher 创建查询刷新器。
func NewQueryRefresher(md *masterdata.Loader, d *draw.Client, helperURL string) *QueryRefresher {
	return &QueryRefresher{
		md:     md,
		draw:   d,
		helper: strings.TrimRight(strings.TrimSpace(helperURL), "/"),
		http:   &http.Client{Timeout: 90 * time.Second},
	}
}

// Refresh 远端刷新主数据，失效 Go 缓存，并清理 pjsk-draw 运行缓存。
func (r *QueryRefresher) Refresh(ctx context.Context, server int, kind QueryRefreshKind) error {
	if r == nil {
		return fmt.Errorf("query refresher is nil")
	}
	files, ok := queryRefreshFiles[kind]
	if !ok {
		return fmt.Errorf("unknown query refresh kind %q", kind)
	}
	if r.helper == "" {
		return fmt.Errorf("PJSK_HELPER_URL 未配置，无法刷新主数据")
	}
	if r.md == nil {
		return fmt.Errorf("masterdata loader is nil")
	}
	if r.draw == nil {
		return fmt.Errorf("draw client is nil")
	}
	if ctx == nil {
		ctx = context.Background()
	}
	refreshCtx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()

	for _, file := range files {
		if err := r.refreshFile(refreshCtx, server, file); err != nil {
			return fmt.Errorf("刷新 %s 失败: %w", file, err)
		}
	}
	r.md.Invalidate(server, files...)
	if err := r.draw.ClearCache(refreshCtx); err != nil {
		return fmt.Errorf("刷新缓存失败: %w", err)
	}
	return nil
}

func (r *QueryRefresher) refreshFile(ctx context.Context, server int, file string) error {
	endpoint := r.helper + "/masterdata/refresh?region=" + url.QueryEscape(serverCode(server)) + "&file=" + url.QueryEscape(file)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, nil)
	if err != nil {
		return err
	}
	resp, err := r.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		return fmt.Errorf("helper HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(body)))
	}
	return nil
}
