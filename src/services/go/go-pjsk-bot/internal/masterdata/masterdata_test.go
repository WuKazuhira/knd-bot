package masterdata

import (
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
)

func TestDerivedCacheAndInvalidation(t *testing.T) {
	dir := t.TempDir()
	jpDir := filepath.Join(dir, "ondemand", "jp")
	if err := os.MkdirAll(jpDir, 0o755); err != nil {
		t.Fatal(err)
	}
	eventsPath := filepath.Join(jpDir, "events.json")
	if err := os.WriteFile(eventsPath, []byte(`[{"id":1}]`), 0o644); err != nil {
		t.Fatal(err)
	}

	loader := New(dir)
	var builds atomic.Int32
	buildFn := func() (any, error) {
		n := builds.Add(1)
		data, err := loader.Load("events.json", 0)
		if err != nil {
			return nil, err
		}
		return map[string]any{"count": len(data), "build": int(n)}, nil
	}

	v1, err := loader.Derived("idx", 0, []string{"events.json"}, buildFn)
	if err != nil {
		t.Fatal(err)
	}
	v2, err := loader.Derived("idx", 0, []string{"events.json"}, buildFn)
	if err != nil {
		t.Fatal(err)
	}
	if builds.Load() != 1 || v1.(map[string]any)["build"] != v2.(map[string]any)["build"] {
		t.Fatalf("expected cached derived value, builds=%d", builds.Load())
	}

	// 依赖文件更新后应自动重建
	if err := os.WriteFile(eventsPath, []byte(`[{"id":1},{"id":2}]`), 0o644); err != nil {
		t.Fatal(err)
	}
	v3, err := loader.Derived("idx", 0, []string{"events.json"}, buildFn)
	if err != nil {
		t.Fatal(err)
	}
	if builds.Load() != 2 || v3.(map[string]any)["count"] != 2 {
		t.Fatalf("expected rebuild on file change, builds=%d count=%v", builds.Load(), v3.(map[string]any)["count"])
	}

	// Invalidate 应清理派生缓存
	loader.Invalidate(0, "events.json")
	if _, err := loader.Derived("idx", 0, []string{"events.json"}, buildFn); err != nil {
		t.Fatal(err)
	}
	if builds.Load() != 3 {
		t.Fatalf("expected rebuild after Invalidate, builds=%d", builds.Load())
	}
}

func TestConstantsCache(t *testing.T) {
	dir := t.TempDir()
	rtDir := filepath.Join(dir, "ondemand", "jp", "realtime")
	if err := os.MkdirAll(rtDir, 0o755); err != nil {
		t.Fatal(err)
	}
	csvPath := filepath.Join(rtDir, "constants.csv")
	if err := os.WriteFile(csvPath, []byte("id,difficulty,constant\n1,master,31.2\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	loader := New(dir)
	c1 := loader.Constants()
	if c1[ConstantKey{MusicID: 1, Diff: "master"}] != 31.2 {
		t.Fatalf("unexpected constant: %v", c1)
	}
	c2 := loader.Constants()
	if len(c2) != 1 {
		t.Fatalf("unexpected cached constants len: %d", len(c2))
	}

	if err := os.WriteFile(csvPath, []byte("id,difficulty,constant\n1,master,31.5\n2,expert,28.4\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	c3 := loader.Constants()
	if c3[ConstantKey{MusicID: 1, Diff: "master"}] != 31.5 || c3[ConstantKey{MusicID: 2, Diff: "expert"}] != 28.4 {
		t.Fatalf("expected updated constants, got %v", c3)
	}
}
