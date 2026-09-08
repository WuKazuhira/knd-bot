// Package guessgame 管理猜曲游戏的进行中状态（并发安全的内存管理器），
// 对齐 old-python guess 的 pjskguess 全局字典结构。
//
// 只负责状态生命周期（开局/查询/结束/答题计数），不含出题出图与答案匹配——
// 那些由业务层用主数据/绘图服务完成。
package guessgame

import (
	"sync"
	"time"
)

// GameType 游戏大类，对齐 GUESS_CARD / GUESS_MUSIC。
type GameType string

const (
	GuessCard  GameType = "card"
	GuessMusic GameType = "music"
)

// Game 是一局进行中的猜曲游戏状态。
type Game struct {
	Diff      int           // 难度（曲绘1/阴间2/非人类3/听歌4/倒放5/谱面6/歌词7；猜卡面另计）
	MusicID   int           // 答案歌曲 id（猜曲/谱面）
	CharaID   int           // 答案角色 id（猜卡面）
	MusicName string        // 答案歌曲名
	StarterID int64         // 开局者 QQ（只有其可结束/看提示）
	PjskType  int           // 服务器
	StartAt   time.Time     // 开局时间
	guessers  map[int64]int // 每个用户已答题次数
	TipsUsed  bool          // 是否用过提示
}

// Guesses 返回某用户已答题次数。
func (g *Game) Guesses(userQQ int64) int {
	if g.guessers == nil {
		return 0
	}
	return g.guessers[userQQ]
}

// Manager 管理所有群的进行中游戏（按 类型+群 索引），并发安全。
type Manager struct {
	mu    sync.Mutex
	games map[key]*Game
}

type key struct {
	typ     GameType
	groupID int64
}

// NewManager 创建游戏管理器。
func NewManager() *Manager {
	return &Manager{games: make(map[key]*Game)}
}

// Get 返回指定群某类型的进行中游戏（无则返回 nil）。
func (m *Manager) Get(typ GameType, groupID int64) *Game {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.games[key{typ, groupID}]
}

// IsGoing 判断该群该类型是否有进行中的游戏。
func (m *Manager) IsGoing(typ GameType, groupID int64) bool {
	return m.Get(typ, groupID) != nil
}

// Start 开局；若该群该类型已有进行中的游戏，返回 (nil, false)。
func (m *Manager) Start(typ GameType, groupID int64, g *Game) (*Game, bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	k := key{typ, groupID}
	if _, exists := m.games[k]; exists {
		return nil, false
	}
	g.StartAt = time.Now()
	g.guessers = make(map[int64]int)
	m.games[k] = g
	return g, true
}

// End 结束指定群某类型的游戏，返回被结束的游戏（无则 nil）。
func (m *Manager) End(typ GameType, groupID int64) *Game {
	m.mu.Lock()
	defer m.mu.Unlock()
	k := key{typ, groupID}
	g := m.games[k]
	delete(m.games, k)
	return g
}

// RecordGuess 记录一次答题，返回该用户答题后的累计次数。
// 若超过 maxGuesses（>0）则返回 (次数, false) 表示已达上限、不应计入本次。
func (m *Manager) RecordGuess(typ GameType, groupID, userQQ int64, maxGuesses int) (int, bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	g := m.games[key{typ, groupID}]
	if g == nil {
		return 0, false
	}
	cur := g.guessers[userQQ]
	if maxGuesses > 0 && cur >= maxGuesses {
		return cur, false
	}
	g.guessers[userQQ] = cur + 1
	return cur + 1, true
}
