package subscription

import (
	"context"
	"errors"
	"sync"
	"time"
)

const (
	JobMusic   = "music"
	JobVLive   = "vlive"
	JobNewCard = "new_card"
	JobMSR     = "msr"
	JobSK      = "sk"
)

// SchedulerOptions 配置五类订阅轮询间隔。零值使用默认值。
type SchedulerOptions struct {
	MusicInterval   time.Duration
	VLiveInterval   time.Duration
	NewCardInterval time.Duration
	MSRInterval     time.Duration
	SKInterval      time.Duration
	// Jobs 非空时只启动列出的任务；为空保持兼容行为，启动全部任务。
	Jobs []string
}

func (o SchedulerOptions) withDefaults() SchedulerOptions {
	if o.MusicInterval <= 0 {
		o.MusicInterval = time.Minute
	}
	if o.VLiveInterval <= 0 {
		o.VLiveInterval = time.Minute
	}
	if o.NewCardInterval <= 0 {
		o.NewCardInterval = time.Minute
	}
	if o.MSRInterval <= 0 {
		o.MSRInterval = 2 * time.Second
	}
	if o.SKInterval <= 0 {
		o.SKInterval = time.Minute
	}
	return o
}

// SubscribeScheduler 为五类订阅分别维护可取消、不可重入的轮询 goroutine。
type SubscribeScheduler struct {
	worker *NotifyWorker
	opts   SchedulerOptions
	logf   func(string, ...any)

	mu     sync.Mutex
	cancel context.CancelFunc
	wg     sync.WaitGroup
	run    bool
}

// NewSubscribeScheduler 创建订阅调度器。
func NewSubscribeScheduler(worker *NotifyWorker, opts SchedulerOptions) *SubscribeScheduler {
	return &SubscribeScheduler{worker: worker, opts: opts.withDefaults()}
}

// SetLogger 设置调度错误日志。应在 Start 前调用。
func (s *SubscribeScheduler) SetLogger(logf func(string, ...any)) {
	s.mu.Lock()
	if logf != nil {
		s.logf = logf
	}
	s.mu.Unlock()
}

// Start 启动四个轮询任务。每个任务启动时先执行一次，再按 interval 重复。
func (s *SubscribeScheduler) Start(parent context.Context) error {
	if s.worker == nil {
		return errors.New("subscription scheduler worker is nil")
	}
	if parent == nil {
		parent = context.Background()
	}
	allJobs := []struct {
		name     string
		interval time.Duration
		poll     func(context.Context) error
	}{
		{JobMusic, s.opts.MusicInterval, s.worker.PollMusic},
		{JobVLive, s.opts.VLiveInterval, s.worker.PollVLive},
		{JobNewCard, s.opts.NewCardInterval, s.worker.PollNewCard},
		{JobMSR, s.opts.MSRInterval, s.worker.PollMSR},
		{JobSK, s.opts.SKInterval, s.worker.PollSK},
	}
	jobs := allJobs
	if len(s.opts.Jobs) > 0 {
		allowed := make(map[string]struct{}, len(s.opts.Jobs))
		for _, name := range s.opts.Jobs {
			allowed[name] = struct{}{}
		}
		jobs = make([]struct {
			name     string
			interval time.Duration
			poll     func(context.Context) error
		}, 0, len(allJobs))
		for _, job := range allJobs {
			if _, ok := allowed[job.name]; ok {
				jobs = append(jobs, job)
			}
		}
	}
	s.mu.Lock()
	if s.run {
		s.mu.Unlock()
		return errors.New("subscription scheduler already started")
	}
	ctx, cancel := context.WithCancel(parent)
	s.cancel = cancel
	s.run = true
	// Add 必须和 Stop 的 Wait 串行化，避免 Start/Stop 并发时遗漏 goroutine。
	for range jobs {
		s.wg.Add(1)
	}
	s.mu.Unlock()
	for _, job := range jobs {
		go s.runJob(ctx, job.name, job.interval, job.poll)
	}
	return nil
}

func (s *SubscribeScheduler) runJob(ctx context.Context, name string, interval time.Duration, poll func(context.Context) error) {
	defer s.wg.Done()
	run := func() {
		if err := poll(ctx); err != nil && !errors.Is(err, context.Canceled) && !errors.Is(err, context.DeadlineExceeded) && !errors.Is(err, ErrSourceUnavailable) {
			s.mu.Lock()
			logf := s.logf
			s.mu.Unlock()
			if logf != nil {
				logf("[subscription] %s 轮询失败: %v", name, err)
			}
		}
	}
	run()
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			run()
		}
	}
}

// Stop 取消并等待全部轮询任务退出；可重复调用。
func (s *SubscribeScheduler) Stop() {
	s.mu.Lock()
	cancel := s.cancel
	if cancel != nil {
		cancel()
	}
	s.cancel = nil
	s.run = false
	s.mu.Unlock()
	s.wg.Wait()
}

// Close 是 Stop 的别名，便于 main 使用 defer scheduler.Close()。
func (s *SubscribeScheduler) Close() {
	s.Stop()
}
