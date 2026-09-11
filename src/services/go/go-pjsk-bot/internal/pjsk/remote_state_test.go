package pjsk

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestRemoteStateStorePersistsAtomically(t *testing.T) {
	dir := t.TempDir()
	store := newRemoteStateStore(dir)
	if got := store.Load(); got != (RemoteState{}) {
		t.Fatalf("初始状态=%+v", got)
	}

	remoteOn, liveOn := true, true
	store.Update(&remoteOn, &liveOn)
	data, err := os.ReadFile(filepath.Join(dir, "ondemand", "remote", "state.json"))
	if err != nil {
		t.Fatalf("读取状态文件: %v", err)
	}
	var got RemoteState
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatalf("解析状态文件: %v", err)
	}
	if got != (RemoteState{RemoteOn: true, LiveOn: true}) {
		t.Fatalf("落盘状态=%+v", got)
	}

	liveOn = false
	store.Update(nil, &liveOn)
	if got := store.Load(); got != (RemoteState{RemoteOn: true, LiveOn: false}) {
		t.Fatalf("部分更新状态=%+v", got)
	}
}

func TestRemoteStateStoreCorruptFileFallsBack(t *testing.T) {
	dir := t.TempDir()
	store := newRemoteStateStore(dir)
	if err := os.MkdirAll(filepath.Dir(store.Path()), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(store.Path(), []byte("not-json"), 0o600); err != nil {
		t.Fatal(err)
	}
	if got := store.Load(); got != (RemoteState{}) {
		t.Fatalf("损坏文件应回退默认状态，got=%+v", got)
	}
}
