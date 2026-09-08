package limiter

import (
	"testing"
	"time"
)

func TestCDLimiter(t *testing.T) {
	c := NewCD(10*time.Second, 2)
	base := time.Now()
	c.now = func() time.Time { return base }

	if !c.Allow("u1") {
		t.Fatal("第 1 次应放行")
	}
	if !c.Allow("u1") {
		t.Fatal("第 2 次应放行")
	}
	if c.Allow("u1") {
		t.Fatal("第 3 次应拒绝（超过 count=2）")
	}
	// 不同 key 互不影响
	if !c.Allow("u2") {
		t.Fatal("不同 key 应独立放行")
	}
	// 时间推进到窗口外，恢复
	c.now = func() time.Time { return base.Add(11 * time.Second) }
	if !c.Allow("u1") {
		t.Fatal("窗口过后应重新放行")
	}
}

func TestBlockLimiter(t *testing.T) {
	b := NewBlock()
	release, ok := b.Acquire("k1")
	if !ok {
		t.Fatal("首次占用应成功")
	}
	if _, ok2 := b.Acquire("k1"); ok2 {
		t.Fatal("重入应被拒绝")
	}
	// 不同 key 可占用
	if _, ok3 := b.Acquire("k2"); !ok3 {
		t.Fatal("不同 key 应可占用")
	}
	release()
	if _, ok4 := b.Acquire("k1"); !ok4 {
		t.Fatal("释放后应可重新占用")
	}
}
