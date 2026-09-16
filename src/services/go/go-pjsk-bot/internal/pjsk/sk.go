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

// WL 活动 ID 编码系数，对齐 WL_EVENT_ID_FACTOR。
const wlEventIDFactor = 1000

// baseEventID 返回 WL 编码活动 ID 对应的主活动 ID。
func baseEventID(eventID int) int {
	if eventID >= wlEventIDFactor {
		return eventID % wlEventIDFactor
	}
	return eventID
}

// isWorldBloomEvent 按 events.json 判定活动类型，并兼容 WL 章节编码 ID。
func isWorldBloomEvent(events []map[string]any, eventID int) bool {
	baseID := baseEventID(eventID)
	for _, event := range events {
		if intField(event, "id") == baseID {
			return strField(event, "eventType") == "world_bloom"
		}
	}
	return false
}

// loadWorldBloomChapters 读取并验证某活动的 WL 章节，供 sk/deck 共用。
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

// shouldRenderWLRankTable 对齐 Python 的 _should_render_wl_rank_table：
// WL 快捷命令固定出总榜+章节专用表；普通 sks/skl 仅在未指定单章时出专用表。
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

// SkModule 实现 sk 时速（sks/时速/日速/半日速）、排名线（skl）、活动预测（sk预测/ycx）
// 与查房（cf/查房/sk）查询。
//
// 榜线时序数据由 go-pjsk-helper 采集写入 sqlite，本模块只读并计算展示；
// 预测缓存 JSON 由 Python 定时任务生成，Go 侧只读取展示（非曲线/非WL 表格模式）。
// sks/skl/cf/csb 支持显式指定 WL 单章节参数（如 wl2/wl角色）；WL 快捷指令
// （wlsks/wlsk 等无参数默认合并榜）与 ycx 曲线作为后续增量。
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

// NewSkModule 创建 sk 模块。forecast/bind/chara/sub 可为 nil（缺失时禁用对应分支）。
func NewSkModule(md *masterdata.Loader, s *skstore.Store, d *draw.Client, forecast *skforecast.Reader, bind *store.Store, chara *cards.CharaAliasResolver, sub *sksub.Store, supers []int64) *SkModule {
	set := make(map[int64]bool, len(supers))
	for _, u := range supers {
		set[u] = true
	}
	return &SkModule{md: md, store: s, draw: d, forecast: forecast, bind: bind, chara: chara, sub: sub, supers: set}
}

