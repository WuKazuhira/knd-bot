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

func TestAssertCnMsrAllowed(t *testing.T) {
	dir := t.TempDir()
	// 非 cn 服（jp=0, tw=1）直接放行
	if msg := assertCnMsrAllowed(dir, 0, 0); msg != "" {
		t.Errorf("jp 服应放行, got %q", msg)
	}
	if msg := assertCnMsrAllowed(dir, 0, 1); msg != "" {
		t.Errorf("tw 服应放行, got %q", msg)
	}
	// cn 服无群号 → 拒绝
	if msg := assertCnMsrAllowed(dir, 0, 2); msg == "" {
		t.Error("cn 服无群号应拒绝")
	}
	// cn 服群号不在白名单 → 拒绝
	if msg := assertCnMsrAllowed(dir, 12345, 2); msg == "" {
		t.Error("cn 服未入白名单应拒绝")
	}
	// 写入白名单后放行
	m := NewCnMsrModule(dir, nil)
	if err := m.saveGroups(map[int64]bool{12345: true}); err != nil {
		t.Fatal(err)
	}
	if msg := assertCnMsrAllowed(dir, 12345, 2); msg != "" {
		t.Errorf("cn 服已入白名单应放行, got %q", msg)
	}
}
