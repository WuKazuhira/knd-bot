package pjsk

import (
	"sync"

	"github.com/kazuhira/go-pjsk-bot/internal/session"
)

// 开始、取消与消费会话共享短锁；锁内不执行下载、上传或 OneBot 调用。
var uploadSessionsMu sync.Mutex

// LinkUploadSessions 必须在接收事件前调用，开启 Suite/token 相互取消。
// 主程序未接线时仍保留各模块独立会话和原有权限约束。
func LinkUploadSessions(suite *UploadModule, remote *RemoteModule) {
	if suite == nil || remote == nil || remote.token == nil {
		return
	}
	uploadSessionsMu.Lock()
	defer uploadSessionsMu.Unlock()
	suite.tokenPeer = remote.token
	remote.token.suitePeer = suite
}

// cancelUploadSession 仅供已持有 uploadSessionsMu 的调用方使用。
func cancelUploadSession(manager *session.Manager, key string) bool {
	if manager == nil {
		return false
	}
	_, exists := manager.Get(key)
	manager.Delete(key)
	return exists
}

// takeUploadSession 使 file/offline_file（包括并发重复事件）只消费一次。
func takeUploadSession(manager *session.Manager, key string) (any, bool) {
	uploadSessionsMu.Lock()
	defer uploadSessionsMu.Unlock()
	value, exists := manager.Get(key)
	if exists {
		manager.Delete(key)
	}
	return value, exists
}

// CancelPendingUpload 取消尚未消费的 Suite 会话，不取消已经开始的上传。
func (m *UploadModule) CancelPendingUpload(userID int64) bool {
	if m == nil {
		return false
	}
	uploadSessionsMu.Lock()
	defer uploadSessionsMu.Unlock()
	return cancelUploadSession(m.state, uploadKey(userID))
}

// CancelPendingTokenUpload 取消尚未消费的 token 会话，不取消已经开始的上传。
func (m *RemoteModule) CancelPendingTokenUpload(userID int64) bool {
	if m == nil || m.token == nil {
		return false
	}
	uploadSessionsMu.Lock()
	defer uploadSessionsMu.Unlock()
	return cancelUploadSession(m.token.pending, tokenPendingKey(userID))
}
