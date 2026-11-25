package pjsk

import (
	"context"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/cards"
	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/skforecast"
	"github.com/kazuhira/go-pjsk-bot/internal/skranking"
	"github.com/kazuhira/go-pjsk-bot/internal/skstore"
	"github.com/kazuhira/go-pjsk-bot/internal/sksub"
	"github.com/kazuhira/go-pjsk-bot/internal/store"
)

// WL 章节活动 ID = chapterNo*1000 + baseEventID
const wlEventIDFactor = 1000

func baseEventID(eventID int) int {
	if eventID >= wlEventIDFactor {
		return eventID % wlEventIDFactor
	}
	return eventID
}

func isWorldBloomEvent(events []map[string]any, eventID int) bool {
	baseID := baseEventID(eventID)
	for _, event := range events {
		if intField(event, "id") == baseID {
			return strField(event, "eventType") == "world_bloom"
		}
	}
	return false
}

func loadWorldBloomChapters(md *masterdata.Loader, server, eventID int) []map[string]any {
	if md == nil {
		return nil
	}
	events, err := md.Load("events.json", server)
	if err != nil || !isWorldBloomEvent(events, eventID) {
		return nil
	}
	raw, err := md.Load("worldBlooms.json", server)
	if err != nil {
		return nil
	}
	baseID := baseEventID(eventID)
	chapters := make([]map[string]any, 0)
	for _, chapter := range raw {
		if intField(chapter, "eventId") == baseID {
			chapters = append(chapters, chapter)
		}
	}
	sortByChapterNo(chapters)
	return chapters
}

func shouldRenderWLRankTable(rawCmd string, chapter map[string]any) bool {
	return isWLShortcut(rawCmd) || chapter == nil
}

func rankLevelsFrom(min int) []int {
	levels := make([]int, 0, len(skranking.RankLevels))
	for _, rank := range skranking.RankLevels {
		if rank >= min {
			levels = append(levels, rank)
		}
	}
	return levels
}

type SkModule struct {
	md       *masterdata.Loader
	store    *skstore.Store
	draw     *draw.Client
	forecast *skforecast.Reader
	bind     *store.Store
	chara    *cards.CharaAliasResolver
	sub      *sksub.Store
	supers   map[int64]bool
}

func NewSkModule(md *masterdata.Loader, s *skstore.Store, d *draw.Client, forecast *skforecast.Reader, bind *store.Store, chara *cards.CharaAliasResolver, sub *sksub.Store, supers []int64) *SkModule {
	set := make(map[int64]bool, len(supers))
	for _, u := range supers {
		set[u] = true
	}
	return &SkModule{md: md, store: s, draw: d, forecast: forecast, bind: bind, chara: chara, sub: sub, supers: set}
}

