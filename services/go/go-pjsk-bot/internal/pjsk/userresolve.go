package pjsk

import (
	"context"
	"strconv"

	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/store"
)

type UserResolver struct {
	store *store.Store
}

func NewUserResolver(s *store.Store) *UserResolver {
	return &UserResolver{store: s}
}

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
