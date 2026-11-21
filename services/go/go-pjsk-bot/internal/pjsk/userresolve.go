package pjsk

import (
	"context"
	"strconv"

	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/store"
)

// UserResolver 把指令参数/at/绑定解析成目标 uid，对齐 old-python get_userid_preprocess。
// 供 rop/arrest/b30 等需要"查自己或他人档案"的模块共享。
type UserResolver struct {
	store *store.Store
}

// NewUserResolver 创建解析器。
func NewUserResolver(s *store.Store) *UserResolver {
	return &UserResolver{store: s}
}

// Resolve 返回 (userid, isPrivate, 错误文案)。错误文案非空时应直接回复用户。
func (u *UserResolver) Resolve(ctx context.Context, req router.Request) (string, bool, string) {
	arg := digitsOnly(req.Arg)
	if arg != "" {
		if !VerifyID(arg, int(req.Server)) {
			return "", false, errID
		}
		return arg, false, ""
	}
	if u.store == nil {
		return "", false, errNotBind
	}
	// 无参数：优先 at 目标，否则发送者
	qid := req.Event.UserID
	ats := req.Event.Message.AtTargets()
	if len(ats) > 0 && ats[0] != req.Event.SelfID {
		qid = ats[0]
	}
	uid, isPrivate, exists, err := u.store.GetUserBind(ctx, qid, int(req.Server))
	if err != nil || !exists {
		who := "你"
		if qid != req.Event.UserID {
			who = "用户"
		}
		return "", false, who + "还没有绑定" + req.Server.Name() + "哦，国服/台服指令请加cn/tw前缀，日服无需前缀"
	}
	if isPrivate && qid != req.Event.UserID {
		return "", false, errRefused
	}
	return strconv.FormatInt(uid, 10), isPrivate, ""
}
