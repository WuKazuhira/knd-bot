package pjsk

import (
	"context"
	"testing"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/remotelive"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

func skMeEvent(userID int64, text string) onebot.MessageEvent {
	return onebot.MessageEvent{
		UserID:      userID,
		MessageType: "private",
		Message:     onebot.Message{onebot.Text(text)},
	}
}

func TestSkMeResolveAccountRegion(t *testing.T) {
	m := NewSkMeModule(nil, nil, nil, "configured", "cn", []int64{1})
	cases := []struct {
		name       string
		req        router.Request
		wantAcct   string
		wantRegion string
		wantErr    bool
	}{
		{
			name:       "config default overrides jp prefix",
			req:        router.Request{Event: skMeEvent(1, "skme"), RawCmd: "skme", Server: router.ServerJP},
			wantAcct:   "configured",
			wantRegion: "cn",
		},
		{
			name:       "explicit account uses command region",
			req:        router.Request{Event: skMeEvent(1, "cnskme account-2"), RawCmd: "cnskme", Server: router.ServerCN, Arg: "account-2"},
			wantAcct:   "account-2",
			wantRegion: "cn",
		},
		{
			name:    "multiple fields rejected",
			req:     router.Request{RawCmd: "skme", Server: router.ServerJP, Arg: "a b"},
			wantErr: true,
		},
		{
			name:    "unsafe account rejected",
			req:     router.Request{RawCmd: "skme", Server: router.ServerJP, Arg: "../db"},
			wantErr: true,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			acct, region, err := m.resolveAccountRegion(tc.req)
			if tc.wantErr {
				if err == nil {
					t.Fatal("应返回参数错误")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if acct != tc.wantAcct || region != tc.wantRegion {
				t.Fatalf("解析得到 %s/%s，期望 %s/%s", acct, region, tc.wantAcct, tc.wantRegion)
			}
		})
	}
}

func TestSkMeMissingConfiguredAccount(t *testing.T) {
	m := NewSkMeModule(nil, nil, nil, "", "cn", nil)
	_, _, err := m.resolveAccountRegion(router.Request{RawCmd: "skme", Server: router.ServerJP})
	if err == nil {
		t.Fatal("缺少账号配置时应返回明确错误")
	}
}

func TestSkMeLiveRecordPayload(t *testing.T) {
	rank := 12
	record := remotelive.Record{TS: 123, EventRank: &rank}
	payload := liveRecordPayload(record)
	if payload["ts"] != int64(123) {
		t.Errorf("ts payload=%v", payload["ts"])
	}
	if payload["event_rank"] != 12 {
		t.Errorf("event_rank payload=%v", payload["event_rank"])
	}
	if payload["event_point"] != nil || payload["wl_chapter_rank"] != nil {
		t.Errorf("NULL 字段应保留 nil: %+v", payload)
	}
}

func TestSkMeTimeRangeFallback(t *testing.T) {
	r1, r2 := 10, 20
	records := []remotelive.Record{{TS: 200, EventRank: &r1}, {TS: 100, EventRank: &r2}}
	got := meCurveTimeRange(map[string]any{}, records)
	if len(got) != 2 || got[0] != 100 || got[1] != 200 {
		t.Fatalf("时间范围 fallback=%v", got)
	}
	got = meCurveTimeRange(map[string]any{"startAt": float64(100000), "aggregateAt": float64(200000)}, records)
	if got[0] != 100 || got[1] != 200 {
		t.Fatalf("活动时间范围=%v", got)
	}
}

func TestSkMeEventRemainText(t *testing.T) {
	if got := eventRemainText(map[string]any{"aggregateAt": float64((time.Now().Unix() - 60) * 1000)}); got != "已结束" {
		t.Fatalf("结束活动 remain=%q", got)
	}
	if got := eventRemainText(map[string]any{}); got != "未知" {
		t.Fatalf("无结束时间 remain=%q", got)
	}
}

func TestSkMeRouteAliasesAndPrefixes(t *testing.T) {
	m := NewSkMeModule(nil, nil, nil, "account", "cn", []int64{1})
	r := router.New([]string{"/", ""}, router.ParseOwnership(`["skme"]`))
	m.Register(r)
	cases := []struct {
		text   string
		server router.ServerType
	}{
		{"skme", router.ServerJP},
		{"cnskme account", router.ServerCN},
		{"twsk我的曲线", router.ServerTW},
	}
	for _, tc := range cases {
		req, handler, ok := r.Match(skMeEvent(1, tc.text))
		if !ok || handler == nil {
			t.Fatalf("%q 未命中 skme", tc.text)
		}
		if req.Command != "skme" || req.Server != tc.server {
			t.Fatalf("%q => command=%q server=%v", tc.text, req.Command, req.Server)
		}
	}
	// 非超管执行 handler 时保持静默；这里不触发外部依赖。
	req, handler, _ := r.Match(skMeEvent(2, "skme"))
	if got := handler(context.Background(), req); got != nil {
		t.Fatal("非超管 skme 应静默")
	}
}
