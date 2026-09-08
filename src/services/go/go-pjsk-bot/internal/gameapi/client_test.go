package gameapi

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestRequiresAuth(t *testing.T) {
	cases := map[string]bool{
		"https://x/api/public/jp/suite/1": false,
		"https://x/cn/mysekai/123":        false,
		"https://x/get_upload_time":       false,
		"https://x/api/sekai/v6/profile":  true,
		"https://x/token/cn/status":       true,
	}
	for url, want := range cases {
		if got := requiresAuth(url); got != want {
			t.Errorf("requiresAuth(%q)=%v want %v", url, got, want)
		}
	}
}

func TestMapBusinessError(t *testing.T) {
	cases := []struct {
		status int
		body   string
		want   string
	}{
		{404, `{"message":"account binding not found"}`, "未在工具箱绑定QQ和游戏账号"},
		{403, `{"message":"you are not allowed to access this player data."}`, "未在 Haruki 工具箱中游戏账号设置里勾选\"允许公开 API 访问\""},
		{503, `{"status":"maintenance_in"}`, "服务器正在维护中"},
		{403, `{"status":"user_id_ban"}`, "账号已被封禁"},
		{500, `{}`, "接口请求失败: HTTP 500"},
	}
	for _, c := range cases {
		err := mapBusinessError(c.status, []byte(c.body))
		if err == nil || err.Error() != c.want {
			t.Errorf("mapBusinessError(%d,%s)=%v want %q", c.status, c.body, err, c.want)
		}
	}
}

func TestGetMissingToken(t *testing.T) {
	// 需鉴权的接口且无 token → APIError
	c := New("")
	_, err := c.Get(context.Background(), "https://x/api/sekai/profile")
	if err == nil {
		t.Fatal("无 token 请求鉴权接口应报错")
	}
	if _, ok := err.(*APIError); !ok {
		t.Errorf("应为 APIError, got %T", err)
	}
}

func TestGetSuccess(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// public 接口无需鉴权
		w.WriteHeader(200)
		w.Write([]byte(`{"ok":true}`))
	}))
	defer srv.Close()

	c := New("")
	data, err := c.Get(context.Background(), srv.URL+"/api/public/jp/suite/1")
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if string(data) != `{"ok":true}` {
		t.Errorf("body=%q", data)
	}
}

func TestGetJSONAndBusinessError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/ban" {
			w.WriteHeader(403)
			w.Write([]byte(`{"status":"user_id_ban"}`))
			return
		}
		w.WriteHeader(200)
		w.Write([]byte(`{"n":42}`))
	}))
	defer srv.Close()

	c := New("tok")
	var out struct {
		N int `json:"n"`
	}
	if err := c.GetJSON(context.Background(), srv.URL+"/api/public/ok", &out); err != nil {
		t.Fatalf("GetJSON: %v", err)
	}
	if out.N != 42 {
		t.Errorf("N=%d want 42", out.N)
	}

	// 业务错误映射
	_, err := c.Get(context.Background(), srv.URL+"/ban")
	if err == nil || err.Error() != "账号已被封禁" {
		t.Errorf("封禁错误映射失败: %v", err)
	}
}

func TestPostJSON(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("应为 POST, got %s", r.Method)
		}
		if r.Header.Get("Content-Type") != "application/json" {
			t.Errorf("Content-Type=%q", r.Header.Get("Content-Type"))
		}
		w.WriteHeader(200)
		w.Write([]byte("binary-image-bytes"))
	}))
	defer srv.Close()

	c := New("")
	// mysekai 接口无需鉴权
	data, err := c.PostJSON(context.Background(), srv.URL+"/cn/mysekai/photo", map[string]any{"id": 1})
	if err != nil {
		t.Fatalf("PostJSON: %v", err)
	}
	if string(data) != "binary-image-bytes" {
		t.Errorf("body=%q", data)
	}
}

func TestAPIErrorMessage(t *testing.T) {
	e := &APIError{Message: "boom"}
	if e.Error() != "boom" {
		t.Errorf("Error()=%q", e.Error())
	}
}
