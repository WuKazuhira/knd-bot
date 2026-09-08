package helper

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestRegionMapping(t *testing.T) {
	cases := map[int]string{0: "jp", 1: "tw", 2: "cn", 99: "jp"}
	for st, want := range cases {
		if got := region(st); got != want {
			t.Errorf("region(%d)=%q want %q", st, got, want)
		}
	}
}

func TestEndpointPaths(t *testing.T) {
	var gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		w.WriteHeader(200)
		w.Write([]byte(`{"ok":true}`))
	}))
	defer srv.Close()
	c := New(srv.URL)
	ctx := context.Background()

	if _, err := c.Suite(ctx, 0, "12345"); err != nil {
		t.Fatalf("Suite: %v", err)
	}
	if gotPath != "/suite/jp/12345" {
		t.Errorf("Suite 路径=%q", gotPath)
	}

	if _, err := c.B30(ctx, 2, "999"); err != nil {
		t.Fatalf("B30: %v", err)
	}
	if gotPath != "/b30/cn/999" {
		t.Errorf("B30 路径=%q", gotPath)
	}

	if _, err := c.RankingLatest(ctx, 1); err != nil {
		t.Fatalf("RankingLatest: %v", err)
	}
	if gotPath != "/ranking/tw/latest" {
		t.Errorf("RankingLatest 路径=%q", gotPath)
	}
}

func TestGetReturnsBody(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(200)
		w.Write([]byte(`{"score":100}`))
	}))
	defer srv.Close()
	c := New(srv.URL)
	data, err := c.Suite(context.Background(), 0, "1")
	if err != nil {
		t.Fatalf("Suite: %v", err)
	}
	if string(data) != `{"score":100}` {
		t.Errorf("body=%q", data)
	}
}

func TestGetErrorStatus(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(404)
	}))
	defer srv.Close()
	c := New(srv.URL)
	_, err := c.B30(context.Background(), 0, "1")
	if err == nil {
		t.Fatal("404 应返回错误")
	}
}

func TestNewTrimsTrailingSlash(t *testing.T) {
	c := New("http://helper:8000/")
	if c.baseURL != "http://helper:8000" {
		t.Errorf("baseURL=%q 应去掉尾斜杠", c.baseURL)
	}
}

func TestConnectionError(t *testing.T) {
	// 指向一个不可达地址，验证请求错误被包装
	c := New("http://127.0.0.1:1")
	_, err := c.RankingLatest(context.Background(), 0)
	if err == nil {
		t.Error("不可达地址应返回错误")
	}
}
