package pjsk

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/serverconfig"
)

func TestCardAssetModuleLoadCardImages(t *testing.T) {
	root := t.TempDir()
	writeJSON(t, filepath.Join(root, "ondemand", "jp", "cards.json"), []map[string]any{{
		"id":              1001,
		"assetbundleName": "some_card",
		"cardRarityType":  "rarity_4",
	}})
	fixture := cardPNGFixture(t)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "image/png")
		_, _ = w.Write(fixture)
	}))
	defer server.Close()

	configDir := t.TempDir()
	writeServersYAML(t, configDir, fmt.Sprintf(`jp:
  rip:
    sources:
    - name: sekai.best
      base_url: %s/
`, server.URL))
	servers, err := serverconfig.Load(configDir)
	if err != nil {
		t.Fatal(err)
	}
	module := NewCardAssetModule(masterdata.New(root), servers, root)
	images, err := module.loadCardImages(context.Background(), 1001, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(images) != 2 {
		t.Fatalf("images=%d want 2", len(images))
	}
	for _, data := range images {
		if _, format, err := image.Decode(bytes.NewReader(data)); err != nil || format != "jpeg" {
			t.Fatalf("decoded card image format=%q err=%v", format, err)
		}
	}
	for _, name := range []string{"card_normal.jpg", "card_after_training.jpg"} {
		path := filepath.Join(root, "jp", "startapp", "character", "member", "some_card", name)
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("missing cached %s: %v", name, err)
		}
	}
}

func writeJSON(t *testing.T, path string, value any) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatal(err)
	}
}

func writeServersYAML(t *testing.T, configDir, content string) {
	t.Helper()
	path := filepath.Join(configDir, "pjsk", "servers.yaml")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func cardPNGFixture(t *testing.T) []byte {
	t.Helper()
	img := image.NewRGBA(image.Rect(0, 0, 2, 2))
	img.Set(0, 0, color.RGBA{R: 255, A: 255})
	img.Set(1, 0, color.RGBA{G: 255, A: 255})
	img.Set(0, 1, color.RGBA{B: 255, A: 255})
	img.Set(1, 1, color.RGBA{R: 255, G: 255, B: 255, A: 255})
	var out bytes.Buffer
	if err := png.Encode(&out, img); err != nil {
		t.Fatal(err)
	}
	return out.Bytes()
}
