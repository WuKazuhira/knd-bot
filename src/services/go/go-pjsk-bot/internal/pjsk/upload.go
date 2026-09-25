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

// UploadModule 迁移 Suite 原始文件上传：命令建立私聊状态，offline_file 通知完成解包落盘。
type UploadModule struct {
	dataDir string
	state   *session.Manager
	http    *http.Client
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
	m.state.Set(uploadKey(req.Event.UserID), int(req.Server), uploadStateTTL)
	return onebot.ReplyText(req.Event, "请发送原始"+serverDirName(int(req.Server))+"数据包文件（10分钟内有效）", false)
}

// HandleNotice 供 OneBot notice handler 调用；非目标通知返回 nil。
func (m *UploadModule) HandleNotice(notice onebot.NoticeEvent) *onebot.ActionRequest {
	if notice.NoticeType != "offline_file" || notice.GroupID != 0 {
		return nil
	}
	value, ok := m.state.Get(uploadKey(notice.UserID))
	if !ok {
		return nil
	}
	m.state.Delete(uploadKey(notice.UserID))
	if notice.File.URL == "" {
		return onebot.ReplyText(privateEvent(notice), "识别失败，请重新发送离线文件", false)
	}
	server, ok := value.(int)
	if !ok {
		return onebot.ReplyText(privateEvent(notice), "识别失败，请重新发送离线文件", false)
	}
	userID, err := m.saveData(context.Background(), notice.File.URL, server)
	if err != nil {
		return onebot.ReplyText(privateEvent(notice), "出错了，可能是文件不符合要求！", false)
	}
	return onebot.ReplyText(privateEvent(notice), fmt.Sprintf("识别成功，%s用户(%s)的信息已记录！", serverDirName(server), userID), false)
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
