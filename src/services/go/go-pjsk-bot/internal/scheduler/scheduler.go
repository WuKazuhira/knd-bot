// Package scheduler 提供可取消、可关闭的轻量定时任务调度器。
package scheduler

import (
	"context"
	"sync"
	"time"
)

type Job func(context.Context)

type Scheduler struct {
	mu     sync.Mutex
	jobs   map[string]context.CancelFunc
	closed bool
}

func New() *Scheduler { return &Scheduler{jobs: make(map[string]context.CancelFunc)} }

// Every 启动一个按 interval 重复执行的任务。相同 id 会先取消旧任务。
func (s *Scheduler) Every(id string, interval time.Duration, job Job) error {
	if interval <= 0 || job == nil {
		return ErrInvalidJob
	}
	ctx, cancel := context.WithCancel(context.Background())
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		cancel()
		return ErrClosed
	}
	if old, ok := s.jobs[id]; ok {
		old()
	}
	s.jobs[id] = cancel
	s.mu.Unlock()
	go func() {
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				job(ctx)
			case <-ctx.Done():
				return
			}
		}
	}()
	return nil
}

func (s *Scheduler) Cancel(id string) {
	s.mu.Lock()
	if cancel, ok := s.jobs[id]; ok {
		cancel()
		delete(s.jobs, id)
	}
	s.mu.Unlock()
}

func (s *Scheduler) Close() {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return
	}
	s.closed = true
	for id, cancel := range s.jobs {
		cancel()
		delete(s.jobs, id)
	}
	s.mu.Unlock()
}

var ErrInvalidJob = err("invalid scheduler job")
var ErrClosed = err("scheduler is closed")

type err string

func (e err) Error() string { return string(e) }