// Register 注册时速、排名线、预测与查房指令。
func (m *SkModule) Register(r *router.Router) {
	r.RegisterNumericSuffix("sks", []string{"时速", "sk时速", "日速", "sk日速", "半日速", "sk半日速"}, m.handleSpeed)
	r.RegisterNumericSuffix("skl", []string{"排名线", "sk排名线", "sk线"}, m.handleLine)
	if m.forecast != nil {
		r.RegisterNumericSuffix("sk预测", []string{"活动预测", "skp"}, m.handleForecast)
		r.RegisterNumericSuffix("ycx曲线", []string{"sk预测曲线", "活动预测曲线"}, m.handleForecastCurve)
	}
	// cf/查房/sk：查房信息（范围/多排名/单排名/ID/绑定账号）。
	r.RegisterNumericSuffix("cf", []string{"查房"}, m.handleCf)
	r.RegisterNumericSuffix("sk", nil, m.handleCf)
	// csb/查水表：逐时游玩次数 + 停车区间（单排名/ID/绑定账号）。
	r.RegisterNumericSuffix("csb", []string{"查水表"}, m.handleCsb)
	// WL 快捷指令：无参数默认查当前章节单榜。
	r.RegisterNumericSuffix("wlsk", []string{"wl查房"}, m.handleCf)
	r.RegisterNumericSuffix("wlcsb", []string{"wl查水表"}, m.handleCsb)
	// WL 快捷指令：无参数默认展示跨章节合并榜表（时速/排名线）。
	r.RegisterNumericSuffix("wlsks", []string{"wl时速", "wlsk时速", "wl日速", "wlsk日速", "wl半日速", "wlsk半日速"}, m.handleWLSpeed)
	r.RegisterNumericSuffix("wlskl", []string{"wl排名线", "wlsk排名线", "wlsk线"}, m.handleWLLine)
	// sk 分数变动订阅（增删查；定时推送仍由 Python）。
	if m.sub != nil {
		r.Register("订阅sk", []string{"sk订阅"}, m.handleSubscribe)
		r.Register("退订sk", []string{"取消订阅sk", "sk取消订阅", "sk退订"}, m.handleUnsubscribe)
		r.Register("清空sk订阅", nil, m.handleClearSubscriptions)
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

	// 解析显式 WL 单章节参数（如 sks wl2 100）；命中后用编码 event_id 与章节标题。
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

	// 普通 sks 在 WL 活动且未指定单章时，和 Python 一样切到总榜+章节专用表。
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

	// 解析显式 WL 单章节参数（如 skl wl2 100）。
	arg := req.Arg
	titlePrefix := fmt.Sprintf("【%s-%d】", strings.ToUpper(region), eventID)
	var chapter map[string]any
	if wlID, rest, selected := m.resolveWLQueryEventID(server, req.Arg, eventID); selected != nil {
		eventID = wlID
		arg = rest
		chapter = selected
		titlePrefix = fmt.Sprintf("【%s-%d-第%d章单榜】", strings.ToUpper(region), eventID%wlEventIDFactor, intField(chapter, "chapterNo"))
	}

	// Python 的 skl 默认查询 T50 以后；显式数字仍可直接查询任意排名。
	ranks := skranking.ParseRankArgs(arg, rankLevelsFrom(50), 20)
	if ranks == nil {
		return onebot.ReplyText(req.Event, "请输入有效的排名（如1000、100 1000或100-110）", false)
	}

	// 普通 skl 在 WL 活动且未指定单章时，和 Python 一样切到 WL 专用表。
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
	// wlsk/wl查房 快捷入口：无 WL 选择器时默认查当前章节单榜。
	arg = m.injectDefaultWLChapter(req.RawCmd, arg, server, currentID)
	// 解析显式 WL 单章节参数（cf wl2 100）；命中后在该章节分榜内查询。
	if wlID, rest, chapter := m.resolveWLQueryEventID(server, arg, currentID); chapter != nil {
		currentID = wlID
		arg = rest
	} else if hasWLToken(arg) {
		// 含 WL 选择器但未命中章节（如非 WL 活动或章节不存在）：交由 Python wlsk 处理。
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

// isWLShortcut 判断原始指令名是否 WL 快捷指令（wl/cnwl/twwl 前缀），
// 对齐 _is_wl_shortcut_command。
func isWLShortcut(rawCmd string) bool {
	c := strings.ToLower(rawCmd)
	return strings.HasPrefix(c, "wl") || strings.HasPrefix(c, "cnwl") || strings.HasPrefix(c, "twwl")
}

// injectDefaultWLChapter 为 WL 快捷指令（wlsk/wlcsb）在无 WL 选择器时注入当前章节
// （wl{章节号}），对齐 _with_current_wl_chapter_arg。非 WL 快捷指令或已含选择器时原样返回。
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
	history, err := m.store.QueryRankingTailByUID(ctx, region, eventID, uid)
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

// wlChapters 返回某活动的 WL 章节（按 chapterNo 升序）。
// 对齐 _get_wl_chapters；活动类型过滤由 wlChaptersForEvents 负责。
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

// wlChaptersForEvents 仅在 events.json 明确标记为 world_bloom 时返回章节。
func (m *SkModule) wlChaptersForEvents(server int, events []map[string]any, eventID int) []map[string]any {
	if !isWorldBloomEvent(events, eventID) {
		return nil
	}
	return m.wlChapters(server, baseEventID(eventID))
}

// wlChaptersForEvent 读取活动主数据后再判定 WL 类型，供跨模块逻辑使用。
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

// sortByChapterNo 按 chapterNo 升序排序。
func sortByChapterNo(chapters []map[string]any) {
	for i := 1; i < len(chapters); i++ {
		for j := i; j > 0 && intField(chapters[j], "chapterNo") < intField(chapters[j-1], "chapterNo"); j-- {
			chapters[j], chapters[j-1] = chapters[j-1], chapters[j]
		}
	}
}

var reWLChapterNum = regexp.MustCompile(`^wl(?:第)?(\d+)(?:章)?$`)
var reBareChapterNum = regexp.MustCompile(`^(?:第)?(\d+)(?:章)?$`)

// currentWLChapter 返回当前进行中的 WL 章节（chapterStartAt 已到的最晚一章），
// 无则返回首章。对齐 _current_wl_chapter。
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

// resolveWLQueryEventID 解析 sk 查询里的 WL 章节参数（wl/wl2/wl角色/-c 角色），
// 返回 (编码后的 event_id, 去掉 WL token 后的剩余参数, 命中的章节)。
// 未命中或非 WL 活动时返回 (baseEventID, 原样 args, nil)。对齐
// _resolve_wl_query_event_id_from_chapters（裸角色名不视为 WL）。
func (m *SkModule) resolveWLQueryEventID(server int, args string, baseEventID int) (int, string, map[string]any) {
	chapters := m.wlChaptersForEvent(server, baseEventID)
	return resolveWLFromChapters(chapters, m.chara, args, baseEventID)
}

// resolveWLFromChapters 是 WL 参数解析的纯逻辑核心（便于单测）。
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

// handleCsb 实现 csb/查水表：单排名(≤100)/ID/绑定账号 → 逐时游玩次数 + 停车区间，
// 出 sk_csb 图。WL 分榜（wlcsb）由 Python 处理。对齐 _handle_csb。
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
	// wlcsb/wl查水表 快捷入口：无 WL 选择器时默认查当前章节单榜。
	arg = m.injectDefaultWLChapter(req.RawCmd, arg, server, currentID)
	if wlID, rest, chapter := m.resolveWLQueryEventID(server, arg, currentID); chapter != nil {
		currentID = wlID
		arg = rest
	} else if hasWLToken(arg) {
		return onebot.ReplyText(req.Event, "WL 分榜查水表请使用 wlcsb 指令", false)
	}

	// 解析出玩家 uid（空→绑定账号；≤100→排名对应玩家；否则按 ID）。
	uid, resp := m.resolveCfUID(ctx, req, region, currentID, arg)
	if resp != nil {
		return resp
	}

	history, err := m.store.QueryRankingByUID(ctx, region, currentID, uid)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	if len(history) == 0 {
		return onebot.ReplyText(req.Event, "没有该玩家的榜线历史记录（可能未进入记录的档线范围内）", false)
	}
	latest := history[len(history)-1]

	// 逐时游玩次数：下一条分数增加则计入其所在的 (day, hour)。
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

	// 停车区间：连续同分区间 ≥5 分钟。
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
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}

// resolveCfUID 解析 cf/csb 的玩家 uid：空参数→绑定账号；≤100→该排名玩家；否则按 ID。
// 返回 (uid, nil) 成功，或 ("", 错误回复)。
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
		return "", onebot.ReplyText(req.Event, "请输入有效的排名（1-100）或玩家ID", false)
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

// daysBetween 返回两个时间的日期差（按本地日期），对齐 (b.date - a.date).days。
func daysBetween(a, b time.Time) int {
	ay, am, ad := a.Date()
	by, bm, bd := b.Date()
	da := time.Date(ay, am, ad, 0, 0, 0, 0, a.Location())
	db := time.Date(by, bm, bd, 0, 0, 0, 0, b.Location())
	return int(db.Sub(da).Hours() / 24)
}

// computeStopPeriods 用滑动窗口找停车区间（连续同分且 ≥5 分钟），对齐 Python 逻辑。
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

// renderWLRankTable 统一调用 pjsk-draw 的 WL 总榜+章节表。
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

// handleWLSpeed 实现 wlsks（WL 时速）：快捷命令始终出跨章节合并榜表，
// 与 Python 的 _should_render_wl_rank_table 保持一致。
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

	// WL 快捷命令即使带 wl2/角色参数也保持 Python 的合并榜表语义；这里只提取剩余排名参数。
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

// handleWLLine 实现 wlskl（WL 排名线）：快捷命令始终出跨章节合并榜表，
// 与 Python 的 _should_render_wl_rank_table 保持一致。
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
	// 排名线不算时速：period_hours=0 → speed 全为 nil。
	return m.renderWLRankTable(ctx, req, region, baseEventID, chapters, ranks,
		0, 3600,
		fmt.Sprintf("【%s-%d】WL排名线", strings.ToUpper(region), baseEventID),
		"score", "分数", "")
}

// wlRankTableRows 读取 WL 总榜 + 各章节单榜的分数/时速，组装成 sk_wl_rank_table 的
// rows（每行 {rank, total, chapters}）。periodHours=0 时不算时速（speed 为 nil）。
// 对齐 _get_wl_rank_table_rows。
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
	// data[key][rank] = {score, speed}
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

// defaultForecastCurveRanks 对齐 _default_forecast_curve_ranks。
func defaultForecastCurveRanks() []int { return []int{100, 500, 1000, 5000, 10000} }

var reDigits = regexp.MustCompile(`\d+`)

// parseForecastCurveArgs 解析 ycx 曲线参数：[活动ID] [排名...] 或仅 [排名...]。
// 对齐 _parse_forecast_curve_args。
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

// eventTimeRange 返回曲线横轴起止秒时间戳，对齐 _get_event_time_range。
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

// formatEventRemaining 返回活动剩余时间文案，对齐 _format_event_remaining。
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

// handleForecastCurve 实现 ycx曲线/sk预测曲线：读历史榜线序列 + 预测缓存，出预测曲线图。
func (m *SkModule) handleForecastCurve(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	region := serverCode(server)
	events, err := m.md.Load("events.json", server)
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	currentID := currentEventID(events, time.Now().UnixMilli())

	eventID, ranks := parseForecastCurveArgs(req.Arg, currentID)
	if eventID == 0 {
		return onebot.ReplyText(req.Event, "未找到可查询的活动", false)
	}
	eventName := eventNameByID(events, eventID)

	// 历史曲线点：每个 rank 的全历史 (ts, score)。
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

	forecasts := m.forecast.ReadCached(region, eventID)
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

// handleSubscribe 实现 订阅sk：为绑定账号订阅当前活动的分数变动推送（群内）。
func (m *SkModule) handleSubscribe(ctx context.Context, req router.Request) *onebot.ActionRequest {
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

// handleUnsubscribe 实现 退订sk：取消当前活动的分数变动订阅。
func (m *SkModule) handleUnsubscribe(ctx context.Context, req router.Request) *onebot.ActionRequest {
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

// handleClearSubscriptions 实现 清空sk订阅（superuser）：清空全部订阅。
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
