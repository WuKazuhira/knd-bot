package masterdata

import (
	"os"
	"path/filepath"
	"testing"
)

func TestWriteIfChangedCreatesWorldReadableMasterdata(t *testing.T) {
	root := t.TempDir()
	syncer := &Syncer{outDir: root}
	changed, err := syncer.writeIfChanged("jp", "costume3ds.json", []byte(`{"ok":true}`))
	if err != nil {
		t.Fatal(err)
	}
	if !changed {
		t.Fatal("first write should report changed")
	}
	path := filepath.Join(root, "jp", "costume3ds.json")
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if mode := info.Mode().Perm(); mode != 0o644 {
		t.Fatalf("mode=%#o want 0644", mode)
	}
}
