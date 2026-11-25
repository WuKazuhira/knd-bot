package store

import (
	"encoding/json"
	"errors"
	"os"
	"strconv"
	"sync"
	"time"
)

type groupPolicy struct {
	Level  int   `json:"level"`
	Status *bool `json:"status"`
}

type GroupManager struct {
	path string
	logf func(string, ...any)

	mu            sync.Mutex
	groups        map[string]groupPolicy
	modified      time.Time
	size          int64
	missingLogged bool
	lastError     string
}

func NewGroupManager(path string, logf func(string, ...any)) *GroupManager {
	return &GroupManager{path: path, logf: logf}
}

func (m *GroupManager) Blocked(groupID int64) bool {
	m.mu.Lock()
	defer m.mu.Unlock()

	info, err := os.Stat(m.path)
	if errors.Is(err, os.ErrNotExist) {
		m.groups = nil
		m.lastError = ""
		if !m.missingLogged {
			m.logf("[pjskbot] 群管理文件不存在，跳过群开关检查: %s", m.path)
			m.missingLogged = true
		}
		return false
	}
	if err == nil && (m.groups == nil || !info.ModTime().Equal(m.modified) || info.Size() != m.size) {
		err = m.reload(info)
	}
	if err != nil {
		if m.lastError != err.Error() {
			m.logf("[pjskbot] 读取群管理文件失败: %v", err)
			m.lastError = err.Error()
		}
		if m.groups == nil {
			return true
		}
	} else {
		m.lastError = ""
	}
	group := m.groups[strconv.FormatInt(groupID, 10)]
	return group.Level < 0 || (group.Status != nil && !*group.Status)
}

func (m *GroupManager) reload(info os.FileInfo) error {
	raw, err := os.ReadFile(m.path)
	if err != nil {
		return err
	}
	var data struct {
		Groups map[string]groupPolicy `json:"group_manager"`
	}
	if err := json.Unmarshal(raw, &data); err != nil {
		return err
	}
	if data.Groups == nil {
		return errors.New("群管理文件缺少 group_manager 对象")
	}
	m.groups = data.Groups
	m.modified = info.ModTime()
	m.size = info.Size()
	return nil
}
