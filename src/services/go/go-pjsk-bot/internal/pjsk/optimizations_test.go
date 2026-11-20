package pjsk

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

func TestBanEventAndFindEventShortcut(t *testing.T) {
	dir := t.TempDir()
	jpDir := filepath.Join(dir, "ondemand", "jp")
	if err := os.MkdirAll(jpDir, 0o755); err != nil {
		t.Fatal(err)
	}
	writeJSON := func(name, content string) {
		if err := os.WriteFile(filepath.Join(jpDir, name), []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	writeJSON("events.json", `[
		{"id": 10, "eventType": "marathon", "name": "Ev1", "startAt": 1000},
		{"id": 20, "eventType": "marathon", "name": "Ev2", "startAt": 2000},
		{"id": 74, "eventType": "cheerful_carnival", "name": "SDL3", "startAt": 1500}
	]`)
	writeJSON("eventMusics.json", `[
		{"eventId": 10, "musicId": 101},
		{"eventId": 20, "musicId": 102}
	]`)
	writeJSON("eventCards.json", `[
		{"eventId": 10, "cardId": 1001},
		{"eventId": 10, "cardId": 1002},
		{"eventId": 20, "cardId": 2001},
		{"eventId": 74, "cardId": 3001}
	]`)
	writeJSON("cardSupplies.json", `[
		{"id": 1, "cardSupplyType": "normal"},
		{"id": 2, "cardSupplyType": "colorful_festival_limited"}
	]`)
	writeJSON("cards.json", `[
		{"id": 1001, "characterId": 10, "cardSupplyId": 1},
		{"id": 1002, "characterId": 11, "cardSupplyId": 1},
		{"id": 2001, "characterId": 10, "cardSupplyId": 1},
		{"id": 3001, "characterId": 10, "cardSupplyId": 1}
	]`)

	md := masterdata.New(dir)
	resolve := func(alias string) int {
		if alias == "an" {
			return 10
		}
		return 0
	}

	ev, rest, errMsg := extractBanEventArg(md, 0, "an2", resolve)
	if errMsg != "" || ev == nil || ev.ID != 74 || rest != "" {
		t.Fatalf("expected an2 -> event 74 (SDL3), got ev=%+v rest=%q err=%q", ev, rest, errMsg)
	}
	ev3, _, errMsg := extractBanEventArg(md, 0, "an3", resolve)
	if errMsg != "" || ev3 == nil || ev3.ID != 20 {
		t.Fatalf("expected an3 -> event 20, got ev=%+v err=%q", ev3, errMsg)
	}
	_, _, errOver := extractBanEventArg(md, 0, "an4", resolve)
	if errOver != "角色an只有3次箱活" {
		t.Fatalf("expected overflow message, got %q", errOver)
	}
}

func TestSongSearchIndexAndChartBPMCache(t *testing.T) {
	dir := t.TempDir()
	jpDir := filepath.Join(dir, "ondemand", "jp")
	scoreDir := filepath.Join(jpDir, "startapp", "music", "music_score", "0001_01")
	if err := os.MkdirAll(scoreDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(jpDir, "musics.json"), []byte(`[{"id":1,"title":"Tell Your World","publishedAt":1000}]`), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(jpDir, "translate.yaml"), []byte("1: 告诉你的世界 / TYW\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	chartPath := filepath.Join(scoreDir, "expert")
	if err := os.WriteFile(chartPath, []byte("#BPM01: 150\n#00008: 0100\n#00112: 11000000\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	md := masterdata.New(dir)
	sm := NewSongModule(md, nil, nil, dir, nil)

	res := sm.findSong(context.Background(), "tyw", 0)
	if !res.found || res.musicID != 1 {
		t.Fatalf("expected normalized translation match, got %+v", res)
	}
	bpms := sm.chartBPM(1, 0)
	if len(bpms) != 1 || bpms[0] != "150" {
		t.Fatalf("expected BPM [150], got %v", bpms)
	}
	// 修改谱面文件后应失效重建
	if err := os.WriteFile(chartPath, []byte("#BPM01: 150\n#BPM02: 180\n#00008: 0102\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	bpms2 := sm.chartBPM(1, 0)
	if len(bpms2) != 2 || bpms2[0] != "150" || bpms2[1] != "180" {
		t.Fatalf("expected updated BPM [150 180], got %v", bpms2)
	}
}

func TestRouterFirstByteBucketAndNumericSuffix(t *testing.T) {
	r := router.New([]string{"/", ""}, router.All())
	r.RegisterNumericSuffix("findevent", []string{"查活动"}, func(ctx context.Context, req router.Request) *onebot.ActionRequest {
		return onebot.ReplyText(req.Event, req.Command+":"+req.Arg, false)
	})
	r.RegisterRegex("pjskset", `(?i)^(cn|tw)?pjskset\s*(.+to.+)$`, func(ctx context.Context, req router.Request) *onebot.ActionRequest {
		return onebot.ReplyText(req.Event, req.Command, false)
	})

	makeMsg := func(text string) onebot.MessageEvent {
		return onebot.MessageEvent{Message: onebot.Message{{Type: "text", Data: map[string]any{"text": text}}}}
	}
	if req, _, ok := r.Match(makeMsg("/查活动 an7")); !ok || req.Command != "findevent" || req.Arg != "an7" {
		t.Fatalf("expected findevent an7, got ok=%v req=%+v", ok, req)
	}
	if req, _, ok := r.Match(makeMsg("CNFINDEVENT 10")); !ok || req.Server != router.ServerCN || req.Arg != "10" {
		t.Fatalf("expected CN findevent 10, got ok=%v req=%+v", ok, req)
	}
	if _, _, ok := r.Match(makeMsg("hello world")); ok {
		t.Fatalf("unexpected match for normal chat")
	}
	if req, _, ok := r.Match(makeMsg("cnpjskset foo to bar")); !ok || req.Command != "pjskset" {
		t.Fatalf("expected regex route pjskset, got ok=%v req=%+v", ok, req)
	}
}
