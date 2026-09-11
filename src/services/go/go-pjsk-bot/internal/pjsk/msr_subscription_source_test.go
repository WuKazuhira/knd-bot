package pjsk

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/gameapi"
	"github.com/kazuhira/go-pjsk-bot/internal/msrsub"
	"github.com/kazuhira/go-pjsk-bot/internal/mysekaidata"
	"github.com/kazuhira/go-pjsk-bot/internal/serverconfig"
)

func TestParseMSRUploadTimes(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name string
		raw  string
		want []int64
	}{
		{"array numbers", `[1,2]`, []int64{1, 2}},
		{"array strings", `["3","4"]`, []int64{3, 4}},
		{"single number", `5`, []int64{5}},
		{"single string", `"6"`, []int64{6}},
		{"upload_times", `{"upload_times":[7,"8"]}`, []int64{7, 8}},
		{"timestamps", `{"timestamps":[9]}`, []int64{9}},
		{"data", `{"data":[10]}`, []int64{10}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := ParseMSRUploadTimes([]byte(tc.raw))
			if err != nil {
				t.Fatal(err)
			}
			if fmt.Sprint(got) != fmt.Sprint(tc.want) {
				t.Fatalf("got %v, want %v", got, tc.want)
			}
		})
	}
	for _, raw := range []string{`{}`, `[1,"bad"]`, `true`, `1 2`, `1.5`} {
		t.Run("invalid "+raw, func(t *testing.T) {
			if _, err := ParseMSRUploadTimes([]byte(raw)); err == nil {
				t.Fatalf("expected error for %s", raw)
			}
		})
	}
}

func TestLastMSRRefreshTimeAndShouldPush(t *testing.T) {
	loc := time.UTC
	jpEarly := time.Date(2026, 1, 2, 4, 5, 0, 0, loc)
	if got, want := LastMSRRefreshTime(0, jpEarly), time.Date(2026, 1, 2, 4, 0, 0, 0, loc); !got.Equal(want) {
		t.Fatalf("jp early refresh=%v, want %v", got, want)
	}
	cnMorning := time.Date(2026, 1, 2, 5, 5, 0, 0, loc)
	if got, want := LastMSRRefreshTime(2, cnMorning), time.Date(2026, 1, 2, 5, 0, 0, 0, loc); !got.Equal(want) {
		t.Fatalf("cn morning refresh=%v, want %v", got, want)
	}
	now := time.Date(2026, 1, 2, 16, 5, 0, 0, loc)
	upload := now.Add(-4 * time.Minute).Unix()
	if !ShouldPushMSR(upload, time.Time{}, 0, now) {
		t.Fatal("recent upload should push")
	}
	if ShouldPushMSR(now.Add(-11*time.Minute).Unix(), time.Time{}, 0, now) {
		t.Fatal("old upload should not push")
	}
	if ShouldPushMSR(upload, time.Date(2026, 1, 2, 16, 0, 0, 0, loc), 0, now) {
		t.Fatal("last push at refresh should skip")
	}
	if ShouldPushMSR(time.Date(2026, 1, 2, 15, 59, 0, 0, loc).Unix(), time.Time{}, 0, now) {
		t.Fatal("upload before refresh should skip")
	}
}

func TestBuildMSRDrawPayloads(t *testing.T) {
	payloads := BuildMSRDrawPayloads(
		map[string]any{"userid": "1"},
		map[string]any{"upload_time": int64(7)},
		map[string]any{"userGamedata": map[string]any{}},
		1,
		"mysekai warning",
		"suite warning",
	)
	if len(payloads) != 3 || payloads[0].Name != "mysekai_summary" || payloads[1].Name != "mysekai_res_list" || payloads[2].Name != "mysekai_map" {
		t.Fatalf("unexpected payloads: %#v", payloads)
	}
	if payloads[0].Payload["data_msg"] != "mysekai warning" || payloads[0].Payload["fast_render"] != true {
		t.Fatalf("summary payload=%#v", payloads[0].Payload)
	}
	if payloads[1].Payload["show_harvested"] != false {
		t.Fatalf("res payload=%#v", payloads[1].Payload)
	}
	privatePayloads := BuildMSRDrawPayloadsForPrivate(
		map[string]any{"userid": "1"},
		map[string]any{},
		map[string]any{},
		1,
		true,
		"",
		"",
	)
	if privatePayloads[0].Payload["is_private"] != true {
		t.Fatalf("private payload=%#v", privatePayloads[0].Payload)
	}
}

