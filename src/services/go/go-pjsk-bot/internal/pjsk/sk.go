package pjsk

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/skforecast"
	"github.com/kazuhira/go-pjsk-bot/internal/skranking"
	"github.com/kazuhira/go-pjsk-bot/internal/skstore"
)

// SkModule 实现 sk 时速（sks/时速/日速/半日速）、排名线（skl）与活动预测（sk预测/ycx）查询。
//
// 榜线时序数据由 go-pjsk-helper 采集写入 sqlite，本模块只读并计算展示；
// 预测缓存 JSON 由 Python 定时任务生成，Go 侧只读取展示（非曲线/非WL 表格模式）。
// WL 分榜、ycx 曲线、sk 查榜档位分数作为后续增量。
type SkModule struct {
	md       *masterdata.Loader
	store    *skstore.Store
	draw     *draw.Client
	forecast *skforecast.Reader
}

// NewSkModule 创建 sk 模块。forecast 可为 nil（无数据目录时禁用预测指令）。
func NewSkModule(md *masterdata.Loader, store *skstore.Store, d *draw.Client, forecast *skforecast.Reader) *SkModule {
	return &SkModule{md: md, store: store, draw: d, forecast: forecast}
}

// Register 注册时速、排名线与预测指令。
func (m *SkModule) Register(r *router.Router) {
	r.Register("sks", []string{"时速", "sk时速", "日速", "sk日速", "半日速", "sk半日速"}, m.handleSpeed)
	r.Register("skl", []string{"排名线", "sk排名线", "sk线"}, m.handleLine)
	if m.forecast != nil {
		r.Register("sk预测", []string{"活动预测", "skp"}, m.handleForecast)
	}
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

// handleForecast 实现 sk预测/活动预测/skp（表格模式）：读取本地预测缓存 JSON +
// 实时榜线，出 sk_forecast 图。曲线(ycx曲线)与 WL 分榜预测仍由 Python 承担。
func (m *SkModule) handleForecast(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	region := serverCode(server)

	events, err := m.md.Load("events.json", server)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	eventID := currentEventID(events, time.Now().UnixMilli())
	// 纯数字参数作为指定活动 ID（表格模式）。
	if arg := strings.TrimSpace(req.Arg); arg != "" {
		if n, e := strconv.Atoi(arg); e == nil && n > 0 {
			eventID = n
		}
	}
	if eventID == 0 {
		return onebot.ReplyText(req.Event, "未找到可查询的活动", false)
	}

	eventName := eventNameByID(events, eventID)

	forecasts := m.forecast.ReadCached(region, eventID)
	if len(forecasts) == 0 {
		return onebot.ReplyText(req.Event,
			fmt.Sprintf("%s 活动 %d 暂无可用预测数据（本地缓存缺失，请稍后再试或用 Python 侧实时获取）",
				strings.ToUpper(region), eventID), false)
	}

	displayRanks := skforecast.DisplayRanks(forecasts)
	liveScores, liveSpeeds := m.liveRankData(ctx, region, eventID, displayRanks)

	img, err := m.draw.Render(ctx, "sk_forecast", map[string]any{
		"region":        region,
		"event_id":      eventID,
		"event_name":    eventName,
		"forecasts":     forecasts,
		"live_scores":   liveScores,
		"live_speeds":   liveSpeeds,
		"display_ranks": displayRanks,
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}

// liveRankData 取各档位最新分数与近1小时时速（万/h），对齐 _get_live_rank_data。
// 返回以 rank 字符串为键的 map（供 pjsk-draw 载荷；无历史基准的档位 speed 为 nil）。
func (m *SkModule) liveRankData(ctx context.Context, region string, eventID int, ranks []int) (map[string]any, map[string]any) {
	scores := map[string]any{}
	speeds := map[string]any{}
	if len(ranks) == 0 {
		return scores, speeds
	}
	latest, err := m.store.QueryLatestRanking(ctx, region, eventID, ranks)
	if err != nil || len(latest) == 0 {
		return scores, speeds
	}
	older, _ := m.store.QueryFirstRankingAfter(ctx, region, eventID, time.Now().Add(-time.Hour), ranks)
	for _, row := range latest {
		key := strconv.Itoa(row.Rank)
		scores[key] = row.Score
		var speed any = nil
		for _, o := range older {
			if o.Rank == row.Rank {
				dt := row.Time.Sub(o.Time).Seconds()
				if dt > 0 {
					speed = float64(row.Score-o.Score) * 3600 / dt / 10000
				}
				break
			}
		}
		speeds[key] = speed
	}
	return scores, speeds
}

// eventNameByID 按活动 id 返回名称，未找到返回 "Event {id}"。
func eventNameByID(events []map[string]any, eventID int) string {
	for _, ev := range events {
		if intField(ev, "id") == eventID {
			if name := strField(ev, "name"); name != "" {
				return name
			}
		}
	}
	return fmt.Sprintf("Event %d", eventID)
}
