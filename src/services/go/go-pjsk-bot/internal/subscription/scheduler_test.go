package subscription

import (
	"context"
	"sync/atomic"
	"testing"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/notifysub"
)

func TestSubscribeSchedulerStartStopAndCancel(t *testing.T) {
	var musicCalls atomic.Int32
	notify := notifysub.New(t.TempDir())
	defer notify.Close()
	if _, err := notify.Add(context.Background(), "100", "", "jp", notifysub.KindMusic); err != nil {
		t.Fatal(err)
	}
	worker := NewNotifyWorker(WorkerOptions{
		Notify: notify,
		Sender: &fakeMessenger{},
		Music: MusicSourceFunc(func(context.Context, string) ([]FeedItem, error) {
			musicCalls.Add(1)
			return nil, nil
		}),
		VLive: VLiveSourceFunc(func(context.Context, string) ([]FeedItem, error) { return nil, nil }),
	})
	s := NewSubscribeScheduler(worker, SchedulerOptions{
		MusicInterval: 5 * time.Millisecond,
		VLiveInterval: 5 * time.Millisecond,
		MSRInterval:   5 * time.Millisecond,
		SKInterval:    5 * time.Millisecond,
	})
	ctx, cancel := context.WithCancel(context.Background())
	if err := s.Start(ctx); err != nil {
		t.Fatal(err)
	}
	if err := s.Start(ctx); err == nil {
		t.Fatal("starting scheduler twice should fail")
	}
	time.Sleep(20 * time.Millisecond)
	cancel()
	s.Close()
	if musicCalls.Load() == 0 {
		t.Fatal("scheduler did not run initial music poll")
	}
	// Stop/Close 应可重复调用。
	s.Close()
}

func TestSubscribeSchedulerOnlyRunsSelectedJobs(t *testing.T) {
	var musicCalls atomic.Int32
	var vliveCalls atomic.Int32
	notify := notifysub.New(t.TempDir())
	defer notify.Close()
	if _, err := notify.Add(context.Background(), "100", "", "jp", notifysub.KindMusic); err != nil {
		t.Fatal(err)
	}
	worker := NewNotifyWorker(WorkerOptions{
		Notify: notify,
		Sender: &fakeMessenger{},
		Music: MusicSourceFunc(func(context.Context, string) ([]FeedItem, error) {
			musicCalls.Add(1)
			return nil, nil
		}),
		VLive: VLiveSourceFunc(func(context.Context, string) ([]FeedItem, error) {
			vliveCalls.Add(1)
			return nil, nil
		}),
	})
	s := NewSubscribeScheduler(worker, SchedulerOptions{
		MusicInterval: 5 * time.Millisecond,
		VLiveInterval: 5 * time.Millisecond,
		Jobs:          []string{JobMusic},
	})
	ctx, cancel := context.WithCancel(context.Background())
	if err := s.Start(ctx); err != nil {
		t.Fatal(err)
	}
	time.Sleep(20 * time.Millisecond)
	cancel()
	s.Close()
	if musicCalls.Load() == 0 {
		t.Fatal("selected music job did not run")
	}
	if vliveCalls.Load() != 0 {
		t.Fatalf("unselected vlive job ran %d times", vliveCalls.Load())
	}
}
