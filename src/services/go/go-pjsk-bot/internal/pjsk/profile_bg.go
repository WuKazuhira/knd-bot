package pjsk

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

// profileBGSettingsFile 返回共享的背景设置 JSON 路径，
// 对齐 pjsk-draw profile_bg 的 STATIC_PATH/profile_bg/settings.json。
func (m *ProfileModule) profileBGSettingsFile() string {
	return filepath.Join(m.staticDir, "profile_bg", "settings.json")
}

// profileBGImagePath 返回用户自定义背景图路径（{server}/{userid}.jpg）。
func (m *ProfileModule) profileBGImagePath(userid, server string) string {
	return filepath.Join(m.staticDir, "profile_bg", server, userid+".jpg")
}

// loadBGSettings 读取全部背景设置（键为 "{server}:{userid}"）。文件缺失返回空 map。
func (m *ProfileModule) loadBGSettings() map[string]map[string]any {
	raw, err := os.ReadFile(m.profileBGSettingsFile())
	if err != nil {
		return map[string]map[string]any{}
	}
	var data map[string]map[string]any
	if json.Unmarshal(raw, &data) != nil {
		return map[string]map[string]any{}
	}
	return data
}

// saveBGSettings 写回全部背景设置（紧凑 JSON，与 Python separators=(',',':') 对齐）。
func (m *ProfileModule) saveBGSettings(data map[string]map[string]any) error {
	if err := os.MkdirAll(filepath.Dir(m.profileBGSettingsFile()), 0o755); err != nil {
		return err
	}
	buf, err := json.Marshal(data)
	if err != nil {
		return err
	}
	return os.WriteFile(m.profileBGSettingsFile(), buf, 0o644)
}

// handleClearBg 实现 清除个人信息背景：删除用户自定义背景图，回退默认背景。
func (m *ProfileModule) handleClearBg(ctx context.Context, req router.Request) *onebot.ActionRequest {
	userid, _, errMsg := m.resolver.Resolve(ctx, req)
	if errMsg != "" {
		return onebot.ReplyText(req.Event, errMsg, true)
	}
	path := m.profileBGImagePath(userid, serverCode(int(req.Server)))
	if _, err := os.Stat(path); err == nil {
		_ = os.Remove(path)
	}
	return onebot.ReplyText(req.Event, "已清除个人信息背景，将使用默认背景", true)
}

// bgInt 从设置项取整数，缺省用 def。
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

// bgBool 从设置项取布尔。
func bgBool(s map[string]any, key string) bool {
	if v, ok := s[key].(bool); ok {
		return v
	}
	return false
}

// formatBGSettings 生成当前设置展示文案。
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

// parseTrailingInt 从 text 中 marker 之后提取首个连续数字，对齐 Python 的解析逻辑。
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

// handleAdjust 实现 调整个人信息：无参数展示当前设置；否则解析方向/模糊/透明并写回。
func (m *ProfileModule) handleAdjust(ctx context.Context, req router.Request) *onebot.ActionRequest {
	userid, _, errMsg := m.resolver.Resolve(ctx, req)
	if errMsg != "" {
		return onebot.ReplyText(req.Event, errMsg, true)
	}
	server := serverCode(int(req.Server))
	key := server + ":" + userid
	settings := m.loadBGSettings()

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
