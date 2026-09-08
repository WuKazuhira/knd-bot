package masterdata

import (
	"os"
	"path/filepath"
	"testing"
)

func TestConstants(t *testing.T) {
	dir := t.TempDir()
	csvDir := filepath.Join(dir, "ondemand", "jp", "realtime")
	if err := os.MkdirAll(csvDir, 0o755); err != nil {
		t.Fatal(err)
	}
	csv := "id,difficulty,constant\n74,master,33.5\n74,expert,25.0\n100,MASTER,30.2\n"
	if err := os.WriteFile(filepath.Join(csvDir, "constants.csv"), []byte(csv), 0o644); err != nil {
		t.Fatal(err)
	}

	l := New(dir)
	c := l.Constants()
	if len(c) != 3 {
		t.Fatalf("应解析 3 条定数, got %d", len(c))
	}
	if c[ConstantKey{74, "master"}] != 33.5 {
		t.Errorf("(74,master) = %v, want 33.5", c[ConstantKey{74, "master"}])
	}
	// 难度大小写归一
	if c[ConstantKey{100, "master"}] != 30.2 {
		t.Errorf("难度应小写归一: (100,master) = %v", c[ConstantKey{100, "master"}])
	}
}

func TestConstantsMissing(t *testing.T) {
	l := New(t.TempDir())
	if c := l.Constants(); len(c) != 0 {
		t.Fatal("文件缺失应返回空表")
	}
}
