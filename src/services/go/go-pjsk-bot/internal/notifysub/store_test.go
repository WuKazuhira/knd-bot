package notifysub

import (
	"context"
	"testing"
	"time"
)

func TestNotifySubLifecycle(t *testing.T) {
	dir := t.TempDir()
	s := New(dir)
	defer s.Close()
	ctx := context.Background()

	// 开启群订阅
	added, err := s.Add(ctx, "100", "", "jp", KindMusic)
	if err != nil || !added {
		t.Fatalf("首次群订阅应成功: added=%v err=%v", added, err)
	}
	// 重复开启返回 false
	if added, _ := s.Add(ctx, "100", "", "jp", KindMusic); added {
		t.Error("重复群订阅应返回 false")
	}
	// 群已订阅
	if ok, _ := s.IsGroupSubbed(ctx, KindMusic, "jp", "100"); !ok {
		t.Error("IsGroupSubbed 应为 true")
	}
	// 其它服未订阅
	if ok, _ := s.IsGroupSubbed(ctx, KindMusic, "cn", "100"); ok {
		t.Error("cn 服不应被订阅")
	}

	// 个人 @ 提醒
	if added, _ := s.Add(ctx, "100", "555", "jp", KindMusic); !added {
		t.Error("个人订阅应成功")
	}
	if added, _ := s.Add(ctx, "100", "555", "jp", KindMusic); added {
		t.Error("重复个人订阅应返回 false")
	}

	// 状态：群订阅 + 个人订阅共 2 条
	status, err := s.GroupStatus(ctx, "100")
	if err != nil || len(status) != 2 {
		t.Fatalf("GroupStatus 应返回 2 条: %d err=%v", len(status), err)
	}

	// 取消个人订阅
	if removed, _ := s.Remove(ctx, "100", "555", "jp", KindMusic); !removed {
		t.Error("取消个人订阅应成功")
	}
	if removed, _ := s.Remove(ctx, "100", "555", "jp", KindMusic); removed {
		t.Error("重复取消应返回 false")
	}

	// 关闭群订阅：连带清理（此时只剩群订阅本身 1 条）
	n, err := s.RemoveGroup(ctx, "100", "jp", KindMusic)
	if err != nil || n != 1 {
		t.Fatalf("RemoveGroup 应删除 1 条: %d err=%v", n, err)
	}
	if ok, _ := s.IsGroupSubbed(ctx, KindMusic, "jp", "100"); ok {
		t.Error("关闭后不应再订阅")
	}
}

func TestRemoveGroupCleansUsers(t *testing.T) {
	dir := t.TempDir()
	s := New(dir)
	defer s.Close()
	ctx := context.Background()

	s.Add(ctx, "200", "", "cn", KindVLive)
	s.Add(ctx, "200", "1", "cn", KindVLive)
	s.Add(ctx, "200", "2", "cn", KindVLive)

	// 关闭群订阅应连带清理 2 条个人提醒，共删除 3 条
	n, err := s.RemoveGroup(ctx, "200", "cn", KindVLive)
	if err != nil || n != 3 {
		t.Fatalf("应删除 3 条(群+2个人): %d err=%v", n, err)
	}
}

func TestListAndGet(t *testing.T) {
	s := New(t.TempDir())
	defer s.Close()
	ctx := context.Background()
	if _, err := s.Add(ctx, "300", "", "jp", KindMusic); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Add(ctx, "300", "9", "jp", KindMusic); err != nil {
		t.Fatal(err)
	}
	rows, err := s.List(ctx, KindMusic, "jp")
	if err != nil || len(rows) != 2 {
		t.Fatalf("List: len=%d err=%v", len(rows), err)
	}
	if _, ok, err := s.Get(ctx, "300", "9", "jp", KindMusic); err != nil || !ok {
		t.Fatalf("Get: ok=%v err=%v", ok, err)
	}
	groups, err := s.ListGroups(ctx, KindMusic, "jp")
	if err != nil || len(groups) != 1 || groups[0].QQID != "" {
		t.Fatalf("ListGroups: %#v err=%v", groups, err)
	}
}

func TestDeliveryStateLifecycle(t *testing.T) {
	s := New(t.TempDir())
	defer s.Close()
	ctx := context.Background()
	if sent, err := s.WasSent(ctx, KindMusic, "jp", "music/1/300"); err != nil || sent {
		t.Fatalf("initial WasSent: sent=%v err=%v", sent, err)
	}
	when := time.Date(2026, 9, 9, 12, 0, 0, 0, time.UTC)
	if err := s.MarkSent(ctx, KindMusic, "jp", "music/1/300", when); err != nil {
		t.Fatal(err)
	}
	if sent, err := s.WasSent(ctx, KindMusic, "jp", "music/1/300"); err != nil || !sent {
		t.Fatalf("WasSent after MarkSent: sent=%v err=%v", sent, err)
	}
	rows, err := s.ListSent(ctx, KindMusic, "jp")
	if err != nil || len(rows) != 1 || !rows[0].LastSentAt.Equal(when) {
		t.Fatalf("ListSent: %#v err=%v", rows, err)
	}
	if removed, err := s.ClearSent(ctx, KindMusic, "jp", "music/1/300"); err != nil || !removed {
		t.Fatalf("ClearSent: removed=%v err=%v", removed, err)
	}
}
