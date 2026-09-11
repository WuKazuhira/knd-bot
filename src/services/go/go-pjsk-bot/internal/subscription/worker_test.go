package subscription

import (
	"context"
	"errors"
	"sync"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/msrsub"
	"github.com/kazuhira/go-pjsk-bot/internal/notifysub"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/sksub"
)

type fakeMessenger struct {
	mu         sync.Mutex
	groups     []sentMessage
	private    []sentMessage
	groupError error
}

type sentMessage struct {
	id  int64
	msg onebot.Message
}

func (m *fakeMessenger) SendGroupMessage(id int64, msg onebot.Message) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.groupError != nil {
		return m.groupError
	}
	m.groups = append(m.groups, sentMessage{id: id, msg: msg})
	return nil
}

func (m *fakeMessenger) SendPrivateMessage(id int64, msg onebot.Message) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.private = append(m.private, sentMessage{id: id, msg: msg})
	return nil
}

func TestNotifyWorkerMusicDeduplicatesAndMentions(t *testing.T) {
	notify := notifysub.New(t.TempDir())
	defer notify.Close()
	ctx := context.Background()
	if _, err := notify.Add(ctx, "100", "", "jp", notifysub.KindMusic); err != nil {
		t.Fatal(err)
	}
	if _, err := notify.Add(ctx, "100", "42", "jp", notifysub.KindMusic); err != nil {
		t.Fatal(err)
	}
	messenger := &fakeMessenger{}
	worker := NewNotifyWorker(WorkerOptions{
		Notify: notify,
		Sender: messenger,
		Music: MusicSourceFunc(func(_ context.Context, server string) ([]FeedItem, error) {
			if server != "jp" {
				t.Fatalf("unexpected server %q", server)
			}
			return []FeedItem{{ID: "music-1", Message: onebot.Message{onebot.Text("new song")}}}, nil
		}),
	})
	if err := worker.PollMusic(ctx); err != nil {
		t.Fatal(err)
	}
	if err := worker.PollMusic(ctx); err != nil {
		t.Fatal(err)
	}
	messenger.mu.Lock()
	defer messenger.mu.Unlock()
	if len(messenger.groups) != 1 {
		t.Fatalf("expected one deduplicated group send, got %d", len(messenger.groups))
	}
	if len(messenger.groups[0].msg) != 2 || messenger.groups[0].msg[0].Type != "at" {
		t.Fatalf("expected @ mention plus text, got %#v", messenger.groups[0].msg)
	}
}

func TestNotifyWorkerMSRUpdatesLastPushAfterSend(t *testing.T) {
	msr := msrsub.New(t.TempDir())
	defer msr.Close()
	ctx := context.Background()
	if err := msr.Add(ctx, "42", "100", "cn", "uid", "latest"); err != nil {
		t.Fatal(err)
	}
	messenger := &fakeMessenger{}
	worker := NewNotifyWorker(WorkerOptions{
		MSR:    msr,
		Sender: messenger,
		MSRFeed: MsrSourceFunc(func(_ context.Context, sub msrsub.Subscription) (FeedItem, bool, error) {
			return FeedItem{ID: "refresh-1", Message: onebot.Message{onebot.Text("updated")}}, true, nil
		}),
	})
	if err := worker.PollMSR(ctx); err != nil {
		t.Fatal(err)
	}
	if err := worker.PollMSR(ctx); err != nil {
		t.Fatal(err)
	}
	messenger.mu.Lock()
	if len(messenger.groups) != 1 {
		t.Fatalf("expected one MSR send, got %d", len(messenger.groups))
	}
	messenger.mu.Unlock()
	sub, ok, err := msr.Get(ctx, "42", "cn")
	if err != nil || !ok {
		t.Fatalf("get MSR subscription: ok=%v err=%v", ok, err)
	}
	if sub.LastPushTime.IsZero() {
		t.Fatal("last_push_time was not updated")
	}
}

