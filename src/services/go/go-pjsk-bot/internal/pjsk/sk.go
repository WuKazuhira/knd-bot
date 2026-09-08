package pjsk

import (
	"context"
	"fmt"
	"regexp"
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
	"github.com/kazuhira/go-pjsk-bot/internal/store"
)

// WL 活动 ID 编码系数，对齐 WL_EVENT_ID_FACTOR。
const wlEventIDFactor = 1000

// SkModule 实现 sk 时速（sks/时速/日速/半日速）、排名线（skl）、活动预测（sk预测/ycx）
// 与查房（cf/查房/sk）查询。
//
// 榜线时序数据由 go-pjsk-helper 采集写入 sqlite，本模块只读并计算展示；
// 预测缓存 JSON 由 Python 定时任务生成，Go 侧只读取展示（非曲线/非WL 表格模式）。
// WL 分榜快捷指令（wlsk 等复杂章节/角色解析）作为后续增量。
type SkModule struct {
	md       *masterdata.Loader
	store    *skstore.Store
	draw     *draw.Client
	forecast *skforecast.Reader
	bind     *store.Store
}

// NewSkModule 创建 sk 模块。forecast/bind 可为 nil（缺失时禁用对应指令分支）。
func NewSkModule(md *masterdata.Loader, s *skstore.Store, d *draw.Client, forecast *skforecast.Reader, bind *store.Store) *SkModule {
	return &SkModule{md: md, store: s, draw: d, forecast: forecast, bind: bind}
}

