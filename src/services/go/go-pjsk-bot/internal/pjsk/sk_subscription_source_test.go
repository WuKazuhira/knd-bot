package pjsk

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/skstore"
	"github.com/kazuhira/go-pjsk-bot/internal/sksub"
	"github.com/kazuhira/go-pjsk-bot/internal/subscription"
	_ "modernc.org/sqlite"
)

func TestSKSubscriptionEventActive(t *testing.T) {
	now := time.Date(2026, 1, 2, 12, 0, 0, 0, time.UTC).UnixMilli()
	events := []map[string]any{
		{"id": float64(10), "startAt": float64(now - 3600000), "aggregateAt": float64(now + 3600000)},
		{"id": float64(11), "startAt": float64(now + 3600000), "aggregateAt": float64(now + 7200000)},
	}
	if !skSubscriptionEventActive(events, 10, now) {
		t.Fatal("current event should be active")
	}
	if skSubscriptionEventActive(events, 11, now) {
		t.Fatal("upcoming event must not be current")
	}
	if skSubscriptionEventActive(events, 10, now+7200000) {
		t.Fatal("ended event must be closed")
	}
}

func TestSKSubscriptionSourceChangedAndUnchanged(t *testing.T) {
	root := t.TempDir()
	base := time.Date(2026, 1, 2, 12, 0, 0, 0, time.UTC)
	mdDir := filepath.Join(root, "ondemand", "jp")
	if err := os.MkdirAll(mdDir, 0o755); err != nil {
		t.Fatal(err)
	}
	event := map[string]any{"id": 10, "startAt": base.Add(-time.Hour).UnixMilli(), "aggregateAt": base.Add(time.Hour).UnixMilli()}
	raw, _ := json.Marshal([]map[string]any{event})
	if err := os.WriteFile(filepath.Join(mdDir, "events.json"), raw, 0o644); err != nil {
		t.Fatal(err)
	}
	dbDir := filepath.Join(root, "ondemand", "database", "sk_jp")
	if err := os.MkdirAll(dbDir, 0o755); err != nil {
		t.Fatal(err)
	}
	dbPath := filepath.Join(dbDir, "10_ranking.db")
	db, err := sql.Open("sqlite", "file:"+dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err := db.Exec(`CREATE TABLE ranking (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, name TEXT, score INTEGER, rank INTEGER, ts REAL)`); err != nil {
		t.Fatal(err)
	}
	old := base.Add(-30 * time.Minute)
	latest := base.Add(-time.Minute)
	for _, row := range []struct {
		score int
		ts    time.Time
	}{{100, old}, {250, latest}} {
		if _, err := db.Exec("INSERT INTO ranking (uid,name,score,rank,ts) VALUES (?,?,?,?,?)", "u1", "player", row.score, 3, float64(row.ts.Unix())); err != nil {
			t.Fatal(err)
		}
	}

	var mu sync.Mutex
	calls := 0
	var payload map[string]any
	drawServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		calls++
		_ = json.NewDecoder(r.Body).Decode(&payload)
		w.Header().Set("Content-Type", "image/png")
		_, _ = w.Write([]byte("png"))
	}))
	defer drawServer.Close()

	source := NewSKSubscriptionSource(masterdata.New(root), skstore.New(root), draw.New(drawServer.URL))
	source.now = func() time.Time { return base }
	sub := sksub.Subscription{Server: "jp", EventID: 10, UID: "u1", LastScore: 100}
	update, err := source.FetchSK(context.Background(), sub)
	if err != nil {
		t.Fatal(err)
	}
	if !update.Changed || update.Score != 250 || update.Rank != 3 || len(update.Message) != 1 {
		t.Fatalf("unexpected changed update: %#v", update)
	}
	if !strings.Contains(update.ID, "10/u1/") || !strings.Contains(update.ID, "/250") {
		t.Fatalf("stable id missing event/uid/score: %q", update.ID)
	}
	mu.Lock()
	if calls != 1 || payload["name"] != "player" || payload["score"] != float64(250) {
		t.Fatalf("unexpected draw call/payload: calls=%d payload=%#v", calls, payload)
	}
	mu.Unlock()

	update, err = source.FetchSK(context.Background(), sksub.Subscription{Server: "jp", EventID: 10, UID: "u1", LastScore: 250, LastRank: 99})
	if err != nil {
		t.Fatal(err)
	}
	if update.Changed || update.Score != 250 || update.Rank != 3 || update.ID != "" || len(update.Message) != 0 {
		t.Fatalf("rank-only change must not construct notification: %#v", update)
	}
	mu.Lock()
	if calls != 1 {
		t.Fatalf("unchanged score must not render, calls=%d", calls)
	}
	mu.Unlock()
}

func TestSKSubscriptionSourceBusinessErrors(t *testing.T) {
	root := t.TempDir()
	base := time.Now().UTC()
	mdDir := filepath.Join(root, "ondemand", "jp")
	if err := os.MkdirAll(mdDir, 0o755); err != nil {
		t.Fatal(err)
	}
	events := []map[string]any{{"id": 10, "startAt": base.Add(-time.Hour).UnixMilli(), "aggregateAt": base.Add(time.Hour).UnixMilli()}}
	raw, _ := json.Marshal(events)
	if err := os.WriteFile(filepath.Join(mdDir, "events.json"), raw, 0o644); err != nil {
		t.Fatal(err)
	}
	source := NewSKSubscriptionSource(masterdata.New(root), skstore.New(root), nil)
	source.now = func() time.Time { return base }
	for _, tc := range []struct {
		name   string
		sub    sksub.Subscription
		reason subscription.SourceStatusReason
	}{
		{"missing uid", sksub.Subscription{Server: "jp", EventID: 10}, subscription.PlayerNoRanking},
		{"no ranking", sksub.Subscription{Server: "jp", EventID: 10, UID: "u404"}, subscription.PlayerNoRanking},
		{"different event", sksub.Subscription{Server: "jp", EventID: 11, UID: "u1"}, subscription.ActivityClosed},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, err := source.FetchSK(context.Background(), tc.sub)
			if got, ok := subscription.SourceStatusReasonOf(err); !ok || got != tc.reason {
				t.Fatalf("reason=%q ok=%v err=%v", got, ok, err)
			}
		})
	}
}
