package pjsk

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
)

func TestQueryRefresherRefreshesFilesAndDrawCache(t *testing.T) {
	var mu sync.Mutex
	var paths []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		paths = append(paths, r.Method+" "+r.URL.Path+"?"+r.URL.RawQuery)
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"changed":false}`))
	}))
	defer srv.Close()

	refresher := NewQueryRefresher(masterdata.New(t.TempDir()), draw.New(srv.URL), srv.URL)
	if err := refresher.Refresh(context.Background(), 0, QueryRefreshCards); err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(paths) != len(queryRefreshFiles[QueryRefreshCards])+1 {
		t.Fatalf("requests=%v", paths)
	}
	for _, path := range paths[:len(paths)-1] {
		if !strings.Contains(path, "/masterdata/refresh") || !strings.Contains(path, "region=jp") {
			t.Errorf("unexpected refresh request %q", path)
		}
	}
	if !strings.HasPrefix(paths[len(paths)-1], "POST /cache/clear") {
		t.Errorf("last request should clear draw cache: %q", paths[len(paths)-1])
	}
}

func TestQueryRefresherFailsClosedWhenDrawCacheClearFails(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/cache/clear" {
			w.WriteHeader(http.StatusBadGateway)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	refresher := NewQueryRefresher(masterdata.New(t.TempDir()), draw.New(srv.URL), srv.URL)
	if err := refresher.Refresh(context.Background(), 0, QueryRefreshEvents); err == nil || !strings.Contains(err.Error(), "刷新缓存失败") {
		t.Fatalf("expected draw cache error, got %v", err)
	}
}
