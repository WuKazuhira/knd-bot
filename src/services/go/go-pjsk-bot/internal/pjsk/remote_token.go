package pjsk

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/url"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/session"
)

const tokenWarnDays = 30

type tokenPending struct {
	region string
}

type tokenFileClient interface {
	Call(context.Context, string, map[string]any) (json.RawMessage, error)
	SendContext(context.Context, *onebot.ActionRequest) error
}

type remoteTokenModule struct {
	parent     *RemoteModule
	pending    *session.Manager
	fileClient tokenFileClient
}

func newRemoteTokenModule(parent *RemoteModule) *remoteTokenModule {
	return &remoteTokenModule{
		parent:  parent,
		pending: session.New(time.Minute),
	}
}

func (m *remoteTokenModule) Close() {
	if m != nil && m.pending != nil {
		m.pending.Close()
	}
}

func (m *remoteTokenModule) Register(r *router.Router) {
	r.Register("pjsk_remote_token", []string{"pjsktoken状态", "pjsktoken", "token状态"}, m.handleStatus)
	r.Register("pjsk_remote_token_upload", []string{"pjsk上传token", "上传token", "更新token"}, m.handleUpload)
}

func (m *remoteTokenModule) handleStatus(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if !m.parent.isSuper(req.Event) {
		return nil
	}
	region, err := commandRegion(req.Arg, m.parent.region)
	if err != nil {
		return onebot.ReplyText(req.Event, "查询失败："+err.Error(), false)
	}
	data, err := m.query(ctx, region)
	if err != nil {
		return onebot.ReplyText(req.Event, "查询失败："+err.Error(), false)
	}
	return onebot.ReplyText(req.Event, formatTokenStatus(data), false)
}

func (m *remoteTokenModule) handleUpload(_ context.Context, req router.Request) *onebot.ActionRequest {
	if !m.parent.isSuper(req.Event) {
		return nil
	}
	if req.Event.IsGroup() {
		return onebot.ReplyText(req.Event, "请在私聊内使用此功能！", true)
	}
	region, err := commandRegion(req.Arg, m.parent.region)
	if err != nil {
		return onebot.ReplyText(req.Event, "无法开始上传："+err.Error(), false)
	}
	m.pending.Set(tokenPendingKey(req.Event.UserID), tokenPending{region: region}, remoteUploadTTL)
	return onebot.ReplyText(req.Event,
		fmt.Sprintf("请私聊发送 %s 服的登录抓包文件（10分钟内有效）。文件将上传到 sekai-api 并校验 accessToken 有效期。",
			strings.ToUpper(region)), false)
}

// SetTokenFileClient 连接 OneBot 文件直链接口，必须在开始接收事件前调用。
func (m *RemoteModule) SetTokenFileClient(client *onebot.Client) {
	if m != nil && m.token != nil {
		m.token.fileClient = client
	}
}

// HandleTokenUploadMessage 仅处理超级用户已有上传会话的私聊文件消息。
func (m *RemoteModule) HandleTokenUploadMessage(event onebot.MessageEvent) *onebot.ActionRequest {
	if m == nil || m.token == nil || event.MessageType != "private" || !m.isSuper(event) {
		return nil
	}
	return m.token.HandleMessage(event)
}

func (m *remoteTokenModule) HandleMessage(event onebot.MessageEvent) *onebot.ActionRequest {
	var file *onebot.Segment
	for i := range event.Message {
		if event.Message[i].Type == "file" {
			file = &event.Message[i]
			break
		}
	}
	if file == nil {
		return nil
	}
	key := tokenPendingKey(event.UserID)
	value, ok := m.pending.Get(key)
	if !ok {
		return nil
	}
	m.pending.Delete(key)
	pending, ok := value.(tokenPending)
	if !ok {
		return onebot.ReplyText(event, "识别失败，请重新发起上传", false)
	}
	fileURL, _ := file.Data["url"].(string)
	filename, _ := file.Data["name"].(string)
	if filename == "" {
		filename, _ = file.Data["file"].(string)
	}
	if validTokenFileURL(fileURL) {
		return m.uploadFile(event, pending.region, fileURL, filename)
	}
	// LLBot 将私聊文件的 url 上报为其自身容器的 file:// 路径。
	// Go 容器不读取该本地路径，只用同一条消息的 file_id 请求可下载的直链。
	fileID, _ := file.Data["file_id"].(string)
	if fileID == "" || m.fileClient == nil {
		return onebot.ReplyText(event, "文件链接无效，请重新发起上传", false)
	}
	go m.uploadPrivateFileAsync(event.UserID, pending.region, fileID, filename)
	return onebot.ReplyText(event, "已收到文件，正在获取下载链接并校验；处理结果稍后私聊回复。", false)
}

