package subscription

import (
	"context"
	"errors"
	"sync"
	"time"
)

const (
	JobMusic       = "music"
	JobVLive       = "vlive"
	JobNewCard     = "new_card"
	JobCostume     = "new_costume"
	JobCardCostume = "card_costume"
	JobMSR         = "msr"
	JobSK          = "sk"
)

type SchedulerOptions struct {
	MusicInterval   time.Duration
	VLiveInterval   time.Duration
	NewCardInterval time.Duration
	MSRInterval     time.Duration
	SKInterval      time.Duration
	Jobs            []string
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

type SubscribeScheduler struct {
	worker *NotifyWorker
	opts   SchedulerOptions
	logf   func(string, ...any)

	mu     sync.Mutex
	cancel context.CancelFunc
	wg     sync.WaitGroup
	run    bool
}

func NewSubscribeScheduler(worker *NotifyWorker, opts SchedulerOptions) *SubscribeScheduler {
	return &SubscribeScheduler{worker: worker, opts: opts.withDefaults()}
}

func (s *SubscribeScheduler) SetLogger(logf func(string, ...any)) {
	s.mu.Lock()
	if logf != nil {
		s.logf = logf
	}
	s.mu.Unlock()
}

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
		{JobCostume, s.opts.NewCardInterval, s.worker.PollCostume},
		{JobCardCostume, s.opts.NewCardInterval, s.worker.PollCardCostume},
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
	// wg.Add 放在锁里，避免和 Stop 的 wg.Wait 竞态
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

func (s *SubscribeScheduler) Close() {
	s.Stop()
}
