// Package session 提供带过期时间的临时用户/群会话状态。
package session

import (
	"sync"
	"time"
)

type entry struct {
	value   any
	expires time.Time
}

// Manager 是线程安全的临时状态表。状态不会自动续期，调用方可显式更新。
type Manager struct {
	mu      sync.Mutex
	items   map[string]entry
	stop    chan struct{}
	stopped chan struct{}
}

func New(cleanInterval time.Duration) *Manager {
	if cleanInterval <= 0 {
		cleanInterval = time.Minute
	}
	m := &Manager{items: make(map[string]entry), stop: make(chan struct{}), stopped: make(chan struct{})}
	go m.cleanLoop(cleanInterval)
	return m
}

func (m *Manager) Set(key string, value any, ttl time.Duration) {
	m.mu.Lock()
	m.items[key] = entry{value: value, expires: time.Now().Add(ttl)}
	m.mu.Unlock()
}

func (m *Manager) Get(key string) (any, bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	item, ok := m.items[key]
	if !ok {
		return nil, false
	}
	if !item.expires.IsZero() && time.Now().After(item.expires) {
		delete(m.items, key)
		return nil, false
	}
	return item.value, true
}

func (m *Manager) Delete(key string) {
	m.mu.Lock()
	delete(m.items, key)
	m.mu.Unlock()
}

func (m *Manager) Len() int {
	m.mu.Lock()
	defer m.mu.Unlock()
	return len(m.items)
}

func (m *Manager) Close() {
	select {
	case <-m.stop:
		return
	default:
		close(m.stop)
	}
	<-m.stopped
}

func (m *Manager) cleanLoop(interval time.Duration) {
	defer close(m.stopped)
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			m.cleanExpired()
		case <-m.stop:
			return
		}
	}
}

func (m *Manager) cleanExpired() {
	now := time.Now()
	m.mu.Lock()
	for key, item := range m.items {
		if !item.expires.IsZero() && now.After(item.expires) {
			delete(m.items, key)
		}
	}
	m.mu.Unlock()
}
