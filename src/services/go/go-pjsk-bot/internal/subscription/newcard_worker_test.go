package subscription

import (
	"context"
	"errors"
	"sync"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/notifysub"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
)

type forwardCall struct {
	groupID int64
	nodes   []onebot.ForwardNode
}

type forwardMessenger struct {
	fakeMessenger
	mu           sync.Mutex
	forwards     []forwardCall
	forwardError error
}

func (m *forwardMessenger) SendGroupForwardMessageContext(_ context.Context, groupID int64, nodes []onebot.ForwardNode) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.forwardError != nil {
		return m.forwardError
	}
	m.forwards = append(m.forwards, forwardCall{groupID: groupID, nodes: nodes})
	return nil
}

func (m *forwardMessenger) SendPrivateForwardMessageContext(_ context.Context, _ int64, _ []onebot.ForwardNode) error {
	return nil
}

func TestNotifyWorkerNewCardBaselineAndDedup(t *testing.T) {
	notify := notifysub.New(t.TempDir())
	defer notify.Close()
	ctx := context.Background()
	if _, err := notify.Add(ctx, "100", "", "jp", notifysub.KindNewCard); err != nil {
		t.Fatal(err)
	}
	messenger := &forwardMessenger{}
	items := []FeedItem(nil)
	worker := NewNotifyWorker(WorkerOptions{
		Notify: notify,
		Sender: messenger,
		NewCard: NewCardSourceFunc(func(context.Context, string) ([]FeedItem, error) {
			return items, nil
		}),
	})
	// 空结果也必须落基线，避免下一轮首个新卡被误判为历史。
	if err := worker.PollNewCard(ctx); err != nil {
		t.Fatal(err)
	}
	id := "event/1/cards/10"
	items = []FeedItem{{ID: id, ForwardNodes: []onebot.ForwardNode{
		onebot.NewForwardNode("test", 1, onebot.Message{onebot.Text("card")}),
	}}}
	if err := worker.PollNewCard(ctx); err != nil {
		t.Fatal(err)
	}
	messenger.mu.Lock()
	if len(messenger.forwards) != 1 {
		t.Fatalf("first post-baseline item should send: %#v", messenger.forwards)
	}
	messenger.mu.Unlock()
	if err := worker.PollNewCard(ctx); err != nil {
		t.Fatal(err)
	}
	messenger.mu.Lock()
	if len(messenger.forwards) != 1 {
		t.Fatalf("same item should remain deduplicated: %#v", messenger.forwards)
	}
	messenger.mu.Unlock()
	id = "event/2/cards/20"
	if err := worker.PollNewCard(ctx); err != nil {
		t.Fatal(err)
	}
	messenger.mu.Lock()
	defer messenger.mu.Unlock()
	if len(messenger.forwards) != 1 || messenger.forwards[0].groupID != 100 {
		t.Fatalf("new card should send one forward: %#v", messenger.forwards)
	}
	if len(messenger.forwards[0].nodes) != 1 {
		t.Fatalf("unexpected nodes: %#v", messenger.forwards[0].nodes)
	}
}

func TestNotifyWorkerNewCardSendFailureRetries(t *testing.T) {
	notify := notifysub.New(t.TempDir())
	defer notify.Close()
	ctx := context.Background()
	if _, err := notify.Add(ctx, "100", "", "jp", notifysub.KindNewCard); err != nil {
		t.Fatal(err)
	}
	messenger := &forwardMessenger{}
	worker := NewNotifyWorker(WorkerOptions{
		Notify: notify,
		Sender: messenger,
		NewCard: NewCardSourceFunc(func(context.Context, string) ([]FeedItem, error) {
			return []FeedItem{{ID: "event/1/cards/10", ForwardNodes: []onebot.ForwardNode{
				onebot.NewForwardNode("test", 1, onebot.Message{onebot.Text("card")}),
			}}}, nil
		}),
	})
	// 建立基线。
	if err := worker.PollNewCard(ctx); err != nil {
		t.Fatal(err)
	}
	messenger.forwardError = errors.New("send failed")
	// 使用新的 ID 触发真正发送。
	worker.newCard = NewCardSourceFunc(func(context.Context, string) ([]FeedItem, error) {
		return []FeedItem{{ID: "event/2/cards/20", ForwardNodes: []onebot.ForwardNode{
			onebot.NewForwardNode("test", 1, onebot.Message{onebot.Text("card")}),
		}}}, nil
	})
	if err := worker.PollNewCard(ctx); err != nil {
		t.Fatal(err)
	}
	messenger.forwardError = nil
	if err := worker.PollNewCard(ctx); err != nil {
		t.Fatal(err)
	}
	messenger.mu.Lock()
	defer messenger.mu.Unlock()
	if len(messenger.forwards) != 1 {
		t.Fatalf("failed forward must retry once: %#v", messenger.forwards)
	}
}
