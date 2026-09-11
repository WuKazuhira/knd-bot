package pjsk

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
)

// RemoteState 是 remote/live 控制面的持久化状态。
//
// live_on 只表示 Go 侧后台循环开关；进程重启后不会假装恢复一个不存在的
// goroutine，RemoteModule 会在初始化时把它校正为 false。
type RemoteState struct {
	RemoteOn bool `json:"remote_on"`
	LiveOn   bool `json:"live_on"`
}

type remoteStateStore struct {
	path string
	mu   sync.Mutex
}

func newRemoteStateStore(dataDir string) *remoteStateStore {
	return &remoteStateStore{path: filepath.Join(dataDir, "ondemand", "remote", "state.json")}
}

func (s *remoteStateStore) Path() string { return s.path }

func (s *remoteStateStore) Load() RemoteState {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.loadLocked()
}

func (s *remoteStateStore) loadLocked() RemoteState {
	data, err := os.ReadFile(s.path)
	if err != nil {
		return RemoteState{}
	}
	var state RemoteState
	if err := json.Unmarshal(data, &state); err != nil {
		return RemoteState{}
	}
	return state
}

func (s *remoteStateStore) Update(remoteOn, liveOn *bool) RemoteState {
	s.mu.Lock()
	defer s.mu.Unlock()
	state := s.loadLocked()
	if remoteOn != nil {
		state.RemoteOn = *remoteOn
	}
	if liveOn != nil {
		state.LiveOn = *liveOn
	}
	_ = s.saveLocked(state)
	return state
}

func (s *remoteStateStore) Save(state RemoteState) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.saveLocked(state)
}

func (s *remoteStateStore) saveLocked(state RemoteState) error {
	if err := os.MkdirAll(filepath.Dir(s.path), 0o755); err != nil {
		return err
	}
	data, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	tmp, err := os.CreateTemp(filepath.Dir(s.path), ".state-*.json")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if err := tmp.Chmod(0o600); err != nil {
		_ = tmp.Close()
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, s.path)
}
