package pjsk

import (
	"context"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

// CnMsrModule 保留 CN 服 MSR 白名单管理指令入口（现已全部默认开放）。
type CnMsrModule struct {
	dataDir   string
	staticDir string
	supers    map[int64]bool
}

// NewCnMsrModule 创建白名单管理模块。supers 为超级用户 QQ 列表。
func NewCnMsrModule(dataDir, staticDir string, supers []int64) *CnMsrModule {
	set := make(map[int64]bool, len(supers))
	for _, s := range supers {
		set[s] = true
	}
	return &CnMsrModule{dataDir: dataDir, staticDir: staticDir, supers: set}
}

// assertCnMsrAllowed CN 服 MySekai 已全量默认放行，不再限制群白名单。
func assertCnMsrAllowed(dataDir, staticDir string, groupID int64, serverType int) string {
	return ""
}

// Register 注册 cnmsr启用 / cnmsr禁用 / cnmsr白名单 指令。
func (m *CnMsrModule) Register(r *router.Router) {
	r.Register("cnmsr启用", nil, m.handleNotice)
	r.Register("cnmsr禁用", nil, m.handleNotice)
	r.Register("cnmsr白名单", nil, m.handleNotice)
}

func (m *CnMsrModule) isSuper(e onebot.MessageEvent) bool {
	return m.supers[e.UserID]
}

func (m *CnMsrModule) handleNotice(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if !m.isSuper(req.Event) {
		return nil
	}
	return onebot.ReplyText(req.Event, "CN 服 MySekai 已全部默认开放，无需白名单限制。", false)
}
