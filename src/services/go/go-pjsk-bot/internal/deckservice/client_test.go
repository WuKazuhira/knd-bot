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
	var payload map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/recommend" {
			t.Fatalf("path=%s", r.URL.Path)
		}
		body, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(body, &payload)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"decks":[{"score":100,"total_power":50000}]}`))
	}))
	defer srv.Close()
	decks, err := New([]string{srv.URL}, time.Second).Recommend(context.Background(), RecommendParams{Region: "jp", UserDataStr: "USERDATA", Options: map[string]any{"live_type": "challenge", "limit": 3}, Algorithm: "dfs", TimeoutMS: 15000})
	if err != nil || len(decks) != 1 {
		t.Fatalf("Recommend=%v decks=%d", err, len(decks))
	}
	if payload["region"] != "jp" || payload["user_data_str"] != "USERDATA" || payload["algorithm"] != "dfs" {
		t.Fatalf("payload=%#v", payload)
	}
}

func TestRecommendFailover(t *testing.T) {
	bad := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Error(w, "boom", http.StatusInternalServerError) }))
	defer bad.Close()
	good := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(`{"decks":[{"score":1}]}`)) }))
	defer good.Close()
	decks, err := New([]string{bad.URL, good.URL}, time.Second).Recommend(context.Background(), RecommendParams{Region: "jp"})
	if err != nil || len(decks) != 1 {
		t.Fatalf("failover=%v decks=%d", err, len(decks))
	}
}

func TestRecommendNoURLs(t *testing.T) {
	if _, err := New(nil, 0).Recommend(context.Background(), RecommendParams{}); err == nil {
		t.Fatal("无地址应报错")
	}
}
