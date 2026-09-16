package pjsk

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/remotelive"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

// SkMeModule 实现 skme/cnskme/twskme（及 sk我的曲线）查询。
//
// 它只读 remote_live SQLite 并把 Python pjsk-draw 已支持的
// sk_me_curve_total/sk_me_curve_wl payload 交给 draw 服务；remote 控制、自动
// 打歌循环、记录落库及订阅调度均不在本模块内。
type SkMeModule struct {
	md      *masterdata.Loader
	live    *remotelive.Store
	draw    *draw.Client
	account string
	region  string
	supers  map[int64]bool
}

// NewSkMeModule 创建 skme 查询模块。account/region 对应
// SEKAI_REMOTE_ACCOUNT/SEKAI_REMOTE_REGION，命令带账号时以命令参数为准。
func NewSkMeModule(md *masterdata.Loader, live *remotelive.Store, d *draw.Client, account, region string, supers []int64) *SkMeModule {
	set := make(map[int64]bool, len(supers))
	for _, userID := range supers {
		set[userID] = true
	}
	return &SkMeModule{
		md:      md,
		live:    live,
		draw:    d,
		account: strings.TrimSpace(account),
		region:  strings.TrimSpace(region),
		supers:  set,
	}
}

// Register 注册曲线查询命令。cn/tw 前缀由 Router 自动展开。
func (m *SkMeModule) Register(r *router.Router) {
	r.RegisterNumericSuffix("skme", []string{"sk我的曲线"}, m.handle)
}

func (m *SkMeModule) handle(ctx context.Context, req router.Request) *onebot.ActionRequest {
	// Python 原命令 permission=SUPERUSER；非超管保持静默，不吞掉其他逻辑。
	if !m.supers[req.Event.UserID] {
		return nil
	}

	account, region, err := m.resolveAccountRegion(req)
	if err != nil {
		return onebot.ReplyText(req.Event, err.Error(), false)
	}

	events, err := m.md.Load("events.json", int(req.Server))
	if err != nil {
		return onebot.ReplyText(req.Event, "读取活动数据失败："+err.Error(), false)
	}
	eventID := currentEventID(events, time.Now().UnixMilli())
	if eventID == 0 {
		return onebot.ReplyText(req.Event, "当前没有可查询的活动", false)
	}
	event := eventByID(events, eventID)
	if event == nil {
		return onebot.ReplyText(req.Event, fmt.Sprintf("主数据中找不到活动 %d，无法生成曲线", eventID), false)
	}

	if m.live == nil {
		return onebot.ReplyText(req.Event, "remote 打歌记录查询未启用", false)
	}
	records, err := m.live.QueryRecords(ctx, region, account, eventID)
	if err != nil {
		return onebot.ReplyText(req.Event, "查询 remote 打歌记录失败："+err.Error(), false)
	}
	if len(records) == 0 {
		return onebot.ReplyText(req.Event,
			fmt.Sprintf("没有 %s #%s 在活动 %d 的打歌记录。\n先用 live on 跑起自动循环，产生记录后再查。",
				strings.ToUpper(region), account, eventID), false)
	}
	if countTotalRankPoints(records) == 0 {
		return onebot.ReplyText(req.Event,
			fmt.Sprintf("%s #%s 在活动 %d 的记录缺少有效总榜排名，无法可靠绘制 skme 曲线。",
				strings.ToUpper(region), account, eventID), false)
	}
	if m.draw == nil {
		return onebot.ReplyText(req.Event, "pjsk-draw 客户端未配置，无法出图", false)
	}

	eventName := strField(event, "name")
	if eventName == "" {
		eventName = fmt.Sprintf("Event %d", eventID)
	}
	timeRange := meCurveTimeRange(event, records)
	basePayload := map[string]any{
		"region":      region,
		"event_id":    eventID,
		"event_name":  eventName,
		"time_range":  timeRange,
		"remain_text": eventRemainText(event),
		"records":     liveRecordPayloads(records),
	}

	image, err := m.draw.Render(ctx, "sk_me_curve_total", basePayload)
	if err != nil {
		return onebot.ReplyText(req.Event, "绘制 skme 总榜曲线失败："+err.Error(), false)
	}
	images := [][]byte{image}

	// WL 分榜是总榜曲线之外的补充图。仅当主数据确认是 world_bloom、数据库有
	// 章节记录且章节排名有效时绘制；绝不从缺失字段猜章节或排名。
	if strField(event, "eventType") == "world_bloom" {
		if wlImage, ok, wlErr := m.renderWLCurve(ctx, region, int(req.Server), eventID, eventName, records, timeRange, eventRemainText(event)); wlErr != nil {
			return onebot.ReplyText(req.Event, "总榜曲线已生成，但 WL 分榜曲线失败："+wlErr.Error(), false)
		} else if ok {
			images = append(images, wlImage)
		}
	}
	return replyCurveImages(req.Event, images...)
}

