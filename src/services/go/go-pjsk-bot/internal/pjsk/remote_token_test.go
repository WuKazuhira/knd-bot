package pjsk

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
)

func TestFormatTokenStatusWarnsBeforeExpiry(t *testing.T) {
	text := formatTokenStatus(map[string]any{
		"server":        "cn",
		"userId":        "123",
		"expiresAtText": "2026-10-01 12:00:00",
		"remainingDays": 7,
	})
	for _, part := range []string{"服务器：CN", "userId：123", "剩余：7 天", "即将过期"} {
		if !strings.Contains(text, part) {
			t.Errorf("状态文本缺少 %q: %s", part, text)
		}
	}
}

func TestRemoteTokenQueryAndMultipartUpload(t *testing.T) {
	var uploaded bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Haruki-Sekai-Token") != "api-token" {
			t.Errorf("缺少 sekai-api token header")
		}
		switch r.URL.Path {
		case "/token/cn/status":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"server": "cn", "userId": "123", "expiresAtText": "2026-10-01 12:00:00", "remainingDays": 45,
			})
		case "/token/cn/upload":
			if err := r.ParseMultipartForm(1 << 20); err != nil {
				t.Errorf("解析 multipart: %v", err)
				w.WriteHeader(http.StatusBadRequest)
				return
			}
			file, _, err := r.FormFile("file")
			if err != nil {
				t.Errorf("缺少 file: %v", err)
				w.WriteHeader(http.StatusBadRequest)
				return
			}
			defer file.Close()
			body, _ := io.ReadAll(file)
			uploaded = string(body) == "capture"
			_ = json.NewEncoder(w).Encode(map[string]any{
				"server": "cn", "userId": "123", "expiresAtText": "2026-10-01 12:00:00", "remainingDays": 45,
				"source": "suite", "reloaded": true,
			})
		default:
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()

	m := NewRemoteModule(RemoteConfig{APIURL: srv.URL, APIToken: "api-token", Region: "cn", DataDir: t.TempDir()})
	defer m.Close()
	data, err := m.token.query(context.Background(), "cn")
	if err != nil || data["userId"] != "123" {
		t.Fatalf("token query data=%v err=%v", data, err)
	}
	data, err = m.token.upload(context.Background(), "cn", []byte("capture"), "capture.bin")
	if err != nil || !uploaded || data["source"] != "suite" {
		t.Fatalf("token upload data=%v uploaded=%v err=%v", data, uploaded, err)
	}
}

func TestRemoteTokenNoticeFlow(t *testing.T) {
	var gotUpload bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/file" {
			_, _ = w.Write([]byte("capture"))
			return
		}
		if r.URL.Path == "/token/cn/upload" {
			if err := r.ParseMultipartForm(1 << 20); err == nil {
				file, _, fileErr := r.FormFile("file")
				if fileErr == nil {
					defer file.Close()
					content, _ := io.ReadAll(file)
					gotUpload = string(content) == "capture"
				}
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"server": "cn", "userId": "123", "expiresAtText": "2026-10-01 12:00:00", "remainingDays": 45,
				"source": "suite", "reloaded": true,
			})
			return
		}
		http.NotFound(w, r)
	}))
	defer srv.Close()

	m := NewRemoteModule(RemoteConfig{APIURL: srv.URL, APIToken: "api-token", Region: "cn", DataDir: t.TempDir(), Superusers: []int64{100}})
	defer m.Close()
	m.token.pending.Set(tokenPendingKey(100), tokenPending{region: "cn"}, remoteUploadTTL)
	action := m.HandleNotice(onebotNotice(100, srv.URL+"/file"))
	if action == nil || !gotUpload {
		t.Fatalf("notice action=%v uploaded=%v", action, gotUpload)
	}
	body, err := action.Marshal()
	if err != nil || !strings.Contains(string(body), "accessToken 已更新") {
		t.Fatalf("notice response=%s err=%v", body, err)
	}
}

func onebotNotice(userID int64, fileURL string) onebot.NoticeEvent {
	return onebot.NoticeEvent{
		SelfID:     1,
		NoticeType: "offline_file",
		UserID:     userID,
		File:       onebot.FileInfo{Name: "capture.bin", URL: fileURL},
	}
}
