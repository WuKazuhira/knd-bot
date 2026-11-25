package pjsk

import (
	"sync"

	"github.com/kazuhira/go-pjsk-bot/internal/session"
)

// 锁内只操作会话状态，不做网络和 OneBot 调用
var uploadSessionsMu sync.Mutex

// 调用方需持有 uploadSessionsMu
func cancelUploadSession(manager *session.Manager, key string) bool {
	if manager == nil {
		return false
	}
	_, exists := manager.Get(key)
	manager.Delete(key)
	return exists
}

func takeUploadSession(manager *session.Manager, key string) (any, bool) {
	uploadSessionsMu.Lock()
	defer uploadSessionsMu.Unlock()
	value, exists := manager.Get(key)
	if exists {
		manager.Delete(key)
	}
	return value, exists
}

func (m *UploadModule) CancelPendingUpload(userID int64) bool {
	if m == nil {
		return false
	}
	uploadSessionsMu.Lock()
	defer uploadSessionsMu.Unlock()
	return cancelUploadSession(m.state, uploadKey(userID))
}