func (m *SkMeModule) resolveAccountRegion(req router.Request) (string, string, error) {
	arg := strings.TrimSpace(req.Arg)
	account := m.account
	region := serverCode(int(req.Server))
	if arg != "" {
		fields := strings.Fields(arg)
		if len(fields) != 1 {
			return "", "", fmt.Errorf("用法：%s [remote账号]（账号只能是一个不含空格的 ID）", req.RawCmd)
		}
		account = fields[0]
	} else if strings.TrimSpace(m.region) != "" {
		// 与 Python 原实现一致：未带账号时，配置区服覆盖命令默认区服。
		region = strings.ToLower(strings.TrimSpace(m.region))
	}
	if account == "" {
		return "", "", fmt.Errorf("未配置 remote 账号。请设置 SEKAI_REMOTE_ACCOUNT，或直接 %s <账号id>", req.RawCmd)
	}
	if !validRemoteAccount(account) {
		return "", "", fmt.Errorf("remote 账号 %q 含有不支持的字符（仅允许字母、数字、点、下划线、短横线）", account)
	}
	if region != "jp" && region != "cn" && region != "tw" {
		return "", "", fmt.Errorf("不支持的 remote 区服 %q（仅支持 jp/cn/tw）", region)
	}
	return account, region, nil
}

func validRemoteAccount(account string) bool {
	account = strings.TrimSpace(account)
	if account == "" {
		return false
	}
	for _, r := range account {
		if !(unicode.IsLetter(r) || unicode.IsDigit(r) || r == '.' || r == '_' || r == '-') {
			return false
		}
	}
	return true
}

func eventByID(events []map[string]any, eventID int) map[string]any {
	for _, event := range events {
		if intField(event, "id") == eventID {
			return event
		}
	}
	return nil
}

func countTotalRankPoints(records []remotelive.Record) int {
	count := 0
	for _, record := range records {
		if record.EventRank != nil && *record.EventRank > 0 {
			count++
		}
	}
	return count
}

func liveRecordPayloads(records []remotelive.Record) []map[string]any {
	payload := make([]map[string]any, 0, len(records))
	for _, record := range records {
		payload = append(payload, liveRecordPayload(record))
	}
	return payload
}

// liveRecordPayload 对齐 Python _live_record_payload；未填列保留为 JSON null。
func liveRecordPayload(record remotelive.Record) map[string]any {
	return map[string]any{
		"ts":               record.TS,
		"event_point":      optionalInt(record.EventPoint),
		"event_rank":       optionalInt(record.EventRank),
		"wl_chapter_no":    optionalInt(record.WLChapterNo),
		"wl_chapter_point": optionalInt(record.WLChapterPoint),
		"wl_chapter_rank":  optionalInt(record.WLChapterRank),
	}
}

func optionalInt(value *int) any {
	if value == nil {
		return nil
	}
	return *value
}

