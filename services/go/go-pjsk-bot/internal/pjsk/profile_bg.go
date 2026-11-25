package pjsk

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	_ "image/gif"
	"image/jpeg"
	_ "image/png"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

func (m *ProfileModule) profileBGSettingsFile() string {
	return filepath.Join(m.staticDir, "profile_bg", "settings.json")
}

func (m *ProfileModule) profileBGImagePath(userid, server string) string {
	return filepath.Join(m.staticDir, "profile_bg", server, userid+".jpg")
}

const profileBGMaxBytes = 32 << 20
const profileBGMaxSide = 3000

// 读写共享背景设置文件时串行化
var profileBGSettingsMu sync.Mutex

func (m *ProfileModule) ownProfileBGUser(ctx context.Context, req router.Request) (string, string) {
	if m.resolver != nil && m.resolver.store != nil {
		uid, _, exists, err := m.resolver.store.GetUserBind(ctx, req.Event.UserID, int(req.Server))
		if err != nil {
			return "", "查询你的绑定失败，请稍后重试"
		}
		if exists && uid > 0 {
			return strconv.FormatInt(uid, 10), ""
		}
	}
	return "", "请先绑定你在" + req.Server.Name() + "的账号，再修改个人信息背景"
}

var profileBGBlockedNetworks = []netip.Prefix{
	netip.MustParsePrefix("100.64.0.0/10"), // CGNAT 保留网段（含部分元数据服务）
	netip.MustParsePrefix("64:ff9b::/96"),
	netip.MustParsePrefix("64:ff9b:1::/48"),
	netip.MustParsePrefix("2002::/16"),
}

func profileBGPublicIPs(ctx context.Context, host string) ([]netip.Addr, error) {
	addrs, err := net.DefaultResolver.LookupNetIP(ctx, "ip", host)
	if err != nil {
		return nil, err
	}
	if len(addrs) == 0 {
		return nil, errors.New("image host has no IP addresses")
	}
	for _, addr := range addrs {
		addr = addr.Unmap()
		if !addr.IsGlobalUnicast() || addr.IsPrivate() || addr.Zone() != "" {
			return nil, errors.New("image host must have a public IP address")
		}
		for _, network := range profileBGBlockedNetworks {
			if network.Contains(addr) {
				return nil, errors.New("image host must have a public IP address")
			}
		}
	}
	return addrs, nil
}

func validProfileBGURL(u *url.URL) bool {
	return (u.Scheme == "http" || u.Scheme == "https") && u.Hostname() != "" && u.User == nil
}

func newProfileBGHTTPClient() *http.Client {
	dialer := &net.Dialer{Timeout: 10 * time.Second}
	return &http.Client{
		Timeout: 30 * time.Second,
		Transport: &http.Transport{
			IdleConnTimeout:     30 * time.Second,
			TLSHandshakeTimeout: 10 * time.Second,
			DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
				host, port, err := net.SplitHostPort(address)
				if err != nil {
					return nil, err
				}
				addrs, err := profileBGPublicIPs(ctx, host)
				if err != nil {
					return nil, err
				}
				for _, addr := range addrs {
					conn, dialErr := dialer.DialContext(ctx, network, net.JoinHostPort(addr.Unmap().String(), port))
					if dialErr == nil {
						return conn, nil
					}
					err = dialErr
				}
				return nil, err
			},
		},
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) >= 10 || !validProfileBGURL(req.URL) {
				return errors.New("invalid image redirect")
			}
			_, err := profileBGPublicIPs(req.Context(), req.URL.Hostname())
			return err
		},
	}
}

