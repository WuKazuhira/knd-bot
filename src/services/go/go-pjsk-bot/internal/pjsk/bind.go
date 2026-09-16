package pjsk

import (
	"context"
	"strconv"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/store"
)

// 错误文案，对齐 old-python _config.py。
const (
	errNotBind = "出错了，可能是因为没有绑定"
	errID      = "你这ID有问题啊"
	errBug     = "出错了，可能是バグ捏"
	errRefused = "查不到捏，可能是不给看"
)

// BindModule 实现绑定/解绑/给看/查时间指令，依赖 store 做持久化。
type BindModule struct {
	store    *store.Store
	resolver *UserResolver
}

// NewBindModule 创建 bind 业务模块。
func NewBindModule(s *store.Store) *BindModule {
	return &BindModule{store: s, resolver: NewUserResolver(s)}
}

// Register 把 bind 相关指令注册到路由器。
func (m *BindModule) Register(r *router.Router) {
	r.RegisterNumericSuffix("bind", []string{"绑定"}, m.handleBind)
	r.Register("unbind", []string{"解绑"}, m.handleUnbind)
	r.Register("给看", []string{"不给看"}, m.handleLook)
	r.RegisterNumericSuffix("查时间", nil, m.handleCtime)
}

// digitsOnly 保留字符串中的数字字符，对齐 Python re.sub(r'\D', "").
func digitsOnly(s string) string {
	var b strings.Builder
	for _, r := range s {
		if r >= '0' && r <= '9' {
			b.WriteRune(r)
		}
	}
	return b.String()
}

func (m *BindModule) handleBind(ctx context.Context, req router.Request) *onebot.ActionRequest {
	arg := digitsOnly(req.Arg)
	if arg == "" {
		return onebot.ReplyText(req.Event, "绑定成...？你id呢？", true)
	}
	if !VerifyID(arg, int(req.Server)) {
		return onebot.ReplyText(req.Event, errID, true)
	}
	uid, _ := strconv.ParseInt(arg, 10, 64)
	if err := m.store.AddBind(ctx, req.Event.UserID, uid, int(req.Server)); err != nil {
		return onebot.ReplyText(req.Event, "绑定"+req.Server.Name()+"服务器失败，请稍后重试", true)
	}
	return onebot.ReplyText(req.Event, "绑定"+req.Server.Name()+"服务器成功", true)
}

func (m *BindModule) handleUnbind(ctx context.Context, req router.Request) *onebot.ActionRequest {
	ok, err := m.store.DelBind(ctx, req.Event.UserID, int(req.Server))
	if err != nil || !ok {
		return onebot.ReplyText(req.Event, "解绑成...？你还没绑定过呢", true)
	}
	return onebot.ReplyText(req.Event, "解绑成功", true)
}

func (m *BindModule) handleLook(ctx context.Context, req router.Request) *onebot.ActionRequest {
	isPrivate := strings.Contains(req.RawCmd, "不给看")
	exists, err := m.store.CheckExists(ctx, req.Event.UserID, int(req.Server))
	if err != nil || !exists {
		return onebot.ReplyText(req.Event, errNotBind, false)
	}
	ok, err := m.store.SetLook(ctx, req.Event.UserID, isPrivate, int(req.Server))
	if err != nil || !ok {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	if isPrivate {
		return onebot.ReplyText(req.Event, "不给看！", false)
	}
	return onebot.ReplyText(req.Event, "给看！", false)
}

func (m *BindModule) handleCtime(ctx context.Context, req router.Request) *onebot.ActionRequest {
	userid, isPrivate, errMsg := m.resolver.Resolve(ctx, req)
	if errMsg != "" {
		return onebot.ReplyText(req.Event, errMsg, true)
	}
	if isPrivate {
		return onebot.ReplyText(req.Event, errRefused, false)
	}
	rt := RegisterTime(userid, int(req.Server))
	if rt == 0 {
		return onebot.ReplyText(req.Event, "计算时间失败，可能是ID无效", false)
	}
	t := time.Unix(rt, 0)
	return onebot.ReplyText(req.Event, t.Format("注册时间：2006-01-02 15:04:05"), false)
}
