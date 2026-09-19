package pjsk

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/remotelive"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

const (
	remoteAPIProbeTimeout = 5 * time.Second
	remoteLiveTimeout     = 5 * time.Minute
	remoteStopTimeout     = 10 * time.Second
	remoteUploadTTL       = 10 * time.Minute
	remoteMaxDownloadSize = 32 << 20
)

// RemoteConfig 是 remote/live/token 控制模块的配置。
type RemoteConfig struct {
	APIURL       string
	APIToken     string
	ControlURL   string
	ControlToken string
	Region       string
	Account      string
	LiveInterval time.Duration
	LiveAutoStop string
	DataDir      string
	Masterdata   *masterdata.Loader
	Superusers   []int64
}

type remoteStats struct {
	startedAt     time.Time
	rounds        int
	success       int
	failed        int
	lastError     string
	lastSuccessAt time.Time
}

// RemoteModule 实现 remote/live 控制、状态落盘及 pjsktoken 管理。
//
// 它只维护可取消的控制后台和 sekai-api 请求，不接管 guess、订阅或 skme
// 绘图逻辑。完整的打歌结果入库/曲线仍可由后续迁移独立接入。
type RemoteModule struct {
	apiURL       string
	apiToken     string
	controlURL   string
	controlToken string
	region       string
	account      string
	interval     time.Duration
	autoStop     string
	http         *http.Client
	supers       map[int64]bool
	state        *remoteStateStore
	token        *remoteTokenModule
	masterdata   *masterdata.Loader
	liveStore    *remotelive.Store

	liveMu     sync.Mutex
	liveCancel context.CancelFunc
	liveDone   chan struct{}

	statsMu sync.RWMutex
	stats   remoteStats
}

// NewRemoteModule 创建 remote/live/token 控制模块。
func NewRemoteModule(cfg RemoteConfig) *RemoteModule {
	interval := cfg.LiveInterval
	if interval <= 0 {
		interval = 80 * time.Second
	}
	region := strings.ToLower(strings.TrimSpace(cfg.Region))
	if region == "" {
		region = "cn"
	}
	controlURL := strings.TrimRight(strings.TrimSpace(cfg.ControlURL), "/")
	controlToken := strings.TrimSpace(cfg.ControlToken)
	if isComposeSekaiAPIURL(cfg.APIURL) {
		// Compose 负责 sekai-api 的生命周期；旧版宿主控制服务即使残留在
		// .env 中也不能让 remote off 误请求 host.docker.internal:9998。
		controlURL = ""
		controlToken = ""
	}
	supers := make(map[int64]bool, len(cfg.Superusers))
	for _, id := range cfg.Superusers {
		supers[id] = true
	}
	m := &RemoteModule{
		apiURL:       strings.TrimRight(strings.TrimSpace(cfg.APIURL), "/"),
		apiToken:     strings.TrimSpace(cfg.APIToken),
		controlURL:   controlURL,
		controlToken: controlToken,
		region:       region,
		account:      strings.TrimSpace(cfg.Account),
		interval:     interval,
		autoStop:     strings.TrimSpace(cfg.LiveAutoStop),
		http:         &http.Client{Timeout: remoteLiveTimeout},
		supers:       supers,
		state:        newRemoteStateStore(cfg.DataDir),
		masterdata:   cfg.Masterdata,
		liveStore:    remotelive.New(cfg.DataDir),
	}
	if m.masterdata == nil && strings.TrimSpace(cfg.DataDir) != "" {
		m.masterdata = masterdata.New(cfg.DataDir)
	}
	// 进程重启后后台 goroutine 不存在，不能保留过期的 live_on 标记。
	m.state.Update(nil, boolPtr(false))
	m.token = newRemoteTokenModule(m)
	return m
}

func isComposeSekaiAPIURL(raw string) bool {
	u, err := url.Parse(strings.TrimSpace(raw))
	return err == nil && strings.EqualFold(u.Hostname(), "sekai-api")
}

// Register 注册 remote/live 与 token 命令。
func (m *RemoteModule) Register(r *router.Router) {
	// 规范名使用 Python remote 插件的 go_owns key，触发词仍兼容用户命令。
	r.Register("pjsk_remote", []string{"remote"}, m.handleRemote)
	r.Register("pjsk_live", []string{"live"}, m.handleLive)
	r.Register("pjsk_remote_status", []string{"remote状态", "remotestatus", "remote status"}, m.handleStatus)
	m.token.Register(r)
}

// Close 取消 live 后台并释放 token 会话清理协程。
func (m *RemoteModule) Close() {
	m.stopLive()
	if m.token != nil {
		m.token.Close()
	}
}

