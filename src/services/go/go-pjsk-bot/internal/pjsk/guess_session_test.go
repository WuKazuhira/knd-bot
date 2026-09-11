package pjsk

import (
	"sync"
	"testing"
	"time"
)

func newTestGuessSession(groupID, initiatorID, answerID int64) *guessSession {
	return &guessSession{
		groupID:     groupID,
		initiatorID: initiatorID,
		answerID:    int(answerID),
		answerName:  "测试答案",
		mode:        guessModeMusic,
	}
}

func TestGuessSessionMutualExclusionAndAttempts(t *testing.T) {
	m := newGuessSessionManager(time.Hour, nil, nil)
	defer m.close()

	if !m.start(newTestGuessSession(100, 1, 42)) {
		t.Fatal("第一次启动应成功")
	}
	if m.start(newTestGuessSession(100, 2, 43)) {
		t.Fatal("同群第二轮不应启动")
	}

	for i, wantRemain := range []int{2, 1, 0} {
		result := m.guess(100, 9, int64(30+i))
		if result.outcome != guessWrong || result.remain != wantRemain {
			t.Fatalf("第%d次错误猜测=%+v", i+1, result)
		}
	}
	if result := m.guess(100, 9, 42); result.outcome != guessAttemptsExhausted {
		t.Fatalf("超过三次后应忽略，got=%v", result.outcome)
	}

	if !m.start(newTestGuessSession(101, 1, 42)) {
		t.Fatal("另一群应可启动")
	}
	if result := m.guess(101, 9, 42); result.outcome != guessCorrect {
		t.Fatalf("正确答案应结算，got=%v", result.outcome)
	}
	if _, ok := m.current(101); ok {
		t.Fatal("正确结算后会话应删除")
	}
}

func TestGuessSessionConcurrentSettlement(t *testing.T) {
	m := newGuessSessionManager(time.Hour, nil, nil)
	defer m.close()
	if !m.start(newTestGuessSession(200, 1, 99)) {
		t.Fatal("启动失败")
	}

	const workers = 32
	results := make(chan guessOutcome, workers)
	var wg sync.WaitGroup
	for i := 0; i < workers; i++ {
		userID := int64(100 + i)
		wg.Add(1)
		go func() {
			defer wg.Done()
			results <- m.guess(200, userID, 99).outcome
		}()
	}
	wg.Wait()
	close(results)

	correct := 0
	inactive := 0
	for outcome := range results {
		switch outcome {
		case guessCorrect:
			correct++
		case guessInactive:
			inactive++
		default:
			t.Fatalf("并发正确答案出现意外结果=%v", outcome)
		}
	}
	if correct != 1 || inactive != workers-1 {
		t.Fatalf("并发结算应只有一个赢家，correct=%d inactive=%d", correct, inactive)
	}
}

func TestGuessSessionTipPermissionAndConsumption(t *testing.T) {
	m := newGuessSessionManager(time.Hour, nil, nil)
	defer m.close()
	s := newTestGuessSession(250, 7, 1)
	s.tips = []string{"第一条", "第二条"}
	if !m.start(s) {
		t.Fatal("启动失败")
	}
	if got, _, _, ok := m.takeTip(250, 8); ok || got == nil {
		t.Fatalf("非发起者不应取提示，session=%+v ok=%v", got, ok)
	}
	got, text, _, ok := m.takeTip(250, 7)
	if !ok || text != "第一条" || got.tipsUsed != 1 || got.rankEligible {
		t.Fatalf("第一次提示结果=%+v text=%q ok=%v", got, text, ok)
	}
	_, text, _, ok = m.takeTip(250, 7)
	if !ok || text != "第二条" {
		t.Fatalf("第二次提示结果 text=%q ok=%v", text, ok)
	}
	_, _, _, ok = m.takeTip(250, 7)
	if ok {
		t.Fatal("提示耗尽后不应成功")
	}
}

func TestGuessSessionInitiatorAndTTL(t *testing.T) {
	expired := make(chan *guessSession, 1)
	m := newGuessSessionManager(15*time.Millisecond, nil, func(s *guessSession) {
		expired <- s
	})
	defer m.close()
	if !m.start(newTestGuessSession(300, 7, 1)) {
		t.Fatal("启动失败")
	}
	if result := m.end(300, 8); result.outcome != guessNotInitiator {
		t.Fatalf("非发起者不应结束，got=%v", result.outcome)
	}

	select {
	case s := <-expired:
		if s.groupID != 300 || s.answerID != 1 {
			t.Fatalf("TTL 结算会话错误=%+v", s)
		}
	case <-time.After(500 * time.Millisecond):
		t.Fatal("TTL 到期未触发结算")
	}
	if _, ok := m.current(300); ok {
		t.Fatal("TTL 后会话应删除")
	}
}
