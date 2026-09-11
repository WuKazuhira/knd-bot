package scheduler

import (
	"context"
	"sync/atomic"
	"testing"
	"time"
)

func TestEveryAndCancel(t *testing.T) {
	s := New()
	defer s.Close()
	var count atomic.Int32
	if err := s.Every("test", 5*time.Millisecond, func(context.Context) { count.Add(1) }); err != nil {
		t.Fatal(err)
	}
	time.Sleep(18 * time.Millisecond)
	s.Cancel("test")
	before := count.Load()
	time.Sleep(12 * time.Millisecond)
	if count.Load() != before {
		t.Fatalf("job continued after cancel: before=%d after=%d", before, count.Load())
	}
}

func TestClosedSchedulerRejectsJobs(t *testing.T) {
	s := New()
	s.Close()
	if err := s.Every("closed", time.Second, func(context.Context) {}); err != ErrClosed {
		t.Fatalf("err=%v, want ErrClosed", err)
	}
}