// HandleNotice 供 OneBot notice handler 调用。没有 token 上传会话时返回 nil，
// 这样可以与已有 Suite UploadModule 串联而不改变其行为。
func (m *RemoteModule) HandleNotice(notice onebot.NoticeEvent) *onebot.ActionRequest {
	if m.token == nil {
		return nil
	}
	return m.token.HandleNotice(notice)
}

func (m *RemoteModule) isSuper(e onebot.MessageEvent) bool { return m.supers[e.UserID] }

func (m *RemoteModule) handleRemote(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if !m.isSuper(req.Event) {
		return nil
	}
	switch strings.ToLower(strings.TrimSpace(req.Arg)) {
	case "on":
		ok, message := m.ensureAPI(ctx)
		if !ok {
			return onebot.ReplyText(req.Event, "remote on 失败："+message, false)
		}
		m.state.Update(boolPtr(true), nil)
		return onebot.ReplyText(req.Event, "remote on："+message+"\nAPI 地址 "+m.apiURL, false)
	case "off":
		wasRunning := m.stopLive()
		m.state.Update(boolPtr(false), boolPtr(false))
		lines := []string{}
		if wasRunning {
			lines = append(lines, "自动循环打歌已停止")
		}
		if ok, message := m.stopAPI(ctx); message != "" {
			lines = append(lines, message)
			if !ok {
				return onebot.ReplyText(req.Event, "remote off：\n"+strings.Join(lines, "\n"), false)
			}
		}
		if len(lines) == 0 {
			lines = append(lines, "控制状态已关闭（API 仍由现有服务托管）")
		}
		return onebot.ReplyText(req.Event, "remote off：\n"+strings.Join(lines, "\n"), false)
	default:
		return onebot.ReplyText(req.Event, "用法：remote on / remote off", false)
	}
}

func (m *RemoteModule) handleLive(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if !m.isSuper(req.Event) {
		return nil
	}
	switch strings.ToLower(strings.TrimSpace(req.Arg)) {
	case "off":
		stopped := m.stopLive()
		m.state.Update(nil, boolPtr(false))
		if stopped {
			return onebot.ReplyText(req.Event, "live off：自动循环打歌已停止", false)
		}
		return onebot.ReplyText(req.Event, "live off：自动循环打歌未在运行", false)
	case "on":
		if m.apiToken == "" {
			return onebot.ReplyText(req.Event, "live on 失败：SEKAI_API_TOKEN 未配置", false)
		}
		if m.account == "" {
			return onebot.ReplyText(req.Event, "live on 失败：SEKAI_REMOTE_ACCOUNT 未配置", false)
		}
		if m.isLiveRunning() {
			return onebot.ReplyText(req.Event, "live on：自动循环打歌已在运行中", false)
		}
		ok, message := m.ensureAPI(ctx)
		if !ok {
			return onebot.ReplyText(req.Event, "live on 失败："+message, false)
		}
		m.startLive()
		m.state.Update(boolPtr(true), boolPtr(true))
		return onebot.ReplyText(req.Event,
			fmt.Sprintf("live on：%s\n自动循环打歌已启动\n目标 %s #%s，间隔 %s%s",
				message, strings.ToUpper(m.region), m.account, m.interval,
				m.autoStopMessage()), false)
	default:
		return onebot.ReplyText(req.Event, "用法：live on / live off", false)
	}
}

func (m *RemoteModule) handleStatus(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if !m.isSuper(req.Event) {
		return nil
	}
	state := m.state.Load()
	lines := []string{
		"控制服务：" + m.controlURLText(),
		"API 地址：" + m.apiURL,
		fmt.Sprintf("remote 开关：%s", onOff(state.RemoteOn)),
		fmt.Sprintf("live 开关：%s", onOff(m.isLiveRunning())),
		fmt.Sprintf("API 可达：%s", onOff(m.apiAlive(ctx))),
	}
	if m.controlURL != "" {
		if status, err := m.controlStatus(ctx); err != nil {
			lines = append(lines, "宿主控制服务："+err.Error())
		} else {
			line := "宿主进程：未运行"
			if running, _ := status["running"].(bool); running {
				line = "宿主进程：运行中"
			}
			if pid := remoteNumberText(status["pid"]); pid != "" && pid != "0" {
				line += "（pid " + pid + "）"
			}
			lines = append(lines, line)
		}
	}
	st := m.statsSnapshot()
	if st.rounds > 0 || st.failed > 0 {
		lines = append(lines, fmt.Sprintf("本次已跑 %d 轮（成功 %d / 失败 %d）", st.rounds, st.success, st.failed))
	}
	if !st.lastSuccessAt.IsZero() {
		lines = append(lines, "最近成功："+st.lastSuccessAt.Format("2006-01-02 15:04:05"))
	}
	if st.lastError != "" {
		lines = append(lines, "最近错误："+st.lastError)
	}
	return onebot.ReplyText(req.Event, strings.Join(lines, "\n"), false)
}

