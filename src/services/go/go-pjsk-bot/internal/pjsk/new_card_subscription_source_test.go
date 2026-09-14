package pjsk

import (
	"context"
	"encoding/base64"
	"image"
	"image/color"
	"image/png"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/serverconfig"
)

func TestSelectNewCardCandidatesWindowAndOrder(t *testing.T) {
	now := time.UnixMilli(1_700_000_000_000)
	events := []map[string]any{
		{"id": float64(1), "startAt": float64(now.Add(2 * time.Hour).UnixMilli()), "aggregateAt": float64(now.Add(24 * time.Hour).UnixMilli())},
		{"id": float64(2), "startAt": float64(now.Add(3 * 24 * time.Hour).UnixMilli()), "aggregateAt": float64(now.Add(4 * 24 * time.Hour).UnixMilli())},
		{"id": float64(3), "startAt": float64(now.Add(-3 * 24 * time.Hour).UnixMilli()), "aggregateAt": float64(now.Add(-2 * 24 * time.Hour).UnixMilli())},
	}
	eventCards := []map[string]any{
		{"eventId": float64(1), "cardId": float64(10)},
		{"eventId": float64(2), "cardId": float64(20)},
		{"eventId": float64(3), "cardId": float64(30)},
	}
	cards := []map[string]any{
		{"id": float64(10), "releaseAt": float64(now.Add(1 * time.Hour).UnixMilli())},
		{"id": float64(20), "releaseAt": float64(now.Add(2 * 24 * time.Hour).UnixMilli())},
		{"id": float64(30), "releaseAt": float64(now.Add(-3 * 24 * time.Hour).UnixMilli())},
	}
	got := selectNewCardCandidates(events, eventCards, cards, now, false)
	if len(got) != 2 || got[0].eventID != 2 || got[1].eventID != 1 {
		t.Fatalf("auto candidates=%#v", got)
	}
	manual := selectNewCardCandidates(events, eventCards, cards, now, true)
	if len(manual) != 2 || manual[0].eventID != 2 || manual[1].eventID != 1 {
		t.Fatalf("manual candidates=%#v", manual)
	}
}

func TestNewCardSubscriptionSourceBuildsForwardNodes(t *testing.T) {
	now := time.UnixMilli(1_700_000_000_000)
	root := t.TempDir()
	writeSourceMD(t, root, "events.json", `[{"id":1,"eventType":"marathon","name":"测试活动","assetbundleName":"event_asset","startAt":1700003600000,"aggregateAt":1700086400000}]`)
	writeSourceMD(t, root, "eventCards.json", `[{"eventId":1,"cardId":10}]`)
	writeSourceMD(t, root, "eventDeckBonuses.json", `[]`)
	writeSourceMD(t, root, "cards.json", `[{"id":10,"characterId":1,"assetbundleName":"card_asset","cardRarityType":"rarity_4","attr":"cool","prefix":"测试卡","releaseAt":1700001800000}]`)

	pngData := testPNG(t)
	drawServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/render/event_info" {
			_, _ = w.Write([]byte("event-image"))
			return
		}
		w.Header().Set("Content-Type", "image/png")
		_, _ = w.Write(pngData)
	}))
	defer drawServer.Close()

	configDir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(configDir, "pjsk"), 0o755); err != nil {
		t.Fatal(err)
	}
	config := "jp:\n  rip:\n    sources:\n      - name: sekai.best\n        base_url: " + drawServer.URL + "/\n"
	if err := os.WriteFile(filepath.Join(configDir, "pjsk", "servers.yaml"), []byte(config), 0o644); err != nil {
		t.Fatal(err)
	}
	servers, err := serverconfig.Load(configDir)
	if err != nil {
		t.Fatal(err)
	}

	source := NewNewCardSubscriptionSource(masterdata.New(root), drawClientForTest(drawServer.URL), servers, root)
	source.now = func() time.Time { return now }
	item, ok, err := source.BuildLatest(context.Background(), false)
	if err != nil || !ok {
		t.Fatalf("BuildLatest: ok=%v err=%v", ok, err)
	}
	if len(item.ForwardNodes) != 5 {
		t.Fatalf("nodes=%d %#v", len(item.ForwardNodes), item.ForwardNodes)
	}
	if item.ID != "event/1/cards/10" {
		t.Fatalf("stable id=%q", item.ID)
	}
	first := item.ForwardNodes[0].Data.Content
	if len(first) != 1 || first[0].Type != "image" {
		t.Fatalf("first node=%#v", first)
	}
	if got := first[0].Data["file"].(string); got != "base64://"+base64.StdEncoding.EncodeToString([]byte("event-image")) {
		t.Fatalf("event image=%q", got)
	}
}

func drawClientForTest(url string) *draw.Client {
	return draw.New(url)
}

func writeSourceMD(t *testing.T, root, filename, content string) {
	t.Helper()
	dir := filepath.Join(root, "ondemand", "jp")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, filename), []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func testPNG(t *testing.T) []byte {
	t.Helper()
	var buf []byte
	imageData := image.NewRGBA(image.Rect(0, 0, 2, 2))
	for y := 0; y < 2; y++ {
		for x := 0; x < 2; x++ {
			imageData.Set(x, y, color.RGBA{R: 255, A: 255})
		}
	}
	file := filepath.Join(t.TempDir(), "asset.png")
	f, err := os.Create(file)
	if err != nil {
		t.Fatal(err)
	}
	if err := png.Encode(f, imageData); err != nil {
		f.Close()
		t.Fatal(err)
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}
	buf, err = os.ReadFile(file)
	if err != nil {
		t.Fatal(err)
	}
	return buf
}