func meCurveTimeRange(event map[string]any, records []remotelive.Record) []int64 {
	startMS := int64(intField(event, "startAt"))
	endMS := int64(intField(event, "aggregateAt"))
	if startMS > 0 && endMS > 0 {
		return []int64{startMS / 1000, endMS / 1000}
	}
	var minTS, maxTS int64
	for _, record := range records {
		if record.TS <= 0 {
			continue
		}
		if minTS == 0 || record.TS < minTS {
			minTS = record.TS
		}
		if record.TS > maxTS {
			maxTS = record.TS
		}
	}
	if minTS > 0 {
		return []int64{minTS, maxTS}
	}
	now := time.Now().Unix()
	return []int64{now - 3600, now}
}

func eventRemainText(event map[string]any) string {
	aggregateMS := int64(intField(event, "aggregateAt"))
	if aggregateMS <= 0 {
		return "未知"
	}
	remain := aggregateMS/1000 - time.Now().Unix()
	if remain <= 0 {
		return "已结束"
	}
	days, rest := remain/86400, remain%86400
	hours, rest := rest/3600, rest%3600
	minutes := rest / 60
	if days > 0 {
		return fmt.Sprintf("%d天%d小时", days, hours)
	}
	return fmt.Sprintf("%d小时%d分钟", hours, minutes)
}

func (m *SkMeModule) renderWLCurve(ctx context.Context, region string, server, eventID int, eventName string, records []remotelive.Record, timeRange []int64, remainText string) ([]byte, bool, error) {
	chapters, err := m.md.Load("worldBlooms.json", server)
	if err != nil {
		return nil, false, nil // 非致命：总榜曲线仍然可用，且不猜章节参数。
	}
	baseEventID := eventID % wlEventIDFactor
	if eventID < wlEventIDFactor {
		baseEventID = eventID
	}
	selected := make([]map[string]any, 0)
	for _, chapter := range chapters {
		if intField(chapter, "eventId") == baseEventID && intField(chapter, "chapterNo") > 0 {
			selected = append(selected, chapter)
		}
	}
	if len(selected) == 0 {
		return nil, false, nil
	}
	sortByChapterNo(selected)
	current := currentWLChapter(selected)
	currentNo := 0
	if current != nil {
		currentNo = intField(current, "chapterNo")
	}

	chapterRecords := make(map[string]any)
	for _, chapterNo := range remotelive.ChapterNumbers(records) {
		items := make([]map[string]any, 0)
		for _, record := range records {
			if record.WLChapterNo == nil || *record.WLChapterNo != chapterNo {
				continue
			}
			if record.WLChapterRank == nil || *record.WLChapterRank <= 0 {
				continue
			}
			items = append(items, liveRecordPayload(record))
		}
		if len(items) > 0 {
			chapterRecords[strconv.Itoa(chapterNo)] = items
		}
	}
	if len(chapterRecords) == 0 {
		return nil, false, nil
	}

	meta := make(map[string]any, len(selected))
	for _, chapter := range selected {
		chapterNo := intField(chapter, "chapterNo")
		endMS := intField(chapter, "aggregateAt")
		if endMS == 0 {
			endMS = intField(chapter, "chapterEndAt")
		}
		var start, end any
		if startMS := intField(chapter, "chapterStartAt"); startMS > 0 {
			start = int64(startMS) / 1000
		}
		if endMS > 0 {
			end = int64(endMS) / 1000
		}
		meta[strconv.Itoa(chapterNo)] = map[string]any{
			"gameCharacterId": intField(chapter, "gameCharacterId"),
			"active":          chapterNo == currentNo,
			"start":           start,
			"end":             end,
		}
	}

	image, err := m.draw.Render(ctx, "sk_me_curve_wl", map[string]any{
		"region":          region,
		"event_id":        eventID,
		"event_name":      eventName,
		"time_range":      timeRange,
		"remain_text":     remainText,
		"chapter_records": chapterRecords,
		"chapter_meta":    meta,
		"pjsk_type":       server,
	})
	return image, true, err
}

func replyCurveImages(event onebot.MessageEvent, images ...[]byte) *onebot.ActionRequest {
	message := make(onebot.Message, 0, len(images))
	for _, image := range images {
		message = append(message, onebot.ImageBytes(base64Encode(image)))
	}
	return onebot.SendMessageAction(event, message)
}
