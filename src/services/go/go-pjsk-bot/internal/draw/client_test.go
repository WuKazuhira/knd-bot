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

func newTestClient(t *testing.T, handler http.HandlerFunc) *Client {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	return New(server.URL)
}

func TestRenderSendsJSONAndReturnsImageBytes(t *testing.T) {
	wantImage := []byte{0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 'i', 'm', 'a', 'g', 'e'}
	client := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("method = %q, want POST", r.Method)
		}
		if r.URL.Path != "/render/song_info" {
			t.Errorf("path = %q, want /render/song_info", r.URL.Path)
		}
		if got := r.Header.Get("Content-Type"); got != "application/json" {
			t.Errorf("Content-Type = %q, want application/json", got)
		}

		var payload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Errorf("decode request payload: %v", err)
		} else if payload["music_id"] != float64(39) {
			t.Errorf("music_id = %v, want 39", payload["music_id"])
		}

		w.Header().Set("Content-Type", "image/png")
		_, _ = w.Write(wantImage)
	})

	got, err := client.Render(context.Background(), "song_info", map[string]any{"music_id": 39})
	if err != nil {
		t.Fatalf("Render() error = %v", err)
	}
	if string(got) != string(wantImage) {
		t.Fatalf("Render() = %q, want %q", got, wantImage)
	}
}

func TestRenderWithMetaDecodesImagesInOrder(t *testing.T) {
	first := []byte("first image")
	second := []byte("second image")
	client := newTestClient(t, func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"images": []string{
				base64.StdEncoding.EncodeToString(first),
				base64.StdEncoding.EncodeToString(second),
			},
			"meta": map[string]any{"pages": 2},
		})
	})

	images, meta, err := client.RenderWithMeta(context.Background(), "song_quiz", map[string]any{})
	if err != nil {
		t.Fatalf("RenderWithMeta() error = %v", err)
	}
	if len(images) != 2 || string(images[0]) != string(first) || string(images[1]) != string(second) {
		t.Fatalf("RenderWithMeta() images = %q, want ordered images %q and %q", images, first, second)
	}
	if meta["pages"] != float64(2) {
		t.Errorf("meta[pages] = %v, want 2", meta["pages"])
	}
}

func TestRenderReturnsHTTPStatusAndTaskName(t *testing.T) {
	client := newTestClient(t, func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "unknown renderer", http.StatusNotFound)
	})

	_, err := client.Render(context.Background(), "missing_task", map[string]any{})
	if err == nil {
		t.Fatal("Render() error = nil, want HTTP status error")
	}
	for _, want := range []string{"missing_task", "404", "unknown renderer"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("Render() error = %q, want it to contain %q", err, want)
		}
	}
}

func TestRenderRejectsInvalidResponses(t *testing.T) {
	tests := []struct {
		name        string
		contentType string
		body        string
		wantError   string
	}{
		{
			name:        "malformed JSON envelope",
			contentType: "application/json",
			body:        `{"images":`,
			wantError:   "decode draw envelope",
		},
		{
			name:        "invalid Base64 image",
			contentType: "application/json",
			body:        `{"images":["%%%"],"meta":{}}`,
			wantError:   "decode image b64",
		},
		{
			name:        "empty image response",
			contentType: "image/png",
			body:        "",
			wantError:   "返回空响应",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			client := newTestClient(t, func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", tt.contentType)
				_, _ = w.Write([]byte(tt.body))
			})

			_, err := client.Render(context.Background(), "test_task", map[string]any{})
			if err == nil || !strings.Contains(err.Error(), tt.wantError) {
				t.Fatalf("Render() error = %v, want substring %q", err, tt.wantError)
			}
		})
	}
}

func TestRenderRejectsEmptyJSONEnvelope(t *testing.T) {
	client := newTestClient(t, func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"images":[],"meta":{}}`))
	})

	_, err := client.Render(context.Background(), "empty_task", map[string]any{})
	if err == nil || !strings.Contains(err.Error(), "未返回图片") {
		t.Fatalf("Render() error = %v, want missing-image error", err)
	}
}
