package masterdata

import (
	"os"
	"path/filepath"
	"testing"
)

func writeMD(t *testing.T, dataDir, server, filename, content string) {
	t.Helper()
	dir := filepath.Join(dataDir, "ondemand", server)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, filename), []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestLoadPlainList(t *testing.T) {
	dir := t.TempDir()
	writeMD(t, dir, "jp", "cards.json", `[{"id":1,"name":"a"},{"id":2,"name":"b"}]`)
	l := New(dir)
	list, err := l.Load("cards.json", 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(list) != 2 || list[0]["name"] != "a" {
		t.Fatalf("解析列表错误: %+v", list)
	}
}

func TestLoadWrapperKey(t *testing.T) {
	dir := t.TempDir()
	writeMD(t, dir, "cn", "events.json", `{"events":[{"id":10},{"id":20}]}`)
	l := New(dir)
	list, err := l.Load("events.json", 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(list) != 2 {
		t.Fatalf("wrapper key 解包错误: %+v", list)
	}
}

func TestLoadNumericMap(t *testing.T) {
	dir := t.TempDir()
	writeMD(t, dir, "jp", "m.json", `{"2":{"id":2},"1":{"id":1}}`)
	l := New(dir)
	list, err := l.Load("m.json", 0)
	if err != nil {
		t.Fatal(err)
	}
	// 数字键按排序取 values：1,2
	if len(list) != 2 {
		t.Fatalf("numeric map 解包错误: %+v", list)
	}
	if id, _ := IntField(list[0], "id"); id != 1 {
		t.Fatalf("numeric map 应按键排序, got first id=%v", list[0]["id"])
	}
}

func TestByIDAndCache(t *testing.T) {
	dir := t.TempDir()
	writeMD(t, dir, "jp", "cards.json", `[{"id":5,"name":"x"},{"id":7,"name":"y"}]`)
	l := New(dir)
	list, err := l.Load("cards.json", 0)
	if err != nil {
		t.Fatal(err)
	}
	idx := ByID(list)
	if idx[7]["name"] != "y" {
		t.Fatalf("ByID 索引错误: %+v", idx)
	}
	// 第二次 Load 命中缓存（返回同一底层切片）
	list2, _ := l.Load("cards.json", 0)
	if len(list2) != 2 {
		t.Fatal("缓存读取错误")
	}
}

func TestLoadMissing(t *testing.T) {
	l := New(t.TempDir())
	if _, err := l.Load("nope.json", 0); err == nil {
		t.Fatal("缺失文件应返回错误")
	}
}
