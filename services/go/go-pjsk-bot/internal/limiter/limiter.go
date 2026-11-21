// Package limiter 提供指令级的冷却（CD）与防重入（Block）限流，
// 对齐 old-python 框架层的 __plugin_cd_limit__ 与 __plugin_block_limit__。
package limiter

import (
	"sync"
	"time"
)

// LimitType 限流粒度。
type LimitType int

const (
	PerUser  LimitType = iota // 按用户
	PerGroup                  // 按群
)

// CDLimiter 实现滑动窗口冷却：window 内最多 count 次。
type CDLimiter struct {
	window time.Duration
	count  int

	mu   sync.Mutex
	hits map[string][]time.Time
	now  func() time.Time // 便于测试注入时间
}

// NewCD 创建冷却限流器：window 时间窗内最多 count 次。
func NewCD(window time.Duration, count int) *CDLimiter {
	return &CDLimiter{
		window: window,
		count:  count,
		hits:   make(map[string][]time.Time),
		now:    time.Now,
	}
}

// Allow 记录一次请求；未超限返回 true，超限返回 false 且不计入。
func (c *CDLimiter) Allow(key string) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	now := c.now()
	cutoff := now.Add(-c.window)
	// 清理窗口外的旧记录
	times := c.hits[key]
	kept := times[:0]
	for _, t := range times {
		if t.After(cutoff) {
			kept = append(kept, t)
		}
	}
	if len(kept) >= c.count {
		c.hits[key] = kept
		return false
	}
	c.hits[key] = append(kept, now)
	return true
}

// BlockLimiter 防止同一 key 的指令并发重入。
type BlockLimiter struct {
	mu       sync.Mutex
	inflight map[string]struct{}
}

// NewBlock 创建防重入限流器。
func NewBlock() *BlockLimiter {
	return &BlockLimiter{inflight: make(map[string]struct{})}
}

// Acquire 尝试占用 key；成功返回 release 函数与 true，已被占用返回 (nil, false)。
func (b *BlockLimiter) Acquire(key string) (func(), bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if _, busy := b.inflight[key]; busy {
		return nil, false
	}
	b.inflight[key] = struct{}{}
	return func() {
		b.mu.Lock()
		delete(b.inflight, key)
		b.mu.Unlock()
	}, true
}
