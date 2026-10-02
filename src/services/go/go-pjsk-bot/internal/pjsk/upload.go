package pjsk

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/session"
	"github.com/vmihailenco/msgpack/v5"
)

const uploadStateTTL = 10 * time.Minute

// UploadModule 迁移 Suite 原始文件上传：命令建立私聊状态，file/offline_file 完成解包落盘。
type UploadModule struct {
	dataDir    string
	state      *session.Manager
	http       *http.Client
	fileClient tokenFileClient
	tokenPeer  *remoteTokenModule
}

func NewUploadModule(dataDir string) *UploadModule {
	return &UploadModule{
		dataDir: dataDir,
		state:   session.New(time.Minute),
		http:    &http.Client{Timeout: 120 * time.Second},
	}
}

func (m *UploadModule) Close() { m.state.Close() }

func (m *UploadModule) Register(r *router.Router) {
	r.Register("pjskupload", []string{"上传用户信息"}, m.handleCommand)
}

func (m *UploadModule) handleCommand(_ context.Context, req router.Request) *onebot.ActionRequest {
	if req.Event.IsGroup() {
		return onebot.ReplyText(req.Event, "请在私聊内使用此功能！", true)
	}
	uploadSessionsMu.Lock()
	canceled := cancelUploadSession(m.state, uploadKey(req.Event.UserID))
	if m.tokenPeer != nil {
		canceled = cancelUploadSession(m.tokenPeer.pending, tokenPendingKey(req.Event.UserID)) || canceled
	}
	m.state.Set(uploadKey(req.Event.UserID), int(req.Server), uploadStateTTL)
	uploadSessionsMu.Unlock()
	message := "请发送原始" + serverDirName(int(req.Server)) + "数据包文件（10分钟内有效）"
	if canceled {
		message = "已取消之前的上传会话。\n" + message
	}
	return onebot.ReplyText(req.Event, message, false)
}

// HandleNotice 供 OneBot notice handler 调用；非目标通知返回 nil。
func (m *UploadModule) HandleNotice(notice onebot.NoticeEvent) *onebot.ActionRequest {
	if notice.NoticeType != "offline_file" || notice.GroupID != 0 {
		return nil
	}
	value, ok := takeUploadSession(m.state, uploadKey(notice.UserID))
	if !ok {
		return nil
	}
	if !validTokenFileURL(notice.File.URL) {
		return onebot.ReplyText(privateEvent(notice), "识别失败，请重新发送离线文件", false)
	}
	server, ok := value.(int)
	if !ok {
		return onebot.ReplyText(privateEvent(notice), "识别失败，请重新发送离线文件", false)
	}
	return m.saveFile(privateEvent(notice), notice.File.URL, server)
}

// SetFileClient 必须在接收事件前调用，用于解析私聊文件的 file_id。
func (m *UploadModule) SetFileClient(client *onebot.Client) { m.fileClient = client }

// HandleMessage 只消费有 Suite 会话的私聊 file；与 offline_file 共享一次性消费。
func (m *UploadModule) HandleMessage(event onebot.MessageEvent) *onebot.ActionRequest {
	if event.MessageType != "private" {
		return nil
	}
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
	value, ok := takeUploadSession(m.state, uploadKey(event.UserID))
	if !ok {
		return nil
	}
	server, ok := value.(int)
	if !ok {
		return onebot.ReplyText(event, "识别失败，请重新发起上传", false)
	}
	fileURL, _ := file.Data["url"].(string)
	if validTokenFileURL(fileURL) {
		return m.saveFile(event, fileURL, server)
	}
	// 不读取 OneBot 容器本地 file://；只从 file_id 获取 HTTP(S) 直链。
	fileID, _ := file.Data["file_id"].(string)
	if fileID == "" || m.fileClient == nil {
		return onebot.ReplyText(event, "文件链接无效，请重新发起上传", false)
	}
	go m.savePrivateFileAsync(event.UserID, fileID, server)
	return onebot.ReplyText(event, "已收到文件，正在获取下载链接并解析；处理结果稍后私聊回复。", false)
}

func (m *UploadModule) saveFile(event onebot.MessageEvent, fileURL string, server int) *onebot.ActionRequest {
	userID, err := m.saveData(context.Background(), fileURL, server)
	if err != nil {
		return onebot.ReplyText(event, "出错了，可能是文件不符合要求！请重新发起上传。", false)
	}
	return onebot.ReplyText(event, fmt.Sprintf("识别成功，%s用户(%s)的信息已记录！", serverDirName(server), userID), false)
}