func (m *RemoteModule) controlURLText() string {
	if m.controlURL == "" {
		return "未配置（API 由现有服务托管）"
	}
	return m.controlURL
}

func (m *RemoteModule) autoStopMessage() string {
	if m.autoStop == "" {
		return ""
	}
	return "，每日 " + m.autoStop + " 自动停止"
}

func (m *RemoteModule) apiAlive(ctx context.Context) bool {
	ctx = contextOrBackground(ctx)
	probeCtx, cancel := context.WithTimeout(ctx, remoteAPIProbeTimeout)
	defer cancel()
	status, _, err := m.apiRequest(probeCtx, http.MethodGet, "/echo", nil, "", false)
	return err == nil && status == http.StatusOK
}

func (m *RemoteModule) ensureAPI(ctx context.Context) (bool, string) {
	if m.apiAlive(ctx) {
		return true, "API 可用"
	}
	if m.controlURL == "" || m.controlToken == "" {
		return false, fmt.Sprintf("sekai-api 不可达（%s），且未配置旧部署控制服务", m.apiURL)
	}
	if _, err := m.controlRequest(ctx, http.MethodPost, "/api/start"); err != nil {
		return false, "请求宿主控制服务启动 API 失败：" + err.Error()
	}
	deadline := time.Now().Add(75 * time.Second)
	for time.Now().Before(deadline) {
		if m.apiAlive(ctx) {
			return true, "API 已启动"
		}
		select {
		case <-ctx.Done():
			return false, "等待 API 启动被取消"
		case <-time.After(2 * time.Second):
		}
	}
	return false, "已下发启动指令但 API 仍不可达"
}

func (m *RemoteModule) stopAPI(ctx context.Context) (bool, string) {
	if m.controlURL == "" {
		return true, "API 仍由现有服务托管，未调用停止接口"
	}
	if m.controlToken == "" {
		return false, "已关闭本地控制状态，但 SEKAI_CONTROL_TOKEN 未配置，未停止 API"
	}
	if _, err := m.controlRequest(ctx, http.MethodPost, "/api/stop"); err != nil {
		return false, "停止 API 失败：" + err.Error()
	}
	return true, "API 已停止"
}

func (m *RemoteModule) controlStatus(ctx context.Context) (map[string]any, error) {
	body, err := m.controlRequest(ctx, http.MethodGet, "/status")
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal(body, &out); err != nil {
		return nil, fmt.Errorf("解析宿主控制服务响应失败")
	}
	return out, nil
}

func (m *RemoteModule) controlRequest(ctx context.Context, method, path string) ([]byte, error) {
	ctx = contextOrBackground(ctx)
	if m.controlURL == "" || m.controlToken == "" {
		return nil, fmt.Errorf("SEKAI_CONTROL_URL/TOKEN 未配置")
	}
	req, err := http.NewRequestWithContext(ctx, method, m.controlURL+path, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-Control-Token", m.controlToken)
	resp, err := m.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("HTTP %d：%s", resp.StatusCode, strings.TrimSpace(string(body)))
	}
	return body, nil
}

func (m *RemoteModule) apiRequest(ctx context.Context, method, path string, body io.Reader, contentType string, auth bool) (int, []byte, error) {
	ctx = contextOrBackground(ctx)
	if m.apiURL == "" {
		return 0, nil, fmt.Errorf("SEKAI_API_URL 未配置")
	}
	req, err := http.NewRequestWithContext(ctx, method, m.apiURL+path, body)
	if err != nil {
		return 0, nil, err
	}
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	if auth && m.apiToken != "" {
		req.Header.Set("X-Haruki-Sekai-Token", m.apiToken)
	}
	resp, err := m.http.Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, remoteMaxDownloadSize+1))
	if err != nil {
		return resp.StatusCode, nil, err
	}
	if len(data) > remoteMaxDownloadSize {
		return resp.StatusCode, nil, fmt.Errorf("响应过大")
	}
	return resp.StatusCode, data, nil
}