func TestNotifyWorkerSKSendsAndUpdatesStatus(t *testing.T) {
	sk := sksub.New(t.TempDir())
	defer sk.Close()
	ctx := context.Background()
	if err := sk.Add(ctx, "42", "100", "jp", 10, "uid"); err != nil {
		t.Fatal(err)
	}
	messenger := &fakeMessenger{}
	worker := NewNotifyWorker(WorkerOptions{
		SK:     sk,
		Sender: messenger,
		SKFeed: SKSourceFunc(func(_ context.Context, sub sksub.Subscription) (SKUpdate, error) {
			return SKUpdate{
				FeedItem: FeedItem{ID: "score-1", Message: onebot.Message{onebot.Text("score changed")}},
				Score:    123,
				Rank:     7,
				Changed:  true,
			}, nil
		}),
	})
	if err := worker.PollSK(ctx); err != nil {
		t.Fatal(err)
	}
	messenger.mu.Lock()
	if len(messenger.groups) != 1 {
		t.Fatalf("expected one SK send, got %d", len(messenger.groups))
	}
	messenger.mu.Unlock()
	sub, ok, err := sk.Get(ctx, "42", "jp", 10)
	if err != nil || !ok {
		t.Fatalf("get SK subscription: ok=%v err=%v", ok, err)
	}
	if sub.LastScore != 123 || sub.LastRank != 7 || sub.LastCheckTime.IsZero() {
		t.Fatalf("unexpected status: %#v", sub)
	}
}

func TestUnavailableSourceDoesNotReportSuccess(t *testing.T) {
	worker := NewNotifyWorker(WorkerOptions{})
	if err := worker.PollMusic(context.Background()); err == nil {
		t.Fatal("missing source should return an error")
	}
}

func TestSourceStatusReasonClassification(t *testing.T) {
	err := errors.New("wrapped")
	status := SourceStatusError{Reason: PlayerNoRanking, Err: err}
	got, ok := SourceStatusReasonOf(status)
	if !ok || got != PlayerNoRanking {
		t.Fatalf("unexpected status: %q, %v", got, ok)
	}
	got, ok = SourceStatusReasonOf(errors.New("ordinary"))
	if ok || got != "" {
		t.Fatalf("ordinary errors must not be classified: %q, %v", got, ok)
	}
}

func TestNotifyWorkerSKActivityClosedRemovesEvent(t *testing.T) {
	sk := sksub.New(t.TempDir())
	defer sk.Close()
	ctx := context.Background()
	if err := sk.Add(ctx, "42", "100", "jp", 10, "uid"); err != nil {
		t.Fatal(err)
	}
	if err := sk.Add(ctx, "43", "100", "jp", 10, "uid2"); err != nil {
		t.Fatal(err)
	}
	if err := sk.Add(ctx, "44", "100", "jp", 11, "uid3"); err != nil {
		t.Fatal(err)
	}
	messenger := &fakeMessenger{}
	worker := NewNotifyWorker(WorkerOptions{
		SK: sk, Sender: messenger,
		SKFeed: SKSourceFunc(func(_ context.Context, sub sksub.Subscription) (SKUpdate, error) {
			if sub.EventID == 10 {
				return SKUpdate{}, SourceStatusError{Reason: ActivityClosed}
			}
			return SKUpdate{Score: sub.LastScore, Rank: sub.LastRank}, nil
		}),
	})
	if err := worker.PollSK(ctx); err != nil {
		t.Fatal(err)
	}
	items, err := sk.List(ctx, "jp", 10)
	if err != nil || len(items) != 0 {
		t.Fatalf("closed event subscriptions should be removed: %d, %v", len(items), err)
	}
	items, err = sk.List(ctx, "jp", 11)
	if err != nil || len(items) != 1 {
		t.Fatalf("other event should remain: %d, %v", len(items), err)
	}
	if len(messenger.groups) != 0 {
		t.Fatal("activity closed must not send messages")
	}
}

func TestNotifyWorkerSKNoRankingRemovesAndNotifies(t *testing.T) {
	sk := sksub.New(t.TempDir())
	defer sk.Close()
	ctx := context.Background()
	if err := sk.Add(ctx, "42", "100", "cn", 20, "uid"); err != nil {
		t.Fatal(err)
	}
	messenger := &fakeMessenger{}
	worker := NewNotifyWorker(WorkerOptions{
		SK: sk, Sender: messenger,
		SKFeed: SKSourceFunc(func(context.Context, sksub.Subscription) (SKUpdate, error) {
			return SKUpdate{}, SourceStatusError{Reason: PlayerNoRanking}
		}),
	})
	if err := worker.PollSK(ctx); err != nil {
		t.Fatal(err)
	}
	if _, ok, err := sk.Get(ctx, "42", "cn", 20); err != nil || ok {
		t.Fatalf("no-ranking subscription should be removed: ok=%v err=%v", ok, err)
	}
	if len(messenger.groups) != 1 {
		t.Fatalf("expected cancellation message, got %d", len(messenger.groups))
	}
	msg := messenger.groups[0].msg
	if len(msg) != 2 || msg[0].Type != "at" || msg[1].Type != "text" {
		t.Fatalf("unexpected cancellation message: %#v", msg)
	}
	if qq, ok := msg[0].Data["qq"].(int64); !ok || qq != 42 {
		t.Fatalf("unexpected cancellation target: %#v", msg[0].Data["qq"])
	}
	if msg.PlainText() == "" {
		t.Fatal("cancellation text is empty")
	}
}

