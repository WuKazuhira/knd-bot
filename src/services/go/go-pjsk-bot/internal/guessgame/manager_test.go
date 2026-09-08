package guessgame

import (
	"sync"
	"testing"
)

func TestStartAndGet(t *testing.T) {
	m := NewManager()
	// 开局成功
	g, ok := m.Start(GuessMusic, 100, &Game{MusicID: 74, StarterID: 5})
	if !ok || g == nil {
		t.Fatal("首次开局应成功")
	}
	if m.Get(GuessMusic, 100).MusicID != 74 {
		t.Error("Get 应返回开局的游戏")
	}
	if !m.IsGoing(GuessMusic, 100) {
		t.Error("IsGoing 应为 true")
	}
	// 重复开局失败
	if _, ok := m.Start(GuessMusic, 100, &Game{MusicID: 99}); ok {
		t.Error("已有进行中游戏时重复开局应失败")
	}
	// 不同群/类型互不影响
	if _, ok := m.Start(GuessMusic, 200, &Game{}); !ok {
		t.Error("不同群应可开局")
	}
	if _, ok := m.Start(GuessCard, 100, &Game{}); !ok {
		t.Error("不同类型应可开局")
	}
}

func TestEnd(t *testing.T) {
	m := NewManager()
	m.Start(GuessMusic, 100, &Game{MusicID: 74})
	ended := m.End(GuessMusic, 100)
	if ended == nil || ended.MusicID != 74 {
		t.Error("End 应返回被结束的游戏")
	}
	if m.IsGoing(GuessMusic, 100) {
		t.Error("结束后不应再进行中")
	}
	if m.End(GuessMusic, 100) != nil {
		t.Error("重复结束应返回 nil")
	}
}

func TestRecordGuess(t *testing.T) {
	m := NewManager()
	m.Start(GuessMusic, 100, &Game{})
	// 上限 2 次
	if n, ok := m.RecordGuess(GuessMusic, 100, 5, 2); n != 1 || !ok {
		t.Errorf("第1次应计入, got n=%d ok=%v", n, ok)
	}
	if n, ok := m.RecordGuess(GuessMusic, 100, 5, 2); n != 2 || !ok {
		t.Errorf("第2次应计入, got n=%d ok=%v", n, ok)
	}
	if _, ok := m.RecordGuess(GuessMusic, 100, 5, 2); ok {
		t.Error("第3次应超限")
	}
	// 查询次数
	if g := m.Get(GuessMusic, 100); g.Guesses(5) != 2 {
		t.Errorf("用户5应答题2次, got %d", g.Guesses(5))
	}
	// 无游戏时
	if _, ok := m.RecordGuess(GuessMusic, 999, 1, 2); ok {
		t.Error("无游戏群不应计入")
	}
}

func TestConcurrentAccess(t *testing.T) {
	m := NewManager()
	m.Start(GuessMusic, 100, &Game{})
	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func(uid int64) {
			defer wg.Done()
			m.RecordGuess(GuessMusic, 100, uid, 0)
			m.IsGoing(GuessMusic, 100)
		}(int64(i))
	}
	wg.Wait() // 主要验证无数据竞争（-race 下）
}