func (m *remoteTokenModule) uploadPrivateFileAsync(userID int64, region, fileID, filename string) {
	event := onebot.MessageEvent{MessageType: "private", UserID: userID}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	result, err := m.fileClient.Call(ctx, "get_private_file_url", map[string]any{"file_id": fileID})
	cancel()
	var response *onebot.ActionRequest
	if err != nil {
		response = onebot.ReplyText(event, "获取文件直链失败，请重新发起上传", false)
	} else {
		var info struct {
			URL string `json:"url"`
		}
		if json.Unmarshal(result, &info) != nil || !validTokenFileURL(info.URL) {
			response = onebot.ReplyText(event, "文件直链无效，请重新发起上传", false)
		} else {
			response = m.uploadFile(event, region, info.URL, filename)
		}
	}
	sendCtx, sendCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer sendCancel()
	_ = m.fileClient.SendContext(sendCtx, response)
}

func validTokenFileURL(raw string) bool {
	parsed, err := url.Parse(raw)
	return err == nil && (parsed.Scheme == "http" || parsed.Scheme == "https") && parsed.Host != "" && parsed.User == nil
}

// HandleNotice 处理 pending token upload 的 OneBot offline_file 通知。
// 无 pending 状态时返回 nil，保证 Suite 上传链路仍可继续处理。
func (m *remoteTokenModule) HandleNotice(notice onebot.NoticeEvent) *onebot.ActionRequest {
	if notice.NoticeType != "offline_file" || notice.GroupID != 0 {
		return nil
	}
	value, ok := m.pending.Get(tokenPendingKey(notice.UserID))
	if !ok {
		return nil
	}
	m.pending.Delete(tokenPendingKey(notice.UserID))
	if notice.File.URL == "" {
		return onebot.ReplyText(privateEvent(notice), "识别失败，请重新发送离线文件", false)
	}
	pending, ok := value.(tokenPending)
	if !ok {
		return onebot.ReplyText(privateEvent(notice), "识别失败，请重新发送离线文件", false)
	}
	return m.uploadFile(privateEvent(notice), pending.region, notice.File.URL, notice.File.Name)
}

func (m *remoteTokenModule) uploadFile(event onebot.MessageEvent, region, fileURL, filename string) *onebot.ActionRequest {
	content, err := m.download(context.Background(), fileURL)
	if err != nil {
		// 下载错误可能包含带签名参数的文件 URL，不回显给用户或日志。
		return onebot.ReplyText(event, "下载文件失败，请重新发起上传", false)
	}
	data, err := m.upload(context.Background(), region, content, filename)
	if err != nil {
		return onebot.ReplyText(event, safeTokenUploadError(err), false)
	}
	return onebot.ReplyText(event, formatTokenUpload(data), false)
}

// safeTokenUploadError 只返回固定分类，不透传可能包含凭据的上游错误原文。
func safeTokenUploadError(err error) string {
	message := err.Error()
	switch {
	case strings.Contains(message, "accessToken 已于") && strings.Contains(message, "过期"):
		return "❌ 更新失败：文件中的 accessToken 已过期，未写入。"
	case strings.Contains(message, "无法解析上传文件"):
		return "❌ 更新失败：文件无法解析，请检查文件格式。"
	case strings.Contains(message, "文件中没有 accessToken"):
		return "❌ 更新失败：文件中没有 accessToken。"
	case strings.Contains(message, "accessToken 无法解析"):
		return "❌ 更新失败：accessToken 格式无效。"
	case strings.Contains(message, "游戏登录或客户端初始化失败"):
		return "❌ 更新失败：游戏侧校验失败，未写入。"
	case strings.Contains(message, "账号保存失败"):
		return "❌ 更新失败：账号保存失败，请联系管理员。"
	default:
		return "❌ 更新失败，请确认文件有效且未过期；如持续失败请联系管理员。"
	}
}

func (m *remoteTokenModule) query(ctx context.Context, region string) (map[string]any, error) {
	if m.parent.apiToken == "" {
		return nil, fmt.Errorf("SEKAI_API_TOKEN 未配置")
	}
	status, body, err := m.parent.apiRequest(ctx, http.MethodGet, "/token/"+region+"/status", nil, "", true)
	if err != nil {
		return nil, fmt.Errorf("连接 sekai-api 失败：%w", err)
	}
	data := decodeObject(body)
	if status < 200 || status >= 300 {
		return nil, apiError(data, body, status)
	}
	return data, nil
}

func (m *remoteTokenModule) download(ctx context.Context, fileURL string) ([]byte, error) {
	ctx = contextOrBackground(ctx)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, fileURL, nil)
	if err != nil {
		return nil, err
	}
	resp, err := m.parent.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	content, err := io.ReadAll(io.LimitReader(resp.Body, remoteMaxDownloadSize+1))
	if err != nil {
		return nil, err
	}
	if len(content) > remoteMaxDownloadSize {
		return nil, fmt.Errorf("文件过大（>32MB）")
	}
	if len(content) == 0 {
		return nil, fmt.Errorf("文件为空")
	}
	return content, nil
}