func TestNotifyWorkerSKOrdinaryErrorKeepsSubscription(t *testing.T) {
	sk := sksub.New(t.TempDir())
	defer sk.Close()
	ctx := context.Background()
	if err := sk.Add(ctx, "42", "100", "jp", 31, "uid"); err != nil {
		t.Fatal(err)
	}
	worker := NewNotifyWorker(WorkerOptions{
		SK: sk,
		SKFeed: SKSourceFunc(func(context.Context, sksub.Subscription) (SKUpdate, error) {
			return SKUpdate{}, errors.New("temporary source error")
		}),
	})
	if err := worker.PollSK(ctx); err != nil {
		t.Fatal(err)
	}
	if _, ok, err := sk.Get(ctx, "42", "jp", 31); err != nil || !ok {
		t.Fatalf("ordinary source errors must preserve subscription: ok=%v err=%v", ok, err)
	}
}

func TestNotifyWorkerSKInvalidCancellationTargetIsSafe(t *testing.T) {
	sk := sksub.New(t.TempDir())
	defer sk.Close()
	ctx := context.Background()
	if err := sk.Add(ctx, "not-a-qq", "not-a-group", "jp", 32, "uid"); err != nil {
		t.Fatal(err)
	}
	worker := NewNotifyWorker(WorkerOptions{
		SK: sk,
		SKFeed: SKSourceFunc(func(context.Context, sksub.Subscription) (SKUpdate, error) {
			return SKUpdate{}, SourceStatusError{Reason: PlayerNoRanking}
		}),
	})
	if err := worker.PollSK(ctx); err != nil {
		t.Fatal(err)
	}
	if _, ok, err := sk.Get(ctx, "not-a-qq", "jp", 32); err != nil || ok {
		t.Fatalf("invalid target should not prevent cleanup: ok=%v err=%v", ok, err)
	}
}

func TestNotifyWorkerSKSendFailureKeepsStatusAndSeen(t *testing.T) {
	sk := sksub.New(t.TempDir())
	defer sk.Close()
	ctx := context.Background()
	if err := sk.Add(ctx, "42", "100", "jp", 30, "uid"); err != nil {
		t.Fatal(err)
	}
	initial, ok, err := sk.Get(ctx, "42", "jp", 30)
	if err != nil || !ok {
		t.Fatalf("get initial subscription: ok=%v err=%v", ok, err)
	}
	messenger := &fakeMessenger{groupError: errors.New("send failed")}
	worker := NewNotifyWorker(WorkerOptions{
		SK: sk, Sender: messenger,
		SKFeed: SKSourceFunc(func(context.Context, sksub.Subscription) (SKUpdate, error) {
			return SKUpdate{FeedItem: FeedItem{ID: "same", Message: onebot.Message{onebot.Text("changed")}}, Score: 999, Rank: 2, Changed: true}, nil
		}),
	})
	if err := worker.PollSK(ctx); err != nil {
		t.Fatal(err)
	}
	sub, ok, err := sk.Get(ctx, "42", "jp", 30)
	if err != nil || !ok {
		t.Fatalf("get subscription: ok=%v err=%v", ok, err)
	}
	if sub.LastScore != initial.LastScore || sub.LastRank != initial.LastRank || !sub.LastCheckTime.Equal(initial.LastCheckTime) {
		t.Fatalf("send failure must not update status: before=%#v after=%#v", initial, sub)
	}
	messenger.groupError = nil
	if err := worker.PollSK(ctx); err != nil {
		t.Fatal(err)
	}
	if len(messenger.groups) != 1 {
		t.Fatalf("send failure must not mark seen; expected retry, got %d", len(messenger.groups))
	}
}