func (m *RemoteModule) callLive(ctx context.Context) (int, []byte, error) {
	path, err := liveAPIPath(m.region)
	if err != nil {
		return 0, nil, err
	}
	return m.apiRequest(ctx, http.MethodPost, path, bytes.NewReader(nil), "", true)
}

// persistLiveRecord 解析一次成功的 live 响应并写入 remote_live SQLite。
// HTTP 200 不等价于游戏服计分成功：endStatus 非 200 或缺少榜线字段时不落库。
func (m *RemoteModule) persistLiveRecord(ctx context.Context, body []byte) (*remotelive.Record, error) {
	if m.liveStore == nil {
		return nil, fmt.Errorf("remote_live store is nil")
	}
	serverType := serverTypeFromShort(m.region)
	now := time.Now()
	eventID := 0
	if m.masterdata != nil {
		if events, err := m.masterdata.Load("events.json", serverType); err == nil {
			eventID = currentEventID(events, now.UnixMilli())
		}
	}
	chapterNo := currentRemoteWLChapter(m.masterdata, serverType, eventID, now)
	fields, err := remotelive.ParseLiveResponse(body, chapterNo)
	if err != nil {
		return nil, err
	}
	if fields.EndStatus != nil && *fields.EndStatus != http.StatusOK {
		return nil, fmt.Errorf("live-end 游戏服返回 %d，本轮不计入记录", *fields.EndStatus)
	}
	if !fields.HasEventResult() {
		return nil, errors.New("响应体中未找到 afterEventRanking / afterEventPoint")
	}

	record := remotelive.Record{
		TS:             now.Unix(),
		AccountID:      remoteStringPtr(m.account),
		LiveID:         fields.LiveID,
		EventRank:      fields.EventRank,
		EventPoint:     fields.EventPoint,
		WLChapterNo:    fields.WLChapterNo,
		WLChapterRank:  fields.WLChapterRank,
		WLChapterPoint: fields.WLChapterPoint,
		Score:          fields.Score,
	}
	if eventID > 0 {
		record.EventID = remoteInt64Ptr(int64(eventID))
	}
	if err := m.liveStore.InsertRecord(ctx, m.region, m.account, record); err != nil {
		return nil, err
	}
	return &record, nil
}

func currentRemoteWLChapter(md *masterdata.Loader, serverType, eventID int, now time.Time) *int {
	if md == nil || eventID <= 0 {
		return nil
	}
	events, err := md.Load("events.json", serverType)
	if err != nil {
		return nil
	}
	worldBloom := false
	for _, event := range events {
		if intField(event, "id") == eventID {
			worldBloom = strField(event, "eventType") == "world_bloom"
			break
		}
	}
	if !worldBloom {
		return nil
	}
	chapters, err := md.Load("worldBlooms.json", serverType)
	if err != nil {
		return nil
	}
	nowMS := now.UnixMilli()
	var selected map[string]any
	var selectedStart int64 = -1
	var earliest map[string]any
	var earliestNo int64 = 1 << 62
	for _, chapter := range chapters {
		if intField(chapter, "eventId") != eventID {
			continue
		}
		chapterNo := int64(intField(chapter, "chapterNo"))
		if chapterNo > 0 && chapterNo < earliestNo {
			earliestNo = chapterNo
			earliest = chapter
		}
		start := int64(intField(chapter, "chapterStartAt"))
		if start <= nowMS && start >= selectedStart {
			selectedStart = start
			selected = chapter
		}
	}
	if selected == nil {
		selected = earliest
	}
	if selected == nil {
		return nil
	}
	chapterNo := int(intField(selected, "chapterNo"))
	if chapterNo <= 0 {
		return nil
	}
	return &chapterNo
}

func remoteInt64Ptr(value int64) *int64 { return &value }

func remoteStringPtr(value string) *string {
	if value == "" {
		return nil
	}
	return &value
}

func liveAPIPath(region string) (string, error) {
	region = strings.ToLower(strings.TrimSpace(region))
	if err := validateRegion(region); err != nil {
		return "", err
	}
	// 发送编码后的占位符；net/http 服务端解码后由 sekai-api 路由分支接管账号池选择。
	return "/api/" + url.PathEscape(region) + "/user/%25user_id%25/live/auto", nil
}