func (m *SkModule) Register(r *router.Router) {
	r.RegisterNumericSuffix("sks", []string{"时速", "sk时速", "日速", "sk日速", "半日速", "sk半日速"}, m.handleSpeed)
	r.RegisterNumericSuffix("skl", []string{"排名线", "sk排名线", "sk线"}, m.handleLine)
	if m.forecast != nil {
		r.RegisterNumericSuffix("sk预测", []string{"活动预测", "skp", "ycx", "ycxall", "lsycx"}, m.handleForecast)
		r.RegisterNumericSuffix("ycx曲线", []string{"sk预测曲线", "活动预测曲线"}, m.handleForecastCurve)
	}
	r.RegisterNumericSuffix("cf", []string{"查房", "wlcf"}, m.handleCf)
	r.RegisterNumericSuffix("sk", nil, m.handleCf)
	r.RegisterNumericSuffix("csb", []string{"查水表"}, m.handleCsb)
	r.RegisterNumericSuffix("wlsk", []string{"wl查房"}, m.handleCf)
	r.RegisterNumericSuffix("wlcsb", []string{"wl查水表"}, m.handleCsb)
	r.RegisterNumericSuffix("wlsks", []string{"wl时速", "wlsk时速", "wl日速", "wlsk日速", "wl半日速", "wlsk半日速"}, m.handleWLSpeed)
	r.RegisterNumericSuffix("wlskl", []string{"wl排名线", "wlsk排名线", "wlsk线"}, m.handleWLLine)
	if m.sub != nil {
		r.Register("订阅sk", []string{"sk订阅", "订阅cnsk", "订阅twsk"}, m.handleSubscribe)
		r.Register("退订sk", []string{"取消订阅sk", "sk取消订阅", "sk退订", "取消订阅cnsk", "取消订阅twsk"}, m.handleUnsubscribe)
		r.Register("清空sk订阅", nil, m.handleClearSubscriptions)
	}
}

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

	arg := req.Arg
	titlePrefix := fmt.Sprintf("【%s-%d】", strings.ToUpper(region), eventID)
	var chapter map[string]any
	if wlID, rest, selected := m.resolveWLQueryEventID(server, req.Arg, eventID); selected != nil {
		eventID = wlID
		arg = rest
		chapter = selected
		titlePrefix = fmt.Sprintf("【%s-%d-第%d章单榜】", strings.ToUpper(region), eventID%wlEventIDFactor, intField(chapter, "chapterNo"))
	}

	ranks := skranking.ParseRankArgs(arg, skranking.RankLevels, 20)
	if ranks == nil {
		return onebot.ReplyText(req.Event, "请输入有效的排名（如1000、100 1000或100-110）", false)
	}

	periodHours, header, unit, title := speedPeriod(strings.ToLower(req.RawCmd))
	periodSeconds := periodHours * 3600

	if shouldRenderWLRankTable(req.RawCmd, chapter) && isWorldBloomEvent(events, eventID) {
		chapters := m.wlChaptersForEvents(server, events, eventID)
		if len(chapters) > 0 {
			return m.renderWLRankTable(ctx, req, region, baseEventID(eventID), chapters, ranks,
				periodHours, periodSeconds,
				fmt.Sprintf("【%s-%d】WL近%d小时%s", strings.ToUpper(region), baseEventID(eventID), periodHours, header),
				"speed", header, unit)
		}
	}

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
		"title":              titlePrefix + title,
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

	arg := req.Arg
	titlePrefix := fmt.Sprintf("【%s-%d】", strings.ToUpper(region), eventID)
	var chapter map[string]any
	if wlID, rest, selected := m.resolveWLQueryEventID(server, req.Arg, eventID); selected != nil {
		eventID = wlID
		arg = rest
		chapter = selected
		titlePrefix = fmt.Sprintf("【%s-%d-第%d章单榜】", strings.ToUpper(region), eventID%wlEventIDFactor, intField(chapter, "chapterNo"))
	}

	// skl 默认从 T50 开始查，显式传数字可查任意排名
	ranks := skranking.ParseRankArgs(arg, rankLevelsFrom(50), 20)
	if ranks == nil {
		return onebot.ReplyText(req.Event, "请输入有效的排名（如1000、100 1000或100-110）", false)
	}

	if shouldRenderWLRankTable(req.RawCmd, chapter) && isWorldBloomEvent(events, eventID) {
		chapters := m.wlChaptersForEvents(server, events, eventID)
		if len(chapters) > 0 {
			return m.renderWLRankTable(ctx, req, region, baseEventID(eventID), chapters, ranks,
				0, 3600,
				fmt.Sprintf("【%s-%d】WL排名线", strings.ToUpper(region), baseEventID(eventID)),
				"score", "分数", "")
		}
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
		"title":              titlePrefix + "排名线",
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

func (m *SkModule) handleForecast(ctx context.Context, req router.Request) *onebot.ActionRequest {
	// ycx/ycxall/lsycx 走当前活动预测曲线
	if legacy, _ := legacyForecastCurveMode(req.RawCmd); legacy {
		return m.handleForecastCurve(ctx, req)
	}
	server := int(req.Server)
	region := serverCode(server)

	events, err := m.md.Load("events.json", server)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	eventID := currentEventID(events, time.Now().UnixMilli())
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
	arg = m.injectDefaultWLChapter(req.RawCmd, arg, server, currentID)
	if wlID, rest, chapter := m.resolveWLQueryEventID(server, arg, currentID); chapter != nil {
		currentID = wlID
		arg = rest
	} else if hasWLToken(arg) {
		return onebot.ReplyText(req.Event, "WL 分榜查房请使用 wlsk 指令（如 wlsk 100、wlsk2 100）", false)
	}
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

	if ranks, tooMany, invalid := parseCfRankList(arg); tooMany {
		return onebot.ReplyText(req.Event, "最多查20个人哦", true)
	} else if invalid {
		return onebot.ReplyText(req.Event,
			"请输入有效的排名（1-100）、多个排名（如1 2 3）、范围（如1-10）或玩家ID", false)
	} else if len(ranks) > 0 {
		return m.cfRange(ctx, req, region, currentID, ranks)
	}

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

func hasWLToken(arg string) bool {
	for _, tok := range strings.Fields(strings.ToLower(arg)) {
		if tok == "wl" || tok == "-c" || strings.HasPrefix(tok, "wl") {
			return true
		}
	}
	return false
}

func isWLShortcut(rawCmd string) bool {
	c := strings.ToLower(rawCmd)
	return strings.HasPrefix(c, "wl") || strings.HasPrefix(c, "cnwl") || strings.HasPrefix(c, "twwl")
}

func (m *SkModule) injectDefaultWLChapter(rawCmd, arg string, server, currentID int) string {
	if !isWLShortcut(rawCmd) || hasWLToken(arg) {
		return arg
	}
	chapter := currentWLChapter(m.wlChaptersForEvent(server, currentID))
	if chapter == nil {
		return arg
	}
	prefix := "wl" + strconv.Itoa(intField(chapter, "chapterNo"))
	return strings.TrimSpace(prefix + " " + arg)
}

// 去重，防止榜单补采导致同一人占多个名次
func uniqueCfRankings(rankings []skranking.Ranking) []skranking.Ranking {
	seen := make(map[string]bool, len(rankings))
	out := make([]skranking.Ranking, 0, len(rankings))
	for _, ranking := range rankings {
		if seen[ranking.UID] {
			continue
		}
		seen[ranking.UID] = true
		out = append(out, ranking)
	}
	return out
}

func (m *SkModule) cfRange(ctx context.Context, req router.Request, region string, eventID int, ranks []int) *onebot.ActionRequest {
	if len(ranks) > 20 {
		return onebot.ReplyText(req.Event, "最多查20个人哦", true)
	}
	// 一次查完整个范围，避免跨批次读到不一致的名次
	latestRankings, err := m.store.QueryLatestRanking(ctx, region, eventID, ranks)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	list := make([]map[string]any, 0, len(latestRankings))
	for _, latest := range uniqueCfRankings(latestRankings) {
		uid := latest.UID
		history, preWindowMaxScore, err := m.store.QueryRankingTailByUID(ctx, region, eventID, uid)
		if err != nil || len(history) == 0 {
			continue
		}
		latestRec := history[len(history)-1]
		stats := skranking.BuildActivityStats(history, latestRec, preWindowMaxScore)
		list = append(list, map[string]any{
			"rank":         latest.Rank,
			"name":         latest.Name,
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

func (m *SkModule) cfSingle(ctx context.Context, req router.Request, region string, eventID int, uid string) *onebot.ActionRequest {
	server := int(req.Server)
	history, preWindowMaxScore, err := m.store.QueryRankingTailByUID(ctx, region, eventID, uid)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	if len(history) == 0 {
		return onebot.ReplyText(req.Event, "没有该玩家的榜线历史记录（可能未进入记录的档线范围内）", false)
	}
	latest := history[len(history)-1]
	stats := skranking.BuildActivityStats(history, latest, preWindowMaxScore)

	baseEventID := eventID % wlEventIDFactor
	if eventID < wlEventIDFactor {
		baseEventID = eventID
	}
	var wlChapterStats []map[string]any
	for _, chapter := range m.wlChaptersForEvent(server, baseEventID) {
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
		chStats := skranking.BuildActivityStats(chHistory, chLatest, 0)
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

func (m *SkModule) wlChapters(server, eventID int) []map[string]any {
	if m.md == nil {
		return nil
	}
	chapters, err := m.md.Load("worldBlooms.json", server)
	if err != nil {
		return nil
	}
	baseID := baseEventID(eventID)
	var out []map[string]any
	for _, c := range chapters {
		if intField(c, "eventId") == baseID {
			out = append(out, c)
		}
	}
	sortByChapterNo(out)
	return out
}

func (m *SkModule) wlChaptersForEvents(server int, events []map[string]any, eventID int) []map[string]any {
	if !isWorldBloomEvent(events, eventID) {
		return nil
	}
	return m.wlChapters(server, baseEventID(eventID))
}

func (m *SkModule) wlChaptersForEvent(server, eventID int) []map[string]any {
	if m.md == nil {
		return nil
	}
	events, err := m.md.Load("events.json", server)
	if err != nil {
		return nil
	}
	return m.wlChaptersForEvents(server, events, eventID)
}

func sortByChapterNo(chapters []map[string]any) {
	for i := 1; i < len(chapters); i++ {
		for j := i; j > 0 && intField(chapters[j], "chapterNo") < intField(chapters[j-1], "chapterNo"); j-- {
			chapters[j], chapters[j-1] = chapters[j-1], chapters[j]
		}
	}
}

var reWLChapterNum = regexp.MustCompile(`^wl(?:第)?(\d+)(?:章)?$`)
var reBareChapterNum = regexp.MustCompile(`^(?:第)?(\d+)(?:章)?$`)

func currentWLChapter(chapters []map[string]any) map[string]any {
	if len(chapters) == 0 {
		return nil
	}
	nowMS := time.Now().UnixMilli()
	var best map[string]any
	var bestStart int64 = -1
	for _, c := range chapters {
		start := int64(intField(c, "chapterStartAt"))
		if start <= nowMS && start > bestStart {
			bestStart = start
			best = c
		}
	}
	if best != nil {
		return best
	}
	return chapters[0]
}

func (m *SkModule) resolveWLQueryEventID(server int, args string, baseEventID int) (int, string, map[string]any) {
	chapters := m.wlChaptersForEvent(server, baseEventID)
	return resolveWLFromChapters(chapters, m.chara, args, baseEventID)
}

func resolveWLFromChapters(chapters []map[string]any, chara *cards.CharaAliasResolver, args string, baseEventID int) (int, string, map[string]any) {
	if len(chapters) == 0 {
		return baseEventID, strings.TrimSpace(args), nil
	}
	tokens := strings.Fields(args)

	byCID := func(cid int) map[string]any {
		if cid == 0 {
			return nil
		}
		for _, c := range chapters {
			if intField(c, "gameCharacterId") == cid {
				return c
			}
		}
		return nil
	}
	byChapterNo := func(no int) map[string]any {
		for _, c := range chapters {
			if intField(c, "chapterNo") == no {
				return c
			}
		}
		return nil
	}
	finish := func(chapter map[string]any, newTokens []string) (int, string, map[string]any) {
		if chapter == nil {
			return baseEventID, strings.TrimSpace(args), nil
		}
		encoded := intField(chapter, "chapterNo")*wlEventIDFactor + baseEventID
		return encoded, strings.TrimSpace(strings.Join(newTokens, " ")), chapter
	}
	removeAt := func(i int, n int) []string {
		out := make([]string, 0, len(tokens))
		out = append(out, tokens[:i]...)
		out = append(out, tokens[i+n:]...)
		return out
	}

	// wl2 / wl第2章 ；或 wl 2
	for i, tok := range tokens {
		tl := strings.ToLower(tok)
		if mm := reWLChapterNum.FindStringSubmatch(tl); mm != nil {
			no, _ := strconv.Atoi(mm[1])
			return finish(byChapterNo(no), removeAt(i, 1))
		}
		if tl == "wl" && i+1 < len(tokens) && reBareChapterNum.MatchString(tokens[i+1]) {
			mm := reBareChapterNum.FindStringSubmatch(tokens[i+1])
			no, _ := strconv.Atoi(mm[1])
			return finish(byChapterNo(no), removeAt(i, 2))
		}
	}
	// -c mfy / c mfy
	if chara != nil {
		for i := 0; i+1 < len(tokens); i++ {
			tl := strings.ToLower(tokens[i])
			if tl == "-c" || tl == "c" {
				if ch := byCID(chara.Resolve(strings.ToLower(tokens[i+1]))); ch != nil {
					return finish(ch, removeAt(i, 2))
				}
			}
		}
		// wl<nick> / wl <nick>
		for i, tok := range tokens {
			tl := strings.ToLower(tok)
			if strings.HasPrefix(tl, "wl") && len(tl) > 2 {
				if ch := byCID(chara.Resolve(tl[2:])); ch != nil {
					return finish(ch, removeAt(i, 1))
				}
			}
			if tl == "wl" && i+1 < len(tokens) {
				if ch := byCID(chara.Resolve(strings.ToLower(tokens[i+1]))); ch != nil {
					return finish(ch, removeAt(i, 2))
				}
			}
		}
	}
	// 裸 wl → 当前章节
	for i, tok := range tokens {
		if strings.ToLower(tok) == "wl" {
			return finish(currentWLChapter(chapters), removeAt(i, 1))
		}
	}
	return baseEventID, strings.TrimSpace(args), nil
}

func parseCfRankList(arg string) (ranks []int, tooMany bool, invalid bool) {
	if mm := reCfRange.FindStringSubmatch(arg); mm != nil {
		start, _ := strconv.Atoi(mm[1])
		end, _ := strconv.Atoi(mm[2])
		if start < 1 || end < start || end > 100 {
			return nil, false, true
		}
		if end-start+1 > 20 {
			return nil, true, false
		}
		ranks = make([]int, 0, end-start+1)
		for r := start; r <= end; r++ {
			ranks = append(ranks, r)
		}
		return ranks, false, false
	}
	if reCfMultiRank.MatchString(arg) {
		fields := strings.Fields(arg)
		if len(fields) > 20 {
			return nil, true, false
		}
		ranks = make([]int, 0, len(fields))
		for _, f := range fields {
			n, _ := strconv.Atoi(f)
			if n < 1 || n > 100 {
				return nil, false, true
			}
			ranks = append(ranks, n)
		}
		return ranks, false, false
	}
	return nil, false, false
}

func (m *SkModule) handleCsb(ctx context.Context, req router.Request) *onebot.ActionRequest {
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
	arg = m.injectDefaultWLChapter(req.RawCmd, arg, server, currentID)
	if wlID, rest, chapter := m.resolveWLQueryEventID(server, arg, currentID); chapter != nil {
		currentID = wlID
		arg = rest
	} else if hasWLToken(arg) {
		return onebot.ReplyText(req.Event, "WL 分榜查水表请使用 wlcsb 指令", false)
	}

	if ranks, tooMany, invalid := parseCfRankList(arg); tooMany {
		return onebot.ReplyText(req.Event, "最多查20个人哦", true)
	} else if invalid {
		return onebot.ReplyText(req.Event,
			"请输入有效的排名（1-100）、多个排名（如1 2 3）、范围（如1-10）或玩家ID", false)
	} else if len(ranks) > 0 {
		return m.csbRange(ctx, req, region, currentID, ranks)
	}

	uid, resp := m.resolveCfUID(ctx, req, region, currentID, arg)
	if resp != nil {
		return resp
	}

	img, errResp := m.renderCsbByUID(ctx, req, region, currentID, uid, true)
	if errResp != nil {
		return errResp
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}

func (m *SkModule) csbRange(ctx context.Context, req router.Request, region string, eventID int, ranks []int) *onebot.ActionRequest {
	latestRankings, err := m.store.QueryLatestRanking(ctx, region, eventID, ranks)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	targets := uniqueCfRankings(latestRankings)
	if len(targets) == 0 {
		return onebot.ReplyText(req.Event, "没有查到查水表数据", false)
	}
	message := make(onebot.Message, 0, len(targets))
	for _, latest := range targets {
		img, errResp := m.renderCsbByUID(ctx, req, region, eventID, latest.UID, false)
		if errResp != nil || len(img) == 0 {
			continue
		}
		message = append(message, onebot.ImageBytes(base64Encode(img)))
	}
	if len(message) == 0 {
		return onebot.ReplyText(req.Event, "没有查到查水表数据", false)
	}
	return onebot.SendMessageAction(req.Event, message)
}

func (m *SkModule) renderCsbByUID(ctx context.Context, req router.Request, region string, eventID int, uid string, strict bool) ([]byte, *onebot.ActionRequest) {
	history, err := m.store.QueryRankingByUID(ctx, region, eventID, uid)
	if err != nil {
		if strict {
			return nil, onebot.ReplyText(req.Event, errBug, false)
		}
		return nil, nil
	}
	if len(history) == 0 {
		if strict {
			return nil, onebot.ReplyText(req.Event, "没有该玩家的榜线历史记录（可能未进入记录的档线范围内）", false)
		}
		return nil, nil
	}
	latest := history[len(history)-1]

	startDate := history[0].Time
	type dayHour struct{ day, hour int }
	counts := map[dayHour]int{}
	for i := 0; i+1 < len(history); i++ {
		if history[i+1].Score > history[i].Score {
			day := daysBetween(startDate, history[i+1].Time)
			key := dayHour{day, history[i+1].Time.Hour()}
			counts[key]++
		}
	}
	hourlyCounts := make([][3]int, 0, len(counts))
	for k, c := range counts {
		hourlyCounts = append(hourlyCounts, [3]int{k.day, k.hour, c})
	}

	stopPeriods := computeStopPeriods(history)

	img, err := m.draw.Render(ctx, "sk_csb", map[string]any{
		"latest_name":     latest.Name,
		"latest_uid":      latest.UID,
		"latest_rank":     latest.Rank,
		"latest_score":    latest.Score,
		"hourly_counts":   hourlyCounts,
		"start_date":      startDate.Format("2006-01-02"),
		"update_time_str": latest.Time.Format("01-02 15:04:05"),
		"stop_periods":    stopPeriods,
	})
	if err != nil {
		if strict {
			return nil, onebot.ReplyText(req.Event, errBug, false)
		}
		return nil, nil
	}
	return img, nil
}

func (m *SkModule) resolveCfUID(ctx context.Context, req router.Request, region string, eventID int, arg string) (string, *onebot.ActionRequest) {
	server := int(req.Server)
	if arg == "" {
		if m.bind == nil {
			return "", onebot.ReplyText(req.Event, "请提供排名或由绑定的账号查询！", false)
		}
		uid, _, exists, berr := m.bind.GetUserBind(ctx, req.Event.UserID, server)
		if berr != nil || !exists || uid == 0 {
			return "", onebot.ReplyText(req.Event, "请提供排名或由绑定的账号查询！", false)
		}
		return itoa64(uid), nil
	}
	n, e := strconv.Atoi(arg)
	if e != nil {
		return "", onebot.ReplyText(req.Event, "请输入有效的排名（1-100）、多个排名（如1 2 3）、范围（如1-10）或玩家ID", false)
	}
	if n <= 100 {
		latest, lerr := m.store.QueryLatestRanking(ctx, region, eventID, []int{n})
		if lerr != nil {
			return "", onebot.ReplyText(req.Event, errBug, false)
		}
		if len(latest) == 0 {
			return "", onebot.ReplyText(req.Event, fmt.Sprintf("没有排名%d的数据", n), false)
		}
		return latest[0].UID, nil
	}
	return arg, nil
}

func daysBetween(a, b time.Time) int {
	ay, am, ad := a.Date()
	by, bm, bd := b.Date()
	da := time.Date(ay, am, ad, 0, 0, 0, 0, a.Location())
	db := time.Date(by, bm, bd, 0, 0, 0, 0, b.Location())
	return int(db.Sub(da).Hours() / 24)
}

func computeStopPeriods(history []skranking.Ranking) []map[string]any {
	var periods []map[string]any
	var l, r *skranking.Ranking
	appendIf := func(l, r *skranking.Ranking) {
		if l == nil || r == nil || l == r {
			return
		}
		mins := int(r.Time.Sub(l.Time).Minutes())
		if mins >= 5 {
			periods = append(periods, map[string]any{
				"start":   l.Time.Format(time.RFC3339),
				"end":     r.Time.Format(time.RFC3339),
				"minutes": mins,
			})
		}
	}
	for i := range history {
		rec := &history[i]
		if l == nil {
			l = rec
		}
		if r == nil {
			r = rec
		}
		if rec.Score != r.Score {
			appendIf(l, r)
			l, r = rec, nil
		} else {
			r = rec
		}
	}
	appendIf(l, r)
	return periods
}

func (m *SkModule) renderWLRankTable(ctx context.Context, req router.Request, region string, baseID int, chapters []map[string]any, ranks []int, periodHours, periodSeconds int, title, valueMode, valueHeader, valueUnit string) *onebot.ActionRequest {
	rows, updateMinutesAgo := m.wlRankTableRows(ctx, region, baseID, chapters, ranks, periodHours, periodSeconds)
	if len(rows) == 0 {
		if valueMode == "speed" {
			return onebot.ReplyText(req.Event, fmt.Sprintf("缺少足够的历史数据计算%s！", valueHeader), false)
		}
		return onebot.ReplyText(req.Event, "缺少榜线数据！", false)
	}
	img, err := m.draw.Render(ctx, "sk_wl_rank_table", map[string]any{
		"title":              title,
		"chapters":           chapters,
		"rows":               rows,
		"update_minutes_ago": updateMinutesAgo,
		"value_mode":         valueMode,
		"value_header":       valueHeader,
		"value_unit":         valueUnit,
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}

func (m *SkModule) handleWLSpeed(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	region := serverCode(server)
	events, err := m.md.Load("events.json", server)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	baseEventID := currentEventID(events, time.Now().UnixMilli())
	if baseEventID == 0 {
		return onebot.ReplyText(req.Event, "当前没有进行中的活动", false)
	}

	// WL 快捷命令带章节参数也照旧出合并榜表，这里只把排名参数拆出来
	_, arg, _ := m.resolveWLQueryEventID(server, req.Arg, baseEventID)
	chapters := m.wlChaptersForEvents(server, events, baseEventID)
	if len(chapters) == 0 {
		return onebot.ReplyText(req.Event, "当前活动不是 World Link 活动", false)
	}
	ranks := skranking.ParseRankArgs(strings.TrimSpace(arg), skranking.RankLevels, 20)
	if ranks == nil {
		return onebot.ReplyText(req.Event, "请输入有效的排名（如1000、100 1000或100-110）", false)
	}
	periodHours, header, unit, _ := speedPeriod(strings.ToLower(req.RawCmd))
	return m.renderWLRankTable(ctx, req, region, baseEventID, chapters, ranks,
		periodHours, periodHours*3600,
		fmt.Sprintf("【%s-%d】WL近%d小时%s", strings.ToUpper(region), baseEventID, periodHours, header),
		"speed", header, unit)
}

func (m *SkModule) handleWLLine(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	region := serverCode(server)
	events, err := m.md.Load("events.json", server)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	baseEventID := currentEventID(events, time.Now().UnixMilli())
	if baseEventID == 0 {
		return onebot.ReplyText(req.Event, "当前没有进行中的活动", false)
	}

	_, arg, _ := m.resolveWLQueryEventID(server, req.Arg, baseEventID)
	chapters := m.wlChaptersForEvents(server, events, baseEventID)
	if len(chapters) == 0 {
		return onebot.ReplyText(req.Event, "当前活动不是 World Link 活动", false)
	}
	ranks := skranking.ParseRankArgs(strings.TrimSpace(arg), rankLevelsFrom(50), 20)
	if ranks == nil {
		return onebot.ReplyText(req.Event, "请输入有效的排名（如1000、100 1000或100-110）", false)
	}
	return m.renderWLRankTable(ctx, req, region, baseEventID, chapters, ranks,
		0, 3600,
		fmt.Sprintf("【%s-%d】WL排名线", strings.ToUpper(region), baseEventID),
		"score", "分数", "")
}

func (m *SkModule) wlRankTableRows(ctx context.Context, region string, baseEventID int, chapters []map[string]any, ranks []int, periodHours, periodSeconds int) ([]map[string]any, int) {
	type source struct {
		key     string
		eventID int
	}
	sources := []source{{"total", baseEventID}}
	for _, c := range chapters {
		chapterNo := intField(c, "chapterNo")
		sources = append(sources, source{fmt.Sprintf("chapter_%d", chapterNo), chapterNo*wlEventIDFactor + baseEventID})
	}

	now := time.Now()
	data := map[string]map[int]map[string]any{}
	updateMinutes := -1
	for _, s := range sources {
		latest, _ := m.store.QueryLatestRanking(ctx, region, s.eventID, ranks)
		var older []skranking.Ranking
		if periodHours > 0 {
			older, _ = m.store.QueryFirstRankingAfter(ctx, region, s.eventID, now.Add(-time.Duration(periodHours)*time.Hour), ranks)
		}
		olderMap := map[int]skranking.Ranking{}
		for _, o := range older {
			olderMap[o.Rank] = o
		}
		data[s.key] = map[int]map[string]any{}
		for _, row := range latest {
			var speed any = nil
			if periodHours > 0 {
				if o, ok := olderMap[row.Rank]; ok {
					speed = skranking.CalculateSpeed(row, &o, periodSeconds)
				} else {
					speed = skranking.CalculateSpeed(row, nil, periodSeconds)
				}
			}
			data[s.key][row.Rank] = map[string]any{"score": row.Score, "speed": speed}
			rowUpdate := int(now.Sub(row.Time).Minutes())
			if updateMinutes < 0 || rowUpdate < updateMinutes {
				updateMinutes = rowUpdate
			}
		}
	}

	var rows []map[string]any
	for _, rank := range ranks {
		total := data["total"][rank]
		chapterCells := map[string]any{}
		hasChapter := false
		for _, c := range chapters {
			chapterNo := intField(c, "chapterNo")
			cell := data[fmt.Sprintf("chapter_%d", chapterNo)][rank]
			chapterCells[strconv.Itoa(chapterNo)] = cell
			if cell != nil {
				hasChapter = true
			}
		}
		if total != nil || hasChapter {
			rows = append(rows, map[string]any{
				"rank":     rank,
				"total":    total,
				"chapters": chapterCells,
			})
		}
	}
	if updateMinutes < 0 {
		updateMinutes = 0
	}
	return rows, updateMinutes
}

func legacyForecastCurveMode(rawCmd string) (legacy, all bool) {
	name := strings.ToLower(rawCmd)
	if strings.HasPrefix(name, "cn") || strings.HasPrefix(name, "tw") {
		name = name[2:]
	}
	switch name {
	case "ycx":
		return true, false
	case "ycxall", "lsycx":
		return true, true
	default:
		return false, false
	}
}

// 旧 ycx 固定查当前活动，未知首数字当作占位符丢掉
func parseLegacyForecastCurveArgs(rawCmd, arg string, currentID int, availableRanks []int) (int, []int) {
	_, all := legacyForecastCurveMode(rawCmd)
	eventID := baseEventID(currentID)
	if all {
		return eventID, defaultForecastCurveRanks()
	}
	known := map[int]bool{}
	for _, ranks := range [][]int{skforecast.RankLevels, skforecast.LiveRanks, availableRanks} {
		for _, rank := range ranks {
			known[rank] = true
		}
	}
	nums := []int{}
	for _, token := range reDigits.FindAllString(arg, -1) {
		if n, err := strconv.Atoi(token); err == nil {
			nums = append(nums, n)
		}
	}
	if len(nums) == 0 || (len(nums) == 1 && !known[nums[0]]) {
		return eventID, defaultForecastCurveRanks()
	}
	if len(nums) > 1 && !known[nums[0]] {
		nums = nums[1:]
	}
	ranks := []int{}
	for _, rank := range nums {
		if rank > 0 {
			ranks = append(ranks, rank)
		}
	}
	if len(ranks) > 8 {
		ranks = ranks[:8]
	}
	if len(ranks) == 0 {
		ranks = defaultForecastCurveRanks()
	}
	return eventID, ranks
}

func defaultForecastCurveRanks() []int { return []int{100, 500, 1000, 5000, 10000} }

var reDigits = regexp.MustCompile(`\d+`)

func parseForecastCurveArgs(arg string, currentEventID int) (int, []int) {
	matches := reDigits.FindAllString(arg, -1)
	if len(matches) == 0 {
		return currentEventID, defaultForecastCurveRanks()
	}
	nums := make([]int, 0, len(matches))
	for _, s := range matches {
		if n, err := strconv.Atoi(s); err == nil {
			nums = append(nums, n)
		}
	}
	known := map[int]bool{}
	for _, r := range skforecast.RankLevels {
		known[r] = true
	}
	for _, r := range skforecast.LiveRanks {
		known[r] = true
	}
	eventID := currentEventID
	ranks := nums
	if len(nums) >= 2 && !known[nums[0]] {
		eventID = nums[0]
		ranks = nums[1:]
	} else if len(nums) == 1 {
		if !known[nums[0]] {
			eventID = nums[0]
			ranks = defaultForecastCurveRanks()
		} else {
			ranks = nums
		}
	}
	out := make([]int, 0, len(ranks))
	for _, r := range ranks {
		if r > 0 {
			out = append(out, r)
		}
	}
	if len(out) > 8 {
		out = out[:8]
	}
	if len(out) == 0 {
		out = []int{100}
	}
	return eventID, out
}

func eventTimeRange(events []map[string]any, eventID int, history map[string][][2]int64) (int64, int64) {
	for _, ev := range events {
		if intField(ev, "id") == eventID {
			start := int64(intField(ev, "startAt"))
			agg := int64(intField(ev, "aggregateAt"))
			if start > 0 && agg > 0 {
				return start / 1000, agg / 1000
			}
		}
	}
	var minTS, maxTS int64 = 0, 0
	for _, points := range history {
		for _, p := range points {
			ts := p[0]
			if minTS == 0 || ts < minTS {
				minTS = ts
			}
			if ts > maxTS {
				maxTS = ts
			}
		}
	}
	if maxTS > 0 {
		return minTS, maxTS
	}
	now := time.Now().Unix()
	return now - 3600, now
}

func formatEventRemaining(events []map[string]any, eventID int) string {
	for _, ev := range events {
		if intField(ev, "id") == eventID {
			agg := int64(intField(ev, "aggregateAt"))
			if agg == 0 {
				return "未知"
			}
			remain := agg/1000 - time.Now().Unix()
			if remain <= 0 {
				return "已结束"
			}
			days := remain / 86400
			hours := (remain % 86400) / 3600
			minutes := (remain % 3600) / 60
			if days > 0 {
				return fmt.Sprintf("%d天%d小时", days, hours)
			}
			return fmt.Sprintf("%d小时%d分钟", hours, minutes)
		}
	}
	return "未知"
}

func (m *SkModule) handleForecastCurve(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	region := serverCode(server)
	events, err := m.md.Load("events.json", server)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	currentID := currentEventID(events, time.Now().UnixMilli())

	eventID, ranks := parseForecastCurveArgs(req.Arg, currentID)
	legacy, _ := legacyForecastCurveMode(req.RawCmd)
	var forecasts []map[string]any
	if legacy {
		if currentID != 0 {
			forecasts = m.forecast.ReadCached(region, baseEventID(currentID))
		}
		eventID, ranks = parseLegacyForecastCurveArgs(req.RawCmd, req.Arg, currentID, skforecast.DisplayRanks(forecasts))
	}
	if eventID == 0 {
		return onebot.ReplyText(req.Event, "未找到可查询的活动", false)
	}
	eventName := eventNameByID(events, eventID)

	history := map[string][][2]int64{}
	for _, rank := range ranks {
		rows, _ := m.store.QueryRankingByRank(ctx, region, eventID, rank)
		if len(rows) == 0 {
			continue
		}
		pts := make([][2]int64, 0, len(rows))
		for _, r := range rows {
			pts = append(pts, [2]int64{r.Time.Unix(), r.Score})
		}
		history[strconv.Itoa(rank)] = pts
	}

	if !legacy {
		forecasts = m.forecast.ReadCached(region, eventID)
	}
	if len(history) == 0 && len(forecasts) == 0 {
		return onebot.ReplyText(req.Event, fmt.Sprintf("%s 活动 %d 暂无可用曲线数据", strings.ToUpper(region), eventID), false)
	}

	start, end := eventTimeRange(events, eventID, history)
	img, err := m.draw.Render(ctx, "sk_forecast_curve", map[string]any{
		"region":      region,
		"event_id":    eventID,
		"event_name":  eventName,
		"ranks":       ranks,
		"history":     history,
		"forecasts":   forecasts,
		"pjsk_type":   server,
		"remain_text": formatEventRemaining(events, eventID),
		"time_range":  []int64{start, end},
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}

func legacySKSubscriptionServer(rawCmd string, fallback router.ServerType) router.ServerType {
	switch strings.ToLower(rawCmd) {
	case "订阅cnsk", "取消订阅cnsk":
		return router.ServerCN
	case "订阅twsk", "取消订阅twsk":
		return router.ServerTW
	default:
		return fallback
	}
}

func (m *SkModule) handleSubscribe(ctx context.Context, req router.Request) *onebot.ActionRequest {
	req.Server = legacySKSubscriptionServer(req.RawCmd, req.Server)
	if !req.Event.IsGroup() {
		return onebot.ReplyText(req.Event, "订阅功能仅支持在群聊中使用", true)
	}
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

	if m.bind == nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	uid, _, exists, berr := m.bind.GetUserBind(ctx, req.Event.UserID, server)
	if berr != nil || !exists || uid == 0 {
		return onebot.ReplyText(req.Event, "你还没有绑定"+req.Server.Name()+"账号哦，请先绑定账号", true)
	}
	qqID := itoa64(req.Event.UserID)

	already, err := m.sub.Exists(ctx, qqID, region, currentID)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	if already {
		return onebot.ReplyText(req.Event,
			fmt.Sprintf("你已经订阅了%s服活动%d的分数变动通知", strings.ToUpper(region), currentID), true)
	}
	if err := m.sub.Add(ctx, qqID, itoa64(req.Event.GroupID), region, currentID, itoa64(uid)); err != nil {
		return onebot.ReplyText(req.Event, "订阅失败，请稍后重试", true)
	}
	return onebot.ReplyText(req.Event,
		fmt.Sprintf("订阅成功！\n服务器：%s\n活动号：%d\n当你的分数发生变化时，将在本群自动推送查房信息",
			strings.ToUpper(region), currentID), true)
}

func (m *SkModule) handleUnsubscribe(ctx context.Context, req router.Request) *onebot.ActionRequest {
	req.Server = legacySKSubscriptionServer(req.RawCmd, req.Server)
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
	qqID := itoa64(req.Event.UserID)
	removed, err := m.sub.Remove(ctx, qqID, region, currentID)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	if removed {
		return onebot.ReplyText(req.Event,
			fmt.Sprintf("已取消订阅%s服活动%d的分数变动通知", strings.ToUpper(region), currentID), true)
	}
	return onebot.ReplyText(req.Event, "你还没有订阅该活动", true)
}

func (m *SkModule) handleClearSubscriptions(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if !m.supers[req.Event.UserID] {
		return nil
	}
	deleted, err := m.sub.ClearAll(ctx)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	if deleted > 0 {
		return onebot.ReplyText(req.Event, fmt.Sprintf("已清空所有订阅，共删除 %d 条记录", deleted), false)
	}
	return onebot.ReplyText(req.Event, "订阅表已经是空的", false)
}