func (m *ProfileModule) handleUploadBg(ctx context.Context, req router.Request) *onebot.ActionRequest {
	userid, errMsg := m.ownProfileBGUser(ctx, req)
	if errMsg != "" {
		return onebot.ReplyText(req.Event, errMsg, true)
	}
	url := firstImageURL(req.Event.Message)
	if url == "" {
		return onebot.ReplyText(req.Event, "请在指令中附带一张图片作为背景", true)
	}
	httpClient := m.http
	if httpClient == nil {
		httpClient = newProfileBGHTTPClient()
	}
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil || !validProfileBGURL(httpReq.URL) {
		return onebot.ReplyText(req.Event, "图片链接无效", true)
	}
	resp, err := httpClient.Do(httpReq)
	if err != nil {
		return onebot.ReplyText(req.Event, "下载图片失败", true)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return onebot.ReplyText(req.Event, fmt.Sprintf("下载图片失败: HTTP %d", resp.StatusCode), true)
	}
	if resp.ContentLength > profileBGMaxBytes {
		return onebot.ReplyText(req.Event, "下载图片失败: 图片过大", true)
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, profileBGMaxBytes+1))
	if err != nil {
		return onebot.ReplyText(req.Event, "下载图片失败", true)
	}
	if len(raw) > profileBGMaxBytes {
		return onebot.ReplyText(req.Event, "下载图片失败: 图片过大", true)
	}
	cfg, _, err := image.DecodeConfig(bytes.NewReader(raw))
	if err != nil {
		return onebot.ReplyText(req.Event, "下载图片失败: 图片格式不受支持", true)
	}
	if cfg.Width <= 0 || cfg.Height <= 0 || cfg.Width > 10_000 || cfg.Height > 10_000 ||
		int64(cfg.Width)*int64(cfg.Height) > 40_000_000 {
		return onebot.ReplyText(req.Event, "下载图片失败: 图片像素尺寸过大", true)
	}
	img, _, err := image.Decode(bytes.NewReader(raw))
	if err != nil {
		return onebot.ReplyText(req.Event, "下载图片失败: 图片格式不受支持", true)
	}
	if ctx.Err() != nil {
		return nil
	}
	if err := m.saveProfileBG(userid, serverCode(int(req.Server)), img); err != nil {
		return onebot.ReplyText(req.Event, "保存背景失败: "+err.Error(), true)
	}
	return onebot.ReplyText(req.Event, "背景设置成功！使用「cn调整个人信息」可以调整方向、模糊、透明度", true)
}

func firstImageURL(message onebot.Message) string {
	for _, segment := range message {
		if segment.Type != "image" {
			continue
		}
		for _, key := range []string{"url", "file"} {
			value, ok := segment.Data[key].(string)
			if !ok {
				continue
			}
			value = strings.TrimSpace(value)
			if strings.HasPrefix(value, "https://") || strings.HasPrefix(value, "http://") {
				return value
			}
		}
	}
	return ""
}

func (m *ProfileModule) saveProfileBG(userid, server string, img image.Image) error {
	img = resizeProfileBG(img, profileBGMaxSide)
	var encoded bytes.Buffer
	if err := jpeg.Encode(&encoded, img, &jpeg.Options{Quality: 85}); err != nil {
		return err
	}
	profileBGSettingsMu.Lock()
	defer profileBGSettingsMu.Unlock()
	settings, err := m.loadBGSettings()
	if err != nil {
		return err
	}
	key := server + ":" + userid
	if settings[key] == nil {
		settings[key] = map[string]any{}
	}
	if _, ok := settings[key]["vertical"]; !ok {
		settings[key]["vertical"] = false
	}
	if _, ok := settings[key]["blur"]; !ok {
		settings[key]["blur"] = 1
	}
	if _, ok := settings[key]["alpha"]; !ok {
		settings[key]["alpha"] = 180
	}
	if err := m.saveBGSettings(settings); err != nil {
		return err
	}
	return writeAtomic(m.profileBGImagePath(userid, server), encoded.Bytes())
}

func resizeProfileBG(src image.Image, maxSide int) image.Image {
	bounds := src.Bounds()
	width, height := bounds.Dx(), bounds.Dy()
	if width <= maxSide && height <= maxSide {
		return src
	}
	ratio := float64(maxSide) / float64(width)
	if height > width {
		ratio = float64(maxSide) / float64(height)
	}
	newWidth := max(1, int(float64(width)*ratio))
	newHeight := max(1, int(float64(height)*ratio))
	dst := image.NewRGBA(image.Rect(0, 0, newWidth, newHeight))
	for y := 0; y < newHeight; y++ {
		sy := bounds.Min.Y + y*height/newHeight
		for x := 0; x < newWidth; x++ {
			sx := bounds.Min.X + x*width/newWidth
			dst.Set(x, y, src.At(sx, sy))
		}
	}
	return dst
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// 调用方需持有 profileBGSettingsMu
func (m *ProfileModule) loadBGSettings() (map[string]map[string]any, error) {
	settings := make(map[string]map[string]any)
	raw, err := os.ReadFile(m.profileBGSettingsFile())
	if errors.Is(err, os.ErrNotExist) {
		return settings, nil
	}
	if err != nil {
		return nil, err
	}
	if err := json.Unmarshal(raw, &settings); err != nil {
		return nil, err
	}
	if settings == nil {
		settings = make(map[string]map[string]any)
	}
	return settings, nil
}

func (m *ProfileModule) saveBGSettings(data map[string]map[string]any) error {
	buf, err := json.Marshal(data)
	if err != nil {
		return err
	}
	return writeAtomicWithMode(m.profileBGSettingsFile(), buf, 0o644)
}

func (m *ProfileModule) handleClearBg(ctx context.Context, req router.Request) *onebot.ActionRequest {
	userid, errMsg := m.ownProfileBGUser(ctx, req)
	if errMsg != "" {
		return onebot.ReplyText(req.Event, errMsg, true)
	}
	path := m.profileBGImagePath(userid, serverCode(int(req.Server)))
	profileBGSettingsMu.Lock()
	err := os.Remove(path)
	profileBGSettingsMu.Unlock()
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return onebot.ReplyText(req.Event, "清除个人信息背景失败，请稍后重试", true)
	}
	return onebot.ReplyText(req.Event, "已清除个人信息背景，将使用默认背景", true)
}

