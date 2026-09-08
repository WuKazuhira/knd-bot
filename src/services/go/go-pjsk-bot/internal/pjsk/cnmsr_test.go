package pjsk

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
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

func TestWithCnCheck(t *testing.T) {
	dir := t.TempDir()
	m := &MysekaiModule{staticDir: dir}
	called := false
	inner := func(ctx context.Context, req router.Request) *onebot.ActionRequest {
		called = true
		return onebot.ReplyText(req.Event, "inner-ran", false)
	}
	wrapped := m.withCnCheck(inner)
	ctx := context.Background()

	mkReq := func(server router.ServerType, groupID int64) router.Request {
		return router.Request{
			Event:  onebot.MessageEvent{MessageType: "group", GroupID: groupID, UserID: 1},
			Server: server,
		}
	}

	// jp 服（server 0）：直接放行，inner 执行
	called = false
	resp := wrapped(ctx, mkReq(router.ServerJP, 100))
	if !called {
		t.Error("jp 服应放行执行 inner")
	}

	// cn 服（server 2）非白名单群：拒绝，inner 不执行
	called = false
	resp = wrapped(ctx, mkReq(router.ServerCN, 100))
	if called {
		t.Error("cn 服非白名单群不应执行 inner")
	}
	if resp == nil {
		t.Error("cn 服非白名单应回复拒绝提示")
	}

	// 加入白名单后放行
	if err := m2SaveGroups(dir, 100); err != nil {
		t.Fatal(err)
	}
	called = false
	wrapped(ctx, mkReq(router.ServerCN, 100))
	if !called {
		t.Error("cn 服白名单群应放行执行 inner")
	}
}

// m2SaveGroups 写 CN MSR 白名单（借用 CnMsrModule.saveGroups）。
func m2SaveGroups(dir string, groups ...int64) error {
	cm := NewCnMsrModule(dir, nil)
	set := map[int64]bool{}
	for _, g := range groups {
		set[g] = true
	}
	return cm.saveGroups(set)
}