func TestMSRSubscriptionSourceFetchBatchHTTPAndCache(t *testing.T) {
	t.Parallel()
	var mu sync.Mutex
	calls := make(map[string]int)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		calls[r.URL.Path]++
		mu.Unlock()
		switch {
		case r.URL.Path == "/get_upload_time":
			var pairs [][]string
			if err := json.NewDecoder(r.Body).Decode(&pairs); err != nil || len(pairs) != 1 || pairs[0][0] != "123" || pairs[0][1] != "latest" {
				t.Errorf("unexpected upload body: %#v", pairs)
			}
			_, _ = w.Write([]byte(`[1700000000]`))
		case strings.HasPrefix(r.URL.Path, "/suite/"):
			_, _ = w.Write([]byte(`{"name":"tester","rank":10,"userGamedata":{"deck":1,"name":"tester","rank":10},"userDecks":[],"userCards":[]}`))
		case strings.HasPrefix(r.URL.Path, "/mysekai/"):
			_, _ = w.Write([]byte(`{"upload_time":1700000000,"updatedResources":{}}`))
		case strings.HasPrefix(r.URL.Path, "/render/"):
			w.Header().Set("Content-Type", "image/png")
			_, _ = w.Write([]byte("image:" + strings.TrimPrefix(r.URL.Path, "/render/")))
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	dir := t.TempDir()
	configPath := filepath.Join(dir, "pjsk", "servers.yaml")
	if err := os.MkdirAll(filepath.Dir(configPath), 0o755); err != nil {
		t.Fatal(err)
	}
	yaml := fmt.Sprintf("jp:\n  api:\n    suite_api_url: %s/suite/{uid}\n    mysekai_api_url: %s/mysekai/{uid}\n    mysekai_upload_time_api_url: %s/get_upload_time\n", server.URL, server.URL, server.URL)
	if err := os.WriteFile(configPath, []byte(yaml), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg, err := serverconfig.Load(dir)
	if err != nil {
		t.Fatal(err)
	}
	api := gameapi.New("test-token")
	fetcher := mysekaidata.NewFetcher(api, cfg, dir)
	source := NewMSRSubscriptionSource(fetcher, cfg, api, draw.New(server.URL))
	now := time.Unix(1700000005, 0).UTC()
	source.now = func() time.Time { return now }
	sub := msrsub.Subscription{ID: 1, Server: "jp", UID: "123", Mode: "latest"}

	items, err := source.FetchBatch(context.Background(), []msrsub.Subscription{sub})
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 1 || items[0].ID != "msr/jp/123/1700000000" {
		t.Fatalf("items=%#v", items)
	}
	if got := items[0].Message.PlainText(); got != " 的 JP MSR 数据已更新" {
		t.Fatalf("message text=%q", got)
	}
	if len(items[0].Message) != 4 {
		t.Fatalf("message segments=%#v", items[0].Message)
	}

	if _, err := source.FetchBatch(context.Background(), []msrsub.Subscription{sub}); err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	defer mu.Unlock()
	if calls["/get_upload_time"] != 2 || calls["/suite/123"] != 1 || calls["/mysekai/123"] != 1 || calls["/render/mysekai_summary"] != 1 || calls["/render/mysekai_res_list"] != 1 || calls["/render/mysekai_map"] != 1 {
		t.Fatalf("calls=%v", calls)
	}
}
