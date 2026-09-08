package pjsk

import (
	"path/filepath"
	"testing"
)

func TestParseGroupID(t *testing.T) {
	if _, msg := parseGroupID(""); msg == "" {
		t.Error("空参数应返回错误提示")
	}
	if _, msg := parseGroupID("abc"); msg == "" {
		t.Error("非数字应返回错误提示")
	}
	if gid, msg := parseGroupID(" 12345 "); msg != "" || gid != 12345 {
		t.Errorf("parseGroupID=%d,%q want 12345,''", gid, msg)
	}
}

func TestCnMsrLoadSaveRoundtrip(t *testing.T) {
	dir := t.TempDir()
	m := NewCnMsrModule(dir, []int64{1001})

	// 初始应为空
	set, err := m.loadGroups()
	if err != nil || len(set) != 0 {
		t.Fatalf("初始应为空: set=%v err=%v", set, err)
	}

	set[111] = true
	set[222] = true
	if err := m.saveGroups(set); err != nil {
		t.Fatalf("saveGroups: %v", err)
	}

	// 重新加载
	got, err := m.loadGroups()
	if err != nil || !got[111] || !got[222] || len(got) != 2 {
		t.Fatalf("回读不一致: got=%v err=%v", got, err)
	}

	// 文件路径正确
	if m.groupsFile() != filepath.Join(dir, "cn_msr_allowed_groups.json") {
		t.Errorf("groupsFile=%q", m.groupsFile())
	}
}

func TestCnMsrIsSuper(t *testing.T) {
	m := NewCnMsrModule(t.TempDir(), []int64{42})
	if !m.supers[42] {
		t.Error("42 应为 superuser")
	}
	if m.supers[7] {
		t.Error("7 不应为 superuser")
	}
}
