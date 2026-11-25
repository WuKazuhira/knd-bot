package pjsk

import (
	"context"
	"strconv"
	"sync"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/limiter"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

type cdRule struct {
	window   time.Duration
	count    int
	perGroup bool // true 按群，默认按用户
}

var defaultCDRule = cdRule{window: 60 * time.Second, count: 5}

var cdRules = map[string]cdRule{
	"组卡":        {60 * time.Second, 2, false},
	"guess":     {30 * time.Minute, 10, true},
	"逮捕":        {60 * time.Second, 2, false},
	"pjsk b30":  {60 * time.Second, 2, false},
	"难度排行":      {60 * time.Second, 2, false},
	"pjsk抽卡":    {60 * time.Second, 2, false},
	"pjsk进度":    {60 * time.Second, 2, false},
	"msr":       {60 * time.Second, 2, false},
	"msg":       {60 * time.Second, 2, false},
	"msm":       {60 * time.Second, 2, false},
	"烤森材料":      {60 * time.Second, 2, false},
	"msb":       {60 * time.Second, 2, false},
	"msf":       {60 * time.Second, 2, false},
	"msd":       {60 * time.Second, 2, false},
	"msp":       {60 * time.Second, 2, false},
	"卡牌一览":      {60 * time.Second, 3, false},
	"event":     {60 * time.Second, 4, false},
	"findevent": {60 * time.Second, 4, false},
	"findcard":  {60 * time.Second, 4, false},
	"sks":       {60 * time.Second, 3, false},
	"skl":       {60 * time.Second, 3, false},
	"cf":        {60 * time.Second, 3, false},
	"sk":        {60 * time.Second, 3, false},
	"csb":       {60 * time.Second, 3, false},
	"sk预测":      {60 * time.Second, 3, false},
	"ycx曲线":     {60 * time.Second, 3, false},
	"wlsk":      {60 * time.Second, 3, false},
	"wlsks":     {60 * time.Second, 3, false},
	"wlskl":     {60 * time.Second, 3, false},
	"wlcsb":     {60 * time.Second, 3, false},
}

type RateLimiter struct {
	supers map[int64]bool
	block  *limiter.BlockLimiter

	mu  sync.Mutex
	cds map[string]*limiter.CDLimiter
}

func NewRateLimiter(supers []int64) *RateLimiter {
	set := make(map[int64]bool, len(supers))
	for _, s := range supers {
		set[s] = true
	}
	return &RateLimiter{supers: set, block: limiter.NewBlock(), cds: map[string]*limiter.CDLimiter{}}
}

func (r *RateLimiter) cdFor(cmd string) (*limiter.CDLimiter, cdRule) {
	rule, ok := cdRules[cmd]
	if !ok {
		rule = defaultCDRule
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	c := r.cds[cmd]
	if c == nil {
		c = limiter.NewCD(rule.window, rule.count)
		r.cds[cmd] = c
	}
	return c, rule
}

func limitKey(cmd string, req router.Request, perGroup bool) string {
	if perGroup && req.Event.GroupID > 0 {
		return cmd + ":g" + strconv.FormatInt(req.Event.GroupID, 10)
	}
	return cmd + ":u" + strconv.FormatInt(req.Event.UserID, 10)
}

func (r *RateLimiter) Wrap(ctx context.Context, req router.Request, h router.Handler) *onebot.ActionRequest {
	if r.supers[req.Event.UserID] {
		return h(ctx, req)
	}
	cmd := req.Command
	switch cmd {
	case "活动组卡", "挑战组卡", "长草组卡", "加成组卡":
		cmd = "组卡"
	}
	cd, rule := r.cdFor(cmd)
	key := limitKey(cmd, req, rule.perGroup)

	if !cd.Allow(key) {
		return onebot.ReplyText(req.Event, "别急，等一会再用！", true)
	}
	release, ok := r.block.Acquire(key)
	if !ok {
		if cmd == "组卡" {
			return onebot.ReplyText(req.Event, "别急，还在组卡！", true)
		}
		return onebot.ReplyText(req.Event, "别急，还在查！", true)
	}
	defer release()
	return h(ctx, req)
}
