package pjsk

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
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

type remoteTokenModule struct {
	parent  *RemoteModule
	pending *session.Manager
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

// HandleNotice 处理 pending token upload 的 OneBot offline_file 通知。
// 无 pending 状态时返回 nil，保证 Suite 上传链路仍可继续处理。
func (m *remoteTokenModule) HandleNotice(notice onebot.NoticeEvent) *onebot.ActionRequest {
	value, ok := m.pending.Get(tokenPendingKey(notice.UserID))
	if !ok {
		return nil
	}
	m.pending.Delete(tokenPendingKey(notice.UserID))
	if notice.NoticeType != "offline_file" || notice.File.URL == "" {
		return onebot.ReplyText(privateEvent(notice), "识别失败，请重新发送离线文件", false)
	}
	pending, ok := value.(tokenPending)
	if !ok {
		return onebot.ReplyText(privateEvent(notice), "识别失败，请重新发送离线文件", false)
	}
	content, err := m.download(context.Background(), notice.File.URL)
	if err != nil {
		return onebot.ReplyText(privateEvent(notice), "下载文件失败："+err.Error(), false)
	}
	data, err := m.upload(context.Background(), pending.region, content, notice.File.Name)
	if err != nil {
		return onebot.ReplyText(privateEvent(notice), "❌ 更新失败："+err.Error(), false)
	}
	return onebot.ReplyText(privateEvent(notice), formatTokenUpload(data), false)
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
		reloadNote = "\n⚠️ 已写入但热重载失败：" + stringValue(data["reloadError"], "")
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
