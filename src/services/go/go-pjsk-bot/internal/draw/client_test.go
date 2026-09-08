package draw

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestRenderBinaryResponse(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("应为 POST, got %s", r.Method)
		}
		if !strings.HasPrefix(r.URL.Path, "/render/") {
			t.Errorf("路径应为 /render/*, got %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "image/jpeg")
		w.WriteHeader(200)
		w.Write([]byte("JPEGDATA"))
	}))
	defer srv.Close()

	c := New(srv.URL)
	img, err := c.Render(context.Background(), "profile", map[string]any{"x": 1})
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	if string(img) != "JPEGDATA" {
		t.Errorf("img=%q", img)
	}
}

func TestRenderJSONEnvelope(t *testing.T) {
	img1 := base64.StdEncoding.EncodeToString([]byte("img-one"))
	img2 := base64.StdEncoding.EncodeToString([]byte("img-two"))
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.WriteHeader(200)
		json.NewEncoder(w).Encode(jsonEnvelope{
			Images: []string{img1, img2},
			Meta:   map[string]any{"source": "sekai_viewer"},
		})
	}))
	defer srv.Close()

	c := New(srv.URL)
	images, meta, err := c.RenderWithMeta(context.Background(), "map_preview", nil)
	if err != nil {
		t.Fatalf("RenderWithMeta: %v", err)
	}
	if len(images) != 2 || string(images[0]) != "img-one" || string(images[1]) != "img-two" {
		t.Errorf("images=%v", images)
	}
	if meta["source"] != "sekai_viewer" {
		t.Errorf("meta=%v", meta)
	}

	// RenderMulti 走同一路径
	multi, err := c.RenderMulti(context.Background(), "skill_preview", nil)
	if err != nil || len(multi) != 2 {
		t.Errorf("RenderMulti=%v err=%v", multi, err)
	}
}

func TestRenderErrorStatus(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(500)
		w.Write([]byte("internal error"))
	}))
	defer srv.Close()

	c := New(srv.URL)
	_, err := c.Render(context.Background(), "x", nil)
	if err == nil || !strings.Contains(err.Error(), "500") {
		t.Errorf("应返回含 500 的错误, got %v", err)
	}
}

func TestRenderEmptyResponse(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "image/png")
		w.WriteHeader(200)
		// 空响应体
	}))
	defer srv.Close()

	c := New(srv.URL)
	_, err := c.Render(context.Background(), "x", nil)
	if err == nil || !strings.Contains(err.Error(), "空响应") {
		t.Errorf("空响应应报错, got %v", err)
	}
}

func TestRenderEmptyImagesEnvelope(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(200)
		w.Write([]byte(`{"images":[],"meta":{}}`))
	}))
	defer srv.Close()

	c := New(srv.URL)
	// Render 要求至少一张图
	_, err := c.Render(context.Background(), "x", nil)
	if err == nil || !strings.Contains(err.Error(), "未返回图片") {
		t.Errorf("空 images 时 Render 应报错, got %v", err)
	}
	// RenderMulti 允许空
	multi, err := c.RenderMulti(context.Background(), "x", nil)
	if err != nil || len(multi) != 0 {
		t.Errorf("RenderMulti 空应为 (nil,nil), got %v err=%v", multi, err)
	}
}

func TestRenderBadBase64(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(200)
		w.Write([]byte(`{"images":["!!not-base64!!"]}`))
	}))
	defer srv.Close()

	c := New(srv.URL)
	_, err := c.Render(context.Background(), "x", nil)
	if err == nil || !strings.Contains(err.Error(), "b64") {
		t.Errorf("非法 base64 应报错, got %v", err)
	}
}

func TestTruncate(t *testing.T) {
	if got := truncate([]byte("hello"), 10); got != "hello" {
		t.Errorf("truncate 短串=%q", got)
	}
	if got := truncate([]byte("hello world"), 5); got != "hello" {
		t.Errorf("truncate 截断=%q", got)
	}
}

func TestNewTrimsTrailingSlash(t *testing.T) {
	c := New("http://draw:45560/")
	if c.baseURL != "http://draw:45560" {
		t.Errorf("baseURL=%q 应去掉尾斜杠", c.baseURL)
	}
}