func bgInt(s map[string]any, key string, def int) int {
	if v, ok := s[key]; ok {
		switch n := v.(type) {
		case float64:
			return int(n)
		case int:
			return n
		}
	}
	return def
}

func bgBool(s map[string]any, key string) bool {
	if v, ok := s[key].(bool); ok {
		return v
	}
	return false
}

func formatBGSettings(s map[string]any) string {
	vertical := bgBool(s, "vertical")
	blur := bgInt(s, "blur", 1)
	if blur == 0 {
		blur = 1
	}
	alpha := bgInt(s, "alpha", 180)
	if alpha == 0 {
		alpha = 180
	}
	transparency := 100 - alpha*100/255
	dir := "横屏"
	if vertical {
		dir = "竖屏"
	}
	return fmt.Sprintf("当前个人信息设置:\n方向: %s\n模糊度: %d\n透明度: %d%%\n---\n"+
		"调整方向: 调整个人信息 竖屏/横屏\n调整模糊: 调整个人信息 模糊 0~10\n调整透明: 调整个人信息 透明 0~100",
		dir, blur, transparency)
}

func parseTrailingInt(text, marker string) (int, bool) {
	idx := strings.Index(text, marker)
	if idx < 0 {
		return 0, false
	}
	rest := strings.TrimSpace(text[idx+len(marker):])
	num := ""
	for _, c := range rest {
		if c >= '0' && c <= '9' {
			num += string(c)
		} else if num != "" {
			break
		}
	}
	if num == "" {
		return 0, false
	}
	n := 0
	for _, c := range num {
		n = n*10 + int(c-'0')
	}
	return n, true
}

func (m *ProfileModule) handleAdjust(ctx context.Context, req router.Request) *onebot.ActionRequest {
	userid, errMsg := m.ownProfileBGUser(ctx, req)
	if errMsg != "" {
		return onebot.ReplyText(req.Event, errMsg, true)
	}
	server := serverCode(int(req.Server))
	key := server + ":" + userid
	profileBGSettingsMu.Lock()
	defer profileBGSettingsMu.Unlock()
	settings, err := m.loadBGSettings()
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}

	args := strings.TrimSpace(req.Arg)
	if args == "" {
		return onebot.ReplyText(req.Event, formatBGSettings(settings[key]), true)
	}

	var vertical *bool
	var blur, alpha *int

	if strings.Contains(args, "竖屏") || strings.Contains(args, "竖向") || strings.Contains(args, "竖版") {
		v := true
		vertical = &v
	} else if strings.Contains(args, "横屏") || strings.Contains(args, "横向") || strings.Contains(args, "横版") {
		v := false
		vertical = &v
	}
	if n, ok := parseTrailingInt(args, "模糊"); ok {
		if n < 0 {
			n = 0
		}
		if n > 10 {
			n = 10
		}
		blur = &n
	}
	if n, ok := parseTrailingInt(args, "透明"); ok {
		if n < 0 {
			n = 0
		}
		if n > 100 {
			n = 100
		}
		a := (100 - n) * 255 / 100
		alpha = &a
	}

	if vertical == nil && blur == nil && alpha == nil {
		return onebot.ReplyText(req.Event, "无法识别参数，请使用: 竖屏/横屏/模糊N/透明N", true)
	}

	if settings[key] == nil {
		settings[key] = map[string]any{}
	}
	if vertical != nil {
		settings[key]["vertical"] = *vertical
	}
	if blur != nil {
		settings[key]["blur"] = *blur
	}
	if alpha != nil {
		settings[key]["alpha"] = *alpha
	}
	if err := m.saveBGSettings(settings); err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyText(req.Event, "已更新个人信息设置：\n"+formatBGSettings(settings[key]), true)
}
