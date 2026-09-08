package deckservice

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestRecommendPayloadAndResponse(t *testing.T) {
	var gotPayload map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/recommend" {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		body, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(body, &gotPayload)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"decks":[{"score":100,"total_power":50000},{"score":90}]}`))
	}))
	defer srv.Close()

	c := New([]string{srv.URL}, 5*time.Second)
	decks, err := c.Recommend(context.Background(), RecommendParams{
		Region:      "jp",
		UserDataStr: "USERDATA",
		Options:     map[string]any{"live_type": "challenge", "limit": 3},
		Algorithm:   "dfs",
		TimeoutMS:   15000,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(decks) != 2 {
		t.Fatalf("应返回 2 套卡组, got %d", len(decks))
	}
	// 校验 payload 组装
	if gotPayload["region"] != "jp" || gotPayload["user_data_str"] != "USERDATA" {
		t.Errorf("payload region/user_data_str 错误: %v", gotPayload)
	}
	if gotPayload["algorithm"] != "dfs" || gotPayload["live_type"] != "challenge" {
		t.Errorf("payload algorithm/options 错误: %v", gotPayload)
	}
}

func TestRecommendFailover(t *testing.T) {
	// 第一个地址失败，第二个成功
	bad := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, `{"error":"boom"}`, http.StatusInternalServerError)
	}))
	defer bad.Close()
	good := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"decks":[{"score":1}]}`))
	}))
	defer good.Close()

	c := New([]string{bad.URL, good.URL}, 5*time.Second)
	decks, err := c.Recommend(context.Background(), RecommendParams{Region: "jp", UserDataStr: "x"})
	if err != nil {
		t.Fatalf("故障转移应成功: %v", err)
	}
	if len(decks) != 1 {
		t.Fatalf("应从第二个地址取得 1 套卡组, got %d", len(decks))
	}
}

func TestRecommendNoURLs(t *testing.T) {
	c := New(nil, 0)
	if _, err := c.Recommend(context.Background(), RecommendParams{}); err == nil {
		t.Fatal("无地址应返回错误")
	}
}
