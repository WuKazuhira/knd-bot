package session

import (
	"testing"
	"time"
)

func TestManagerExpiresAndDeletes(t *testing.T) {
	m := New(5 * time.Millisecond)
	defer m.Close()
	m.Set("user:1", "state", 20*time.Millisecond)
	if value, ok := m.Get("user:1"); !ok || value != "state" {
		t.Fatalf("initial state=%v/%v", value, ok)
	}
	m.Delete("user:1")
	if _, ok := m.Get("user:1"); ok {
		t.Fatal("deleted state should not be returned")
	}
	m.Set("user:2", "state", 1*time.Millisecond)
	time.Sleep(10 * time.Millisecond)
	if _, ok := m.Get("user:2"); ok {
		t.Fatal("expired state should not be returned")
	}
}
