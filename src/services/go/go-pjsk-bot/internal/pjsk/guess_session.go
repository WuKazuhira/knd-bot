package pjsk

import (
	"sync"
	"time"
)

const (
	guessMaxAttempts = 3
	guessTTL         = 90 * time.Second
)

type guessOutcome uint8

const (
	guessInactive guessOutcome = iota
	guessWrong
	guessCorrect
	guessAttemptsExhausted
	guessEnded
	guessNotInitiator
	guessExpired
)

type guessSession struct {
	groupID       int64
	selfID        int64
	server        int
	mode          guessMode
	difficulty    int
	initiatorID   int64
	answerID      int
	answerName    string
	asset         string
	questionImage []byte
	answerImage   []byte
	questionText  string
	mediaFile     string
	answerFile    string
	tips          []string
	tipImages     [][]byte
	tipsUsed      int
	rankEligible  bool
	reward        int
	rewardCapped  bool
	startedAt     time.Time
	expiresAt     time.Time
	attempts      map[int64]int
	generation    uint64
	timer         *time.Timer
}

type guessResult struct {
	outcome  guessOutcome
	session  *guessSession
	attempts int
	remain   int
}

// guessSessionManager 保存每个群的唯一活动会话，并将结算操作串行化。
// 定时器只负责触发 expire；真正的删除与结算仍通过同一把锁完成。
type guessSessionManager struct {
	mu       sync.Mutex
	sessions map[int64]*guessSession
	ttl      time.Duration
	now      func() time.Time
	onExpire func(*guessSession)
}

func newGuessSessionManager(ttl time.Duration, now func() time.Time, onExpire func(*guessSession)) *guessSessionManager {
	if ttl <= 0 {
		ttl = guessTTL
	}
	if now == nil {
		now = time.Now
	}
	return &guessSessionManager{
		sessions: make(map[int64]*guessSession),
		ttl:      ttl,
		now:      now,
		onExpire: onExpire,
	}
}

func (m *guessSessionManager) start(s *guessSession) bool {
	if s == nil || s.groupID == 0 {
		return false
	}

	var expired *guessSession
	m.mu.Lock()
	if current, ok := m.sessions[s.groupID]; ok {
		if !current.expiresAt.IsZero() && !m.now().Before(current.expiresAt) {
			delete(m.sessions, s.groupID)
			stopGuessTimer(current)
			expired = cloneGuessSession(current)
		} else {
			m.mu.Unlock()
			return false
		}
	}
	now := m.now()
	s.startedAt = now
	s.expiresAt = now.Add(m.ttl)
	s.attempts = make(map[int64]int)
	s.rankEligible = true
	s.generation++
	generation := s.generation
	m.sessions[s.groupID] = s
	s.timer = time.AfterFunc(m.ttl, func() {
		m.expire(s.groupID, generation)
	})
	m.mu.Unlock()

	if expired != nil {
		m.notifyExpire(expired)
	}
	return true
}

func (m *guessSessionManager) current(groupID int64) (*guessSession, bool) {
	var expired *guessSession
	m.mu.Lock()
	s, ok := m.sessions[groupID]
	if ok && !s.expiresAt.IsZero() && !m.now().Before(s.expiresAt) {
		delete(m.sessions, groupID)
		stopGuessTimer(s)
		expired = cloneGuessSession(s)
		s = nil
		ok = false
	}
	if ok {
		s = cloneGuessSession(s)
	}
	m.mu.Unlock()
	if expired != nil {
		m.notifyExpire(expired)
	}
	return s, ok
}

func (m *guessSessionManager) takeTip(groupID, userID int64) (*guessSession, string, []byte, bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	s, ok := m.sessions[groupID]
	if !ok || !m.now().Before(s.expiresAt) {
		return nil, "", nil, false
	}
	if s.initiatorID != userID {
		return cloneGuessSession(s), "", nil, false
	}
	if len(s.tips) == 0 && len(s.tipImages) == 0 {
		return cloneGuessSession(s), "", nil, false
	}
	var text string
	if len(s.tips) > 0 {
		text = s.tips[0]
		s.tips = s.tips[1:]
	}
	var image []byte
	if len(s.tipImages) > 0 {
		image = append([]byte(nil), s.tipImages[0]...)
		s.tipImages = s.tipImages[1:]
	}
	s.tipsUsed++
	s.rankEligible = false
	return cloneGuessSession(s), text, image, true
}

