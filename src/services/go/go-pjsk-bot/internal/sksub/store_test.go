package sksub

import (
	"context"
	"testing"
)

func TestSubscriptionLifecycle(t *testing.T) {
	dir := t.TempDir()
	s := New(dir)
	defer s.Close()
	ctx := context.Background()

	// 初始未订阅
	if ok, _ := s.Exists(ctx, "111", "jp", 200); ok {
		t.Error("初始不应存在订阅")
	}
	// 添加
	if err := s.Add(ctx, "111", "g1", "jp", 200, "uid999"); err != nil {
		t.Fatalf("Add: %v", err)
	}
	if ok, _ := s.Exists(ctx, "111", "jp", 200); !ok {
		t.Error("添加后应存在")
	}
	// 重复添加（更新 group/uid，不报错）
	if err := s.Add(ctx, "111", "g2", "jp", 200, "uid888"); err != nil {
		t.Fatalf("重复 Add 应更新而非报错: %v", err)
	}
	// 其它服/活动互不影响
	if ok, _ := s.Exists(ctx, "111", "cn", 200); ok {
		t.Error("cn 服不应存在")
	}
	// 取消
	if removed, _ := s.Remove(ctx, "111", "jp", 200); !removed {
		t.Error("取消应成功")
	}
	if removed, _ := s.Remove(ctx, "111", "jp", 200); removed {
		t.Error("重复取消应返回 false")
	}
}

func TestClearAll(t *testing.T) {
	dir := t.TempDir()
	s := New(dir)
	defer s.Close()
	ctx := context.Background()

	s.Add(ctx, "1", "g", "jp", 200, "u1")
	s.Add(ctx, "2", "g", "jp", 200, "u2")
	s.Add(ctx, "3", "g", "cn", 201, "u3")

	n, err := s.ClearAll(ctx)
	if err != nil || n != 3 {
		t.Fatalf("ClearAll 应删除 3 条: %d err=%v", n, err)
	}
	n, _ = s.ClearAll(ctx)
	if n != 0 {
		t.Errorf("再次清空应为 0, got %d", n)
	}
}