func (m *UploadModule) savePrivateFileAsync(userID int64, fileID string, server int) {
	event := onebot.MessageEvent{MessageType: "private", UserID: userID}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	result, err := m.fileClient.Call(ctx, "get_private_file_url", map[string]any{"file_id": fileID})
	cancel()
	var response *onebot.ActionRequest
	var info struct {
		URL string `json:"url"`
	}
	if err != nil || json.Unmarshal(result, &info) != nil || !validTokenFileURL(info.URL) {
		response = onebot.ReplyText(event, "获取文件直链失败，请重新发起上传", false)
	} else {
		response = m.saveFile(event, info.URL, server)
	}
	sendCtx, sendCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer sendCancel()
	_ = m.fileClient.SendContext(sendCtx, response)
}

func uploadKey(userID int64) string { return "pjskupload:" + strconv.FormatInt(userID, 10) }

func privateEvent(notice onebot.NoticeEvent) onebot.MessageEvent {
	return onebot.MessageEvent{Time: notice.Time, SelfID: notice.SelfID, MessageType: "private", UserID: notice.UserID}
}

func (m *UploadModule) saveData(ctx context.Context, url string, server int) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return "", err
	}
	resp, err := m.http.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("file download HTTP %d", resp.StatusCode)
	}
	content, err := io.ReadAll(io.LimitReader(resp.Body, 64<<20+1))
	if err != nil {
		return "", err
	}
	if len(content) > 64<<20 {
		return "", fmt.Errorf("file too large")
	}
	data, err := decodeSuite(content)
	if err != nil {
		return "", err
	}
	userID, ok := nestedString(data, "user", "userRegistration", "userId")
	if !ok || userID == "" {
		return "", fmt.Errorf("suite user id missing")
	}
	if numericID, err := strconv.ParseUint(userID, 10, 64); err != nil || numericID == 0 {
		return "", fmt.Errorf("invalid Suite user id")
	} else {
		userID = strconv.FormatUint(numericID, 10)
	}
	encoded, err := json.Marshal(data)
	if err != nil {
		return "", err
	}
	dir := filepath.Join(m.dataDir, "ondemand", serverDirName(server))
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	path := filepath.Join(dir, userID+".json")
	tmp, err := os.CreateTemp(dir, ".suite-*.json")
	if err != nil {
		return "", err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if err := tmp.Chmod(0o600); err != nil {
		_ = tmp.Close()
		return "", err
	}
	if _, err := tmp.Write(encoded); err != nil {
		_ = tmp.Close()
		return "", err
	}
	if err := tmp.Close(); err != nil {
		return "", err
	}
	if err := os.Rename(tmpName, path); err != nil {
		return "", err
	}
	return userID, nil
}

func decodeSuite(encrypted []byte) (map[string]any, error) {
	block, err := aes.NewCipher([]byte("g2fcC0ZczN9MTJ61"))
	if err != nil {
		return nil, err
	}
	if len(encrypted) == 0 || len(encrypted)%aes.BlockSize != 0 {
		return nil, fmt.Errorf("invalid encrypted Suite length")
	}
	plain := make([]byte, len(encrypted))
	cipher.NewCBCDecrypter(block, []byte("msx3IV0i9XE5uYZ1")).CryptBlocks(plain, encrypted)
	if len(plain) == 0 {
		return nil, fmt.Errorf("empty Suite payload")
	}
	pad := int(plain[len(plain)-1])
	if pad <= 0 || pad > aes.BlockSize || pad > len(plain) {
		return nil, fmt.Errorf("invalid Suite padding")
	}
	for _, b := range plain[len(plain)-pad:] {
		if int(b) != pad {
			return nil, fmt.Errorf("invalid Suite padding")
		}
	}
	plain = plain[:len(plain)-pad]
	var data map[string]any
	if err := msgpack.Unmarshal(plain, &data); err != nil {
		return nil, fmt.Errorf("decode Suite msgpack: %w", err)
	}
	return data, nil
}

func nestedString(data map[string]any, keys ...string) (string, bool) {
	var current any = data
	for _, key := range keys {
		block, ok := current.(map[string]any)
		if !ok {
			return "", false
		}
		current, ok = block[key]
		if !ok {
			return "", false
		}
	}
	switch value := current.(type) {
	case string:
		return strings.TrimSpace(value), true
	case []byte:
		return strings.TrimSpace(string(value)), true
	case fmt.Stringer:
		return strings.TrimSpace(value.String()), true
	default:
		return strings.TrimSpace(fmt.Sprint(value)), true
	}
}