// Register 注册时速、排名线、预测与查房指令。
func (m *SkModule) Register(r *router.Router) {
	r.Register("sks", []string{"时速", "sk时速", "日速", "sk日速", "半日速", "sk半日速"}, m.handleSpeed)
	r.Register("skl", []string{"排名线", "sk排名线", "sk线"}, m.handleLine)
	if m.forecast != nil {
		r.Register("sk预测", []string{"活动预测", "skp"}, m.handleForecast)
	}
	// cf/查房/sk：查房信息（范围/多排名/单排名/ID/绑定账号）。
	r.Register("cf", []string{"查房"}, m.handleCf)
	r.Register("sk", nil, m.handleCf)
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

var (
	reCfRange     = regexp.MustCompile(`^(\d+)-(\d+)$`)
	reCfMultiRank = regexp.MustCompile(`^\d+(?:\s+\d+)+$`)
)

// handleCf 实现 cf/查房/sk：范围/多排名 → sk_cf_range；单排名(≤100)/ID → sk_cf 单人图；
// 空参数 → 查绑定账号。WL 分榜章节统计在单人图中一并展示（非 WL 活动为空）。
func (m *SkModule) handleCf(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	region := serverCode(server)

	events, err := m.md.Load("events.json", server)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	currentID := currentEventID(events, time.Now().UnixMilli())
	if currentID == 0 {
		return onebot.ReplyText(req.Event, "当前没有进行中的活动", false)
	}

	arg := strings.TrimSpace(req.Arg)
	// WL 分榜查询（cf wl / cf wl2 / cf wl角色）由 Python 的 wlsk 指令处理。
	if hasWLToken(arg) {
		return onebot.ReplyText(req.Event, "WL 分榜查房请使用 wlsk 指令（如 wlsk 100、wlsk2 100）", false)
	}
	// 空参数：用绑定账号查询自己。
	if arg == "" {
		if m.bind == nil {
			return onebot.ReplyText(req.Event, "请提供排名或由绑定的账号查询！", false)
		}
		uid, _, exists, berr := m.bind.GetUserBind(ctx, req.Event.UserID, server)
		if berr != nil || !exists || uid == 0 {
			return onebot.ReplyText(req.Event, "请提供排名或由绑定的账号查询！", false)
		}
		return m.cfSingle(ctx, req, region, currentID, itoa64(uid))
	}

	// 范围查询 1-10
	if mm := reCfRange.FindStringSubmatch(arg); mm != nil {
		start, _ := strconv.Atoi(mm[1])
		end, _ := strconv.Atoi(mm[2])
		ranks := make([]int, 0, end-start+1)
		for r := start; r <= end; r++ {
			ranks = append(ranks, r)
		}
		return m.cfRange(ctx, req, region, currentID, ranks)
	}
	// 多排名 1 2 3
	if reCfMultiRank.MatchString(arg) {
		fields := strings.Fields(arg)
		ranks := make([]int, 0, len(fields))
		for _, f := range fields {
			n, _ := strconv.Atoi(f)
			ranks = append(ranks, n)
		}
		return m.cfRange(ctx, req, region, currentID, ranks)
	}

	// 单个数字：≤100 视为排名，否则视为玩家 ID。
	if n, e := strconv.Atoi(arg); e == nil {
		if n <= 100 {
			latest, lerr := m.store.QueryLatestRanking(ctx, region, currentID, []int{n})
			if lerr != nil {
				return onebot.ReplyText(req.Event, errBug, false)
			}
			if len(latest) == 0 {
				return onebot.ReplyText(req.Event, fmt.Sprintf("没有排名%d的数据", n), false)
			}
			return m.cfSingle(ctx, req, region, currentID, latest[0].UID)
		}
		return m.cfSingle(ctx, req, region, currentID, arg)
	}

	return onebot.ReplyText(req.Event,
		"请输入有效的排名（1-100）、多个排名（如1 2 3）、范围（如1-10）或玩家ID", false)
}

// hasWLToken 判断参数是否包含 WL 分榜语法（wl/wl2/wl角色/-c），这类查询
// 仍由 Python 的 wlsk 指令处理。
func hasWLToken(arg string) bool {
	for _, tok := range strings.Fields(strings.ToLower(arg)) {
		if tok == "wl" || tok == "-c" || strings.HasPrefix(tok, "wl") {
			return true
		}
	}
	return false
}

// cfRange 查询多个排名的查房数据，出 sk_cf_range 图。
func (m *SkModule) cfRange(ctx context.Context, req router.Request, region string, eventID int, ranks []int) *onebot.ActionRequest {
	if len(ranks) > 20 {
		return onebot.ReplyText(req.Event, "最多查20个人哦", true)
	}
	list := make([]map[string]any, 0, len(ranks))
	for _, rank := range ranks {
		latest, err := m.store.QueryLatestRanking(ctx, region, eventID, []int{rank})
		if err != nil || len(latest) == 0 {
			continue
		}
		uid := latest[0].UID
		history, err := m.store.QueryRankingByUID(ctx, region, eventID, uid)
		if err != nil || len(history) == 0 {
			continue
		}
		latestRec := history[len(history)-1]
		stats := skranking.BuildActivityStats(history, latestRec)
		list = append(list, map[string]any{
			"rank":         rank,
			"name":         latest[0].Name,
			"uid":          uid,
			"score":        latestRec.Score,
			"hourly_speed": stats.HourlySpeed,
			"play_count":   stats.PlayCount,
			"is_playing":   stats.IsPlaying,
		})
	}
	if len(list) == 0 {
		return onebot.ReplyText(req.Event, "没有查到查房数据", false)
	}
	img, err := m.draw.Render(ctx, "sk_cf_range", map[string]any{"cf_data_list": list})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}

// cfSingle 查询单个玩家（按 uid）的查房数据，出 sk_cf 单人图（含 WL 章节统计）。
func (m *SkModule) cfSingle(ctx context.Context, req router.Request, region string, eventID int, uid string) *onebot.ActionRequest {
	server := int(req.Server)
	history, err := m.store.QueryRankingByUID(ctx, region, eventID, uid)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	if len(history) == 0 {
		return onebot.ReplyText(req.Event, "没有该玩家的榜线历史记录（可能未进入记录的档线范围内）", false)
	}
	latest := history[len(history)-1]
	stats := skranking.BuildActivityStats(history, latest)

	// WL 章节统计（非 WL 活动为空）。
	baseEventID := eventID % wlEventIDFactor
	if eventID < wlEventIDFactor {
		baseEventID = eventID
	}
	var wlChapterStats []map[string]any
	for _, chapter := range m.wlChapters(server, baseEventID) {
		chapterNo := intField(chapter, "chapterNo")
		encodedID := chapterNo*wlEventIDFactor + baseEventID
		chHistory, _ := m.store.QueryRankingByUID(ctx, region, encodedID, uid)
		if len(chHistory) == 0 {
			wlChapterStats = append(wlChapterStats, map[string]any{
				"chapter_no": chapterNo,
				"cid":        intField(chapter, "gameCharacterId"),
			})
			continue
		}
		chLatest := chHistory[len(chHistory)-1]
		chStats := skranking.BuildActivityStats(chHistory, chLatest)
		wlChapterStats = append(wlChapterStats, map[string]any{
			"chapter_no":   chapterNo,
			"cid":          intField(chapter, "gameCharacterId"),
			"rank":         chLatest.Rank,
			"score":        chLatest.Score,
			"hourly_speed": chStats.HourlySpeed,
		})
	}

	var stopSeconds any = nil
	if stats.StopDuration != nil {
		stopSeconds = stats.StopDuration.Seconds()
	}
	var chapterStatsPayload any = nil
	if len(wlChapterStats) > 0 {
		chapterStatsPayload = wlChapterStats
	}

	img, err := m.draw.Render(ctx, "sk_cf", map[string]any{
		"name":             latest.Name,
		"uid":              latest.UID,
		"score":            latest.Score,
		"rank":             latest.Rank,
		"hourly_speed":     stats.HourlySpeed,
		"twenty_min_speed": stats.TwentyMinSpeed,
		"play_count":       stats.PlayCount,
		"avg_pt":           stats.AvgPt,
		"last_pt":          stats.LastPt,
		"is_playing":       stats.IsPlaying,
		"stop_seconds":     stopSeconds,
		"wl_chapter_stats": chapterStatsPayload,
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}

// wlChapters 返回某活动的 WL 章节（按 chapterNo 升序），非 WL 活动返回空。
// 对齐 _get_wl_chapters。
func (m *SkModule) wlChapters(server, baseEventID int) []map[string]any {
	chapters, err := m.md.Load("worldBlooms.json", server)
	if err != nil {
		return nil
	}
	var out []map[string]any
	for _, c := range chapters {
		if intField(c, "eventId") == baseEventID {
			out = append(out, c)
		}
	}
	sortByChapterNo(out)
	return out
}

// sortByChapterNo 按 chapterNo 升序排序。
func sortByChapterNo(chapters []map[string]any) {
	for i := 1; i < len(chapters); i++ {
		for j := i; j > 0 && intField(chapters[j], "chapterNo") < intField(chapters[j-1], "chapterNo"); j-- {
			chapters[j], chapters[j-1] = chapters[j-1], chapters[j]
		}
	}
}
