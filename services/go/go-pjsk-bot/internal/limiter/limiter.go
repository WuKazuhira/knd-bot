package limiter

import (
	"sync"
	"time"
)

type LimitType int

const (
	PerUser LimitType = iota
	PerGroup
)

type CDLimiter struct {
	window time.Duration
	count  int

	mu   sync.Mutex
	hits map[string][]time.Time
	now  func() time.Time
}

func NewCD(window time.Duration, count int) *CDLimiter {
	return &CDLimiter{
		window: window,
		count:  count,
		hits:   make(map[string][]time.Time),
		now:    time.Now,
	}
}

func (c *CDLimiter) Allow(key string) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	now := c.now()
	cutoff := now.Add(-c.window)
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

type BlockLimiter struct {
	mu       sync.Mutex
	inflight map[string]struct{}
}

func NewBlock() *BlockLimiter {
	return &BlockLimiter{inflight: make(map[string]struct{})}
}

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
