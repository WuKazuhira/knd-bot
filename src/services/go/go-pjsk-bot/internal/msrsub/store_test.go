package msrsub

import (
	"context"
	"testing"
)

func TestMsrSubscriptionLifecycle(t *testing.T) {
	dir := t.TempDir()
	s := New(dir)
	defer s.Close()
	ctx := context.Background()

	// 添加
	if err := s.Add(ctx, "111", "g1", "cn", "uid999", "latest"); err != nil {
		t.Fatalf("Add: %v", err)
	}
	// upsert：重复添加更新 group/uid，不报错也不重复
	if err := s.Add(ctx, "111", "g2", "cn", "uid888", ""); err != nil {
		t.Fatalf("重复 Add 应 upsert: %v", err)
	}
	// 取消
	if removed, _ := s.Remove(ctx, "111", "cn"); !removed {
		t.Error("取消应成功")
	}
	if removed, _ := s.Remove(ctx, "111", "cn"); removed {
		t.Error("重复取消应返回 false")
	}
}

func TestMsrPerServerIsolation(t *testing.T) {
	dir := t.TempDir()
	s := New(dir)
	defer s.Close()
	ctx := context.Background()

	s.Add(ctx, "1", "g", "jp", "u", "latest")
	s.Add(ctx, "1", "g", "cn", "u", "latest")
	// 同 QQ 不同服是两条独立订阅（UNIQUE(qq_id, server)）
	if removed, _ := s.Remove(ctx, "1", "jp"); !removed {
		t.Error("jp 订阅应可单独取消")
	}
	if removed, _ := s.Remove(ctx, "1", "cn"); !removed {
		t.Error("cn 订阅应仍存在")
	}
}