func (m *guessSessionManager) guess(groupID, userID, answerID int64) guessResult {
	m.mu.Lock()
	s, ok := m.sessions[groupID]
	if !ok {
		m.mu.Unlock()
		return guessResult{outcome: guessInactive}
	}
	if !s.expiresAt.IsZero() && !m.now().Before(s.expiresAt) {
		delete(m.sessions, groupID)
		stopGuessTimer(s)
		expired := cloneGuessSession(s)
		m.mu.Unlock()
		return guessResult{outcome: guessExpired, session: expired}
	}

	attempts := s.attempts[userID]
	if attempts >= guessMaxAttempts {
		m.mu.Unlock()
		return guessResult{outcome: guessAttemptsExhausted, attempts: attempts, remain: 0}
	}
	attempts++
	s.attempts[userID] = attempts
	if int(answerID) == s.answerID {
		delete(m.sessions, groupID)
		stopGuessTimer(s)
		result := guessResult{
			outcome:  guessCorrect,
			session:  cloneGuessSession(s),
			attempts: attempts,
			remain:   guessMaxAttempts - attempts,
		}
		m.mu.Unlock()
		return result
	}
	result := guessResult{
		outcome:  guessWrong,
		attempts: attempts,
		remain:   guessMaxAttempts - attempts,
	}
	m.mu.Unlock()
	return result
}

func (m *guessSessionManager) end(groupID, userID int64) guessResult {
	var expired *guessSession
	m.mu.Lock()
	s, ok := m.sessions[groupID]
	if !ok {
		m.mu.Unlock()
		return guessResult{outcome: guessInactive}
	}
	if !s.expiresAt.IsZero() && !m.now().Before(s.expiresAt) {
		delete(m.sessions, groupID)
		stopGuessTimer(s)
		expired = cloneGuessSession(s)
		m.mu.Unlock()
		return guessResult{outcome: guessExpired, session: expired}
	}
	if s.initiatorID != userID {
		m.mu.Unlock()
		return guessResult{outcome: guessNotInitiator, session: cloneGuessSession(s)}
	}
	delete(m.sessions, groupID)
	stopGuessTimer(s)
	result := guessResult{outcome: guessEnded, session: cloneGuessSession(s)}
	m.mu.Unlock()
	return result
}

func (m *guessSessionManager) expire(groupID int64, generation uint64) {
	var expired *guessSession
	m.mu.Lock()
	s, ok := m.sessions[groupID]
	if !ok || s.generation != generation {
		m.mu.Unlock()
		return
	}
	delete(m.sessions, groupID)
	expired = cloneGuessSession(s)
	m.mu.Unlock()
	m.notifyExpire(expired)
}

func (m *guessSessionManager) close() {
	m.mu.Lock()
	for groupID, s := range m.sessions {
		stopGuessTimer(s)
		delete(m.sessions, groupID)
	}
	m.mu.Unlock()
}

func (m *guessSessionManager) notifyExpire(s *guessSession) {
	if s != nil && m.onExpire != nil {
		m.onExpire(s)
	}
}

func stopGuessTimer(s *guessSession) {
	if s != nil && s.timer != nil {
		s.timer.Stop()
		s.timer = nil
	}
}

func cloneGuessSession(s *guessSession) *guessSession {
	if s == nil {
		return nil
	}
	clone := *s
	clone.timer = nil
	clone.questionImage = append([]byte(nil), s.questionImage...)
	clone.answerImage = append([]byte(nil), s.answerImage...)
	clone.tips = append([]string(nil), s.tips...)
	clone.tipImages = make([][]byte, len(s.tipImages))
	for i, image := range s.tipImages {
		clone.tipImages[i] = append([]byte(nil), image...)
	}
	clone.attempts = make(map[int64]int, len(s.attempts))
	for userID, attempts := range s.attempts {
		clone.attempts[userID] = attempts
	}
	return &clone
}
