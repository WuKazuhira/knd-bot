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

// SKAPIModule 持久化并切换榜线采集所用的 API 模式。
// 状态文件与 Python 基线共用 data/pjsk/ondemand/sk_api_state.json。
type SKAPIModule struct {
	path   string
	supers map[int64]bool
}

func NewSKAPIModule(dataDir string, supers []int64) *SKAPIModule {
	set := make(map[int64]bool, len(supers))
	for _, id := range supers {
		set[id] = true
	}
	return &SKAPIModule{path: filepath.Join(dataDir, "ondemand", "sk_api_state.json"), supers: set}
}

func (m *SKAPIModule) Register(r *router.Router) {
	r.Register("SKAPI切换", []string{"skapi切换", "SK API切换", "sk api切换", "SKAPI切换新", "SKAPI切换旧", "skapi切换新", "skapi切换旧"}, m.handle)
}

func (m *SKAPIModule) handle(_ context.Context, req router.Request) *onebot.ActionRequest {
	if !m.supers[req.Event.UserID] {
		return onebot.ReplyText(req.Event, "仅超级用户可用", true)
	}
	current := m.load()
	raw := strings.ToLower(strings.ReplaceAll(strings.TrimSpace(req.Arg), " ", ""))
	if raw == "" {
		switch {
		case strings.HasSuffix(strings.ToLower(strings.ReplaceAll(req.RawCmd, " ", "")), "新"):
			raw = "新"
		case strings.HasSuffix(strings.ToLower(strings.ReplaceAll(req.RawCmd, " ", "")), "旧"):
			raw = "旧"
		}
	}
	if raw == "" {
		return onebot.ReplyText(req.Event, fmt.Sprintf("当前 SK 榜线来源：%s\n用法：SKAPI切换 新 / SKAPI切换 旧", apiLabel(current)), true)
	}
	mode := ""
	switch raw {
	case "新", "新版", "新api", "new":
		mode = "new"
	case "旧", "旧版", "旧api", "old":
		mode = "old"
	default:
		return onebot.ReplyText(req.Event, "参数无效。用法：SKAPI切换 新 / SKAPI切换 旧", true)
	}
	if mode == current {
		return onebot.ReplyText(req.Event, "SK 榜线来源已经是："+apiLabel(current), true)
	}
	if err := m.save(mode); err != nil {
		return onebot.ReplyText(req.Event, "保存 SK API 状态失败："+err.Error(), true)
	}
	return onebot.ReplyText(req.Event, "已切换 SK 榜线来源："+apiLabel(mode)+"\n下一轮定时抓取立即生效。", true)
}

func (m *SKAPIModule) load() string {
	data, err := os.ReadFile(m.path)
	if err == nil {
		var value struct {
			Mode string `json:"mode"`
		}
		if json.Unmarshal(data, &value) == nil && (value.Mode == "new" || value.Mode == "old") {
			return value.Mode
		}
	}
	return "new"
}

func (m *SKAPIModule) save(mode string) error {
	if err := os.MkdirAll(filepath.Dir(m.path), 0o755); err != nil {
		return err
	}
	data, err := json.MarshalIndent(map[string]string{"mode": mode}, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	tmp, err := os.CreateTemp(filepath.Dir(m.path), ".sk_api_state.*.tmp")
	if err != nil {
		return err
	}
	name := tmp.Name()
	if _, err = tmp.Write(data); err != nil {
		_ = tmp.Close()
		_ = os.Remove(name)
		return err
	}
	if err = tmp.Close(); err != nil {
		_ = os.Remove(name)
		return err
	}
	if err = os.Rename(name, m.path); err != nil {
		_ = os.Remove(name)
		return err
	}
	return nil
}

func apiLabel(mode string) string {
	if mode == "old" {
		return "旧 API（180 秒，注意调用限制）"
	}
	return "新 API（30 秒）"
}
