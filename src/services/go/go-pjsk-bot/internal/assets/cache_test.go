package assets

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestClientFetchCachesAndRejectsTraversal(t *testing.T) {
	root := t.TempDir()
	calls := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		_, _ = w.Write([]byte("payload"))
	}))
	defer srv.Close()

	c := New(root, srv.Client(), 1024)
	ctx := context.Background()
	first, err := c.Fetch(ctx, srv.URL, "charts/a.bin")
	if err != nil || string(first) != "payload" {
		t.Fatalf("first fetch=%q err=%v", first, err)
	}
	second, err := c.Fetch(ctx, srv.URL+"/different", "charts/a.bin")
	if err != nil || string(second) != "payload" || calls != 1 {
		t.Fatalf("cached fetch=%q err=%v calls=%d", second, err, calls)
	}
	if _, err := c.Path("../escape"); err == nil {
		t.Fatal("path traversal should be rejected")
	}
	if _, err := os.Stat(filepath.Join(root, "charts", "a.bin")); err != nil {
		t.Fatalf("cache file missing: %v", err)
	}
}

func TestClientRejectsOversizedResponse(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("too-large"))
	}))
	defer srv.Close()
	c := New(t.TempDir(), srv.Client(), 3)
	if _, err := c.Fetch(context.Background(), srv.URL, "x"); err != ErrTooLarge {
		t.Fatalf("err=%v, want ErrTooLarge", err)
	}
}
