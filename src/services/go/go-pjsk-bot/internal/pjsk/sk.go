package pjsk

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/skranking"
	"github.com/kazuhira/go-pjsk-bot/internal/skstore"
)

// SkModule 实现 sk 时速（sks/时速/日速/半日速）与排名线（skl）查询。
//
// 榜线时序数据由 go-pjsk-helper 采集写入 sqlite，本模块只读并计算展示。
// WL 分榜、sk 查榜档位分数、预测(ycx) 作为后续增量。
type SkModule struct {
	md    *masterdata.Loader
	store *skstore.Store
	draw  *draw.Client
}

// NewSkModule 创建 sk 模块。
func NewSkModule(md *masterdata.Loader, store *skstore.Store, d *draw.Client) *SkModule {
	return &SkModule{md: md, store: store, draw: d}
}

// Register 注册时速与排名线指令。
func (m *SkModule) Register(r *router.Router) {
	r.Register("sks", []string{"时速", "sk时速", "日速", "sk日速", "半日速", "sk半日速"}, m.handleSpeed)
	r.Register("skl", []string{"排名线", "sk排名线", "sk线"}, m.handleLine)
}

// speedPeriod 由指令名推断时速周期。
func speedPeriod(rawCmd string) (periodHours int, header, unit, title string) {
	switch {
	case strings.Contains(rawCmd, "半日速"):
		return 12, "半日速", "万/半日", "近12小时半日速"
	case strings.Contains(rawCmd, "日速"):
		return 24, "日速", "万/日", "近24小时日速"
	default:
		return 1, "时速", "万/h", "近1小时时速"
	}
}

func (m *SkModule) handleSpeed(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	region := serverCode(server)

	events, err := m.md.Load("events.json", server)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	eventID := currentEventID(events, time.Now().UnixMilli())
	if eventID == 0 {
		return onebot.ReplyText(req.Event, "当前没有进行中的活动", false)
	}

	ranks := skranking.ParseRankArgs(req.Arg, skranking.RankLevels, 20)
	if ranks == nil {
		return onebot.ReplyText(req.Event, "请输入有效的排名（如1000、100 1000或100-110）", false)
	}

	periodHours, header, unit, title := speedPeriod(strings.ToLower(req.RawCmd))
	periodSeconds := periodHours * 3600

	now := time.Now()
	older, err := m.store.QueryFirstRankingAfter(ctx, region, eventID, now.Add(-time.Duration(periodHours)*time.Hour), ranks)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	latest, err := m.store.QueryLatestRanking(ctx, region, eventID, ranks)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	if len(older) == 0 || len(latest) == 0 {
		return onebot.ReplyText(req.Event, fmt.Sprintf("缺少足够的历史数据计算%s！", header), false)
	}

	rows := skranking.BuildRankTableData(latest, older, periodSeconds)
	updateMinutesAgo := int(now.Sub(latest[0].Time).Minutes())

	img, err := m.draw.Render(ctx, "sk_rank_table", map[string]any{
		"title":              fmt.Sprintf("【%s-%d】%s", strings.ToUpper(region), eventID, title),
		"ranks_data":         rows,
		"update_minutes_ago": updateMinutesAgo,
		"speed_header":       header,
		"speed_unit":         unit,
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}

// handleLine 排名线：展示各档位当前分数（不含时速）。
func (m *SkModule) handleLine(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	region := serverCode(server)

	events, err := m.md.Load("events.json", server)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	eventID := currentEventID(events, time.Now().UnixMilli())
	if eventID == 0 {
		return onebot.ReplyText(req.Event, "当前没有进行中的活动", false)
	}

	ranks := skranking.ParseRankArgs(req.Arg, skranking.RankLevels, 20)
	if ranks == nil {
		return onebot.ReplyText(req.Event, "请输入有效的排名（如1000、100 1000或100-110）", false)
	}

	latest, err := m.store.QueryLatestRanking(ctx, region, eventID, ranks)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	if len(latest) == 0 {
		return onebot.ReplyText(req.Event, "缺少榜线数据！", false)
	}

	rows := make([]skranking.RankTableRow, 0, len(latest))
	for _, r := range latest {
		rows = append(rows, skranking.RankTableRow{Rank: r.Rank, Score: r.Score})
	}
	now := time.Now()
	updateMinutesAgo := int(now.Sub(latest[0].Time).Minutes())

	img, err := m.draw.Render(ctx, "sk_rank_table", map[string]any{
		"title":              fmt.Sprintf("【%s-%d】排名线", strings.ToUpper(region), eventID),
		"ranks_data":         rows,
		"update_minutes_ago": updateMinutesAgo,
		"speed_header":       "分数",
		"speed_unit":         "",
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}
