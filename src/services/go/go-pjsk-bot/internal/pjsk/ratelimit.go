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

// cdRule 描述一条指令的冷却配置，对齐 old-python __plugin_cd_limit__。
type cdRule struct {
	window   time.Duration
	count    int
	perGroup bool // true=按群，默认按用户
}

// defaultCDRule 是未单独配置指令的默认冷却：60 秒内最多 5 次（宽松，仅防刷）。
var defaultCDRule = cdRule{window: 60 * time.Second, count: 5}

// cdRules 按规范命令名给出冷却配置，count 对齐各 Python 模块的
// __plugin_cd_limit__.count_limit。未列出的命令用 defaultCDRule。
//
// 语义差异说明：Python 的 CD 是 per-plugin（同插件所有命令共享一个计数窗口），
// 这里是 per-command（每个命令名独立计数）。count 值一致，但 Go 侧对多命令模块
// （mysekai/sk）更宽松——按命令各自限流，防刷目的达到且体验更好。这是有意的合理近似。
var cdRules = map[string]cdRule{
	// arrest / b30 / deck / diffrank / gacha / rop / mysekai：count_limit=2
	"逮捕":       {60 * time.Second, 2, false},
	"pjsk b30": {60 * time.Second, 2, false},
	"挑战组卡":     {60 * time.Second, 2, false},
	"难度排行":     {60 * time.Second, 2, false},
	"pjsk抽卡":   {60 * time.Second, 2, false},
	"pjsk进度":   {60 * time.Second, 2, false},
	// mysekai 模块（count_limit=2）
	"msr":  {60 * time.Second, 2, false},
	"msg":  {60 * time.Second, 2, false},
	"msm":  {60 * time.Second, 2, false},
	"烤森材料": {60 * time.Second, 2, false},
	"msb":  {60 * time.Second, 2, false},
	"msf":  {60 * time.Second, 2, false},
	"msd":  {60 * time.Second, 2, false},
	"msp":  {60 * time.Second, 2, false},
	// cardbox（count_limit=3）
	"卡牌一览": {60 * time.Second, 3, false},
	// event / findcard（count_limit=4）
	"event":     {60 * time.Second, 4, false},
	"findevent": {60 * time.Second, 4, false},
	"findcard":  {60 * time.Second, 4, false},
	// sk 模块（count_limit=3）
	"sks":   {60 * time.Second, 3, false},
	"skl":   {60 * time.Second, 3, false},
	"cf":    {60 * time.Second, 3, false},
	"sk":    {60 * time.Second, 3, false},
	"csb":   {60 * time.Second, 3, false},
	"sk预测":  {60 * time.Second, 3, false},
	"ycx曲线": {60 * time.Second, 3, false},
	"wlsk":  {60 * time.Second, 3, false},
	"wlsks": {60 * time.Second, 3, false},
	"wlskl": {60 * time.Second, 3, false},
	"wlcsb": {60 * time.Second, 3, false},
}

// RateLimiter 在指令分发前做冷却（CD）与防重入（Block）限流，对齐 Python
// 框架层的 __plugin_cd_limit__ / __plugin_block_limit__。superuser 豁免。
type RateLimiter struct {
	supers map[int64]bool
	block  *limiter.BlockLimiter

	mu  sync.Mutex
	cds map[string]*limiter.CDLimiter // 命令名 -> CD 限流器
}

// NewRateLimiter 创建限流器。
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

// limitKey 生成限流键：按用户或按群 + 命令名。
func limitKey(cmd string, req router.Request, perGroup bool) string {
	if perGroup && req.Event.GroupID > 0 {
		return cmd + ":g" + strconv.FormatInt(req.Event.GroupID, 10)
	}
	return cmd + ":u" + strconv.FormatInt(req.Event.UserID, 10)
}

// Wrap 包装分发：CD/Block 检查通过才执行 h；超限返回提示，不执行 h。
// superuser 豁免所有限流。
func (r *RateLimiter) Wrap(ctx context.Context, req router.Request, h router.Handler) *onebot.ActionRequest {
	// superuser 豁免
	if r.supers[req.Event.UserID] {
		return h(ctx, req)
	}
	cmd := req.Command
	cd, rule := r.cdFor(cmd)
	key := limitKey(cmd, req, rule.perGroup)

	// 冷却检查
	if !cd.Allow(key) {
		return onebot.ReplyText(req.Event, "别急，稍后再用～", true)
	}
	// 防重入：同一 key 的指令未结束前不并发执行
	release, ok := r.block.Acquire(key)
	if !ok {
		return onebot.ReplyText(req.Event, "别急，还在处理上一条～", true)
	}
	defer release()
	return h(ctx, req)
}