func (m *remoteTokenModule) upload(ctx context.Context, region string, content []byte, filename string) (map[string]any, error) {
	if m.parent.apiToken == "" {
		return nil, fmt.Errorf("SEKAI_API_TOKEN 未配置")
	}
	if filename == "" || filename == "." || filename == string(filepath.Separator) {
		filename = "upload.bin"
	}
	filename = filepath.Base(filename)
	var body bytes.Buffer
	form := multipart.NewWriter(&body)
	part, err := form.CreateFormFile("file", filename)
	if err != nil {
		return nil, err
	}
	if _, err := part.Write(content); err != nil {
		return nil, err
	}
	if err := form.Close(); err != nil {
		return nil, err
	}
	status, response, err := m.parent.apiRequest(ctx, http.MethodPost, "/token/"+region+"/upload", &body, form.FormDataContentType(), true)
	if err != nil {
		return nil, fmt.Errorf("连接 sekai-api 失败：%w", err)
	}
	data := decodeObject(response)
	if status < 200 || status >= 300 {
		return nil, apiError(data, response, status)
	}
	return data, nil
}

func commandRegion(arg, defaultRegion string) (string, error) {
	region := strings.ToLower(strings.TrimSpace(arg))
	if region == "" {
		region = strings.ToLower(strings.TrimSpace(defaultRegion))
	}
	if err := validateRegion(region); err != nil {
		return "", err
	}
	return region, nil
}

// PendingTokenUploadShape 仅记录待上传会话的消息结构，不返回消息、字段值或文件信息。
func (m *RemoteModule) PendingTokenUploadShape(event onebot.MessageEvent) (string, bool) {
	if m == nil || m.token == nil || event.MessageType != "private" || !m.isSuper(event) {
		return "", false
	}
	if _, ok := m.token.pending.Get(tokenPendingKey(event.UserID)); !ok {
		return "", false
	}
	var parts []string
	for i, segment := range event.Message {
		if i == 8 {
			parts = append(parts, "more")
			break
		}
		segmentType := "other"
		switch segment.Type {
		case "text", "file", "image", "video", "record", "at", "reply", "forward":
			segmentType = segment.Type
		}
		_, hasURL := segment.Data["url"]
		_, hasFile := segment.Data["file"]
		_, hasID := segment.Data["id"]
		_, hasName := segment.Data["name"]
		parts = append(parts, fmt.Sprintf("%s(url=%t,file=%t,id=%t,name=%t)", segmentType, hasURL, hasFile, hasID, hasName))
	}
	return strings.Join(parts, ","), true
}

func tokenPendingKey(userID int64) string { return "pjsktoken:" + strconv.FormatInt(userID, 10) }

func decodeObject(body []byte) map[string]any {
	var data map[string]any
	if err := json.Unmarshal(body, &data); err != nil || data == nil {
		return map[string]any{}
	}
	return data
}

func apiError(data map[string]any, body []byte, status int) error {
	if message, ok := data["error"].(string); ok && strings.TrimSpace(message) != "" {
		return fmt.Errorf("%s", message)
	}
	return fmt.Errorf("HTTP %d：%s", status, compactBody(body))
}

func formatTokenStatus(data map[string]any) string {
	region := strings.ToUpper(stringValue(data["server"], "?"))
	text := fmt.Sprintf("服务器：%s\nuserId：%s\n有效期至：%s\n剩余：%s 天",
		region,
		stringValue(data["userId"], "?"),
		stringValue(data["expiresAtText"], "?"),
		stringValue(data["remainingDays"], "?"))
	if remaining, ok := numericValue(data["remainingDays"]); ok && remaining <= tokenWarnDays {
		text += "\n⚠️ 即将过期，请尽快更新"
	}
	return text
}

func formatTokenUpload(data map[string]any) string {
	reloadNote := ""
	if reloaded, ok := data["reloaded"].(bool); ok && !reloaded {
		// 上游诊断可能夹带凭据，不在机器人回复中透传原始错误。
		reloadNote = "\n⚠️ 已写入但热重载失败，请联系管理员"
	}
	return fmt.Sprintf("✅ %s accessToken 已更新\nuserId：%s\n有效期至：%s\n剩余：%s 天\n来源格式：%s%s",
		strings.ToUpper(stringValue(data["server"], "?")),
		stringValue(data["userId"], "?"),
		stringValue(data["expiresAtText"], "?"),
		stringValue(data["remainingDays"], "?"),
		stringValue(data["source"], "?"),
		reloadNote)
}

func stringValue(value any, fallback string) string {
	if value == nil {
		return fallback
	}
	switch v := value.(type) {
	case string:
		if strings.TrimSpace(v) == "" {
			return fallback
		}
		return v
	case json.Number:
		return v.String()
	case float64:
		return fmt.Sprintf("%.0f", v)
	case int:
		return strconv.Itoa(v)
	case int64:
		return strconv.FormatInt(v, 10)
	default:
		return fmt.Sprint(v)
	}
}

func numericValue(value any) (int, bool) {
	switch v := value.(type) {
	case json.Number:
		n, err := v.Int64()
		return int(n), err == nil
	case float64:
		return int(v), true
	case int:
		return v, true
	case int64:
		return int(v), true
	case string:
		n, err := strconv.Atoi(strings.TrimSpace(v))
		return n, err == nil
	default:
		return 0, false
	}
}