func (m *RemoteModule) startLive() {
	m.liveMu.Lock()
	if m.liveCancel != nil {
		m.liveMu.Unlock()
		return
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	now := time.Now()
	autoStopDeadline, _ := nextAutoStopDeadline(now, m.autoStop)
	m.liveCancel = cancel
	m.liveDone = done
	m.statsMu.Lock()
	m.stats = remoteStats{startedAt: now}
	m.statsMu.Unlock()
	m.liveMu.Unlock()
	go m.liveLoop(ctx, done, autoStopDeadline)
}

func (m *RemoteModule) stopLive() bool {
	m.liveMu.Lock()
	cancel := m.liveCancel
	done := m.liveDone
	m.liveMu.Unlock()
	if cancel == nil {
		return false
	}
	cancel()
	select {
	case <-done:
	case <-time.After(remoteStopTimeout):
	}
	return true
}

func (m *RemoteModule) isLiveRunning() bool {
	m.liveMu.Lock()
	defer m.liveMu.Unlock()
	return m.liveCancel != nil
}

func (m *RemoteModule) liveLoop(ctx context.Context, done chan struct{}, autoStopDeadline time.Time) {
	defer close(done)
	defer func() {
		m.liveMu.Lock()
		if m.liveDone == done {
			m.liveCancel = nil
			m.liveDone = nil
		}
		m.liveMu.Unlock()
		m.state.Update(nil, boolPtr(false))
	}()
	for {
		if autoStopReached(time.Now(), autoStopDeadline) {
			return
		}
		started := time.Now()
		m.statsMu.Lock()
		m.stats.rounds++
		m.statsMu.Unlock()
		status, body, err := m.callLive(ctx)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			m.recordLiveFailure(err.Error())
		} else if status < 200 || status >= 300 {
			m.recordLiveFailure(fmt.Sprintf("HTTP %d: %s", status, compactBody(body)))
		} else {
			if _, recordErr := m.persistLiveRecord(ctx, body); recordErr != nil {
				m.recordLiveFailure(recordErr.Error())
			} else {
				m.recordLiveSuccess()
			}
		}

		now := time.Now()
		if autoStopReached(now, autoStopDeadline) {
			return
		}
		wait := m.interval - now.Sub(started)
		if wait <= 0 {
			continue
		}
		if !autoStopDeadline.IsZero() {
			untilAutoStop := autoStopDeadline.Sub(now)
			if untilAutoStop < wait {
				wait = untilAutoStop
			}
		}
		timer := time.NewTimer(wait)
		select {
		case <-ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
		}
	}
}

func nextAutoStopDeadline(now time.Time, configured string) (time.Time, bool) {
	configured = strings.TrimSpace(configured)
	if len(configured) != len("HH:MM") || configured[2] != ':' {
		return time.Time{}, false
	}
	parsed, err := time.ParseInLocation("15:04", configured, now.Location())
	if err != nil {
		return time.Time{}, false
	}
	deadline := time.Date(now.Year(), now.Month(), now.Day(), parsed.Hour(), parsed.Minute(), 0, 0, now.Location())
	if deadline.Before(now) {
		deadline = deadline.AddDate(0, 0, 1)
	}
	return deadline, true
}

func autoStopReached(now, deadline time.Time) bool {
	return !deadline.IsZero() && !now.Before(deadline)
}

func (m *RemoteModule) recordLiveFailure(message string) {
	m.statsMu.Lock()
	m.stats.failed++
	m.stats.lastError = message
	m.statsMu.Unlock()
}

func (m *RemoteModule) recordLiveSuccess() {
	m.statsMu.Lock()
	m.stats.success++
	m.stats.lastError = ""
	m.stats.lastSuccessAt = time.Now()
	m.statsMu.Unlock()
}

func (m *RemoteModule) statsSnapshot() remoteStats {
	m.statsMu.RLock()
	defer m.statsMu.RUnlock()
	return m.stats
}

func validateRegion(region string) error {
	switch strings.ToLower(strings.TrimSpace(region)) {
	case "cn", "jp", "tw":
		return nil
	default:
		return fmt.Errorf("不支持的服务器：%s（仅 cn/jp/tw）", region)
	}
}

func contextOrBackground(ctx context.Context) context.Context {
	if ctx == nil {
		return context.Background()
	}
	return ctx
}

func boolPtr(value bool) *bool { return &value }

func onOff(value bool) string {
	if value {
		return "开启"
	}
	return "关闭"
}

func remoteNumberText(value any) string {
	switch v := value.(type) {
	case json.Number:
		return v.String()
	case float64:
		return fmt.Sprintf("%.0f", v)
	case int:
		return fmt.Sprintf("%d", v)
	case int64:
		return fmt.Sprintf("%d", v)
	case string:
		return v
	default:
		return ""
	}
}

func compactBody(body []byte) string {
	text := strings.TrimSpace(string(body))
	if len(text) > 200 {
		return text[:200] + "…"
	}
	if text == "" {
		return "无响应体"
	}
	return text
}
