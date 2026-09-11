package pjsk

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDeduplicatePreviewAndApply(t *testing.T) {
	root := filepath.Join(t.TempDir(), "ondemand")
	source := filepath.Join(root, "jp", "startapp", "music", "music_score", "100", "master.txt")
	target := filepath.Join(root, "cn", "startapp", "music", "music_score", "100", "master.txt")
	if err := os.MkdirAll(filepath.Dir(source), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		t.Fatal(err)
	}
	content := []byte("same chart")
	if err := os.WriteFile(source, content, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(target, content, 0o644); err != nil {
		t.Fatal(err)
	}

	stats, err := deduplicate(root, []string{"cn"}, false)
	if err != nil {
		t.Fatal(err)
	}
	if stats["cn"].Duplicates != 1 || stats["cn"].Linked != 0 {
		t.Fatalf("preview stats=%+v", stats["cn"])
	}
	stats, err = deduplicate(root, []string{"cn"}, true)
	if err != nil {
		t.Fatal(err)
	}
	if stats["cn"].Linked != 1 {
		t.Fatalf("apply stats=%+v", stats["cn"])
	}
	sourceInfo, err := os.Stat(source)
	if err != nil {
		t.Fatal(err)
	}
	targetInfo, err := os.Stat(target)
	if err != nil {
		t.Fatal(err)
	}
	if !os.SameFile(sourceInfo, targetInfo) {
		t.Fatal("target should be hardlink to JP source")
	}
}
