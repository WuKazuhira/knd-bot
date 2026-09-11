package pjsk

import (
	"image"
	"image/color"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestParseTrailingInt(t *testing.T) {
	cases := []struct {
		text, marker string
		want         int
		ok           bool
	}{
		{"模糊 5", "模糊", 5, true},
		{"模糊5", "模糊", 5, true},
		{"横屏 模糊 10 透明 30", "模糊", 10, true},
		{"透明30", "透明", 30, true},
		{"竖屏", "模糊", 0, false},
		{"模糊abc", "模糊", 0, false},
	}
	for _, c := range cases {
		n, ok := parseTrailingInt(c.text, c.marker)
		if ok != c.ok || (ok && n != c.want) {
			t.Errorf("parseTrailingInt(%q,%q)=(%d,%v) want (%d,%v)", c.text, c.marker, n, ok, c.want, c.ok)
		}
	}
}

func TestFormatBGSettings(t *testing.T) {
	// 默认（空设置）：横屏/模糊1/透明约29%
	out := formatBGSettings(map[string]any{})
	if !strings.Contains(out, "方向: 横屏") {
		t.Errorf("默认应横屏: %q", out)
	}
	// 竖屏 + alpha=255 → 透明 0%
	out = formatBGSettings(map[string]any{"vertical": true, "alpha": float64(255), "blur": float64(3)})
	if !strings.Contains(out, "方向: 竖屏") || !strings.Contains(out, "透明度: 0%") || !strings.Contains(out, "模糊度: 3") {
		t.Errorf("竖屏设置格式错误: %q", out)
	}
}

func TestBGSettingsRoundtrip(t *testing.T) {
	dir := t.TempDir()
	m := &ProfileModule{staticDir: dir}

	// 初始为空
	s := m.loadBGSettings()
	if len(s) != 0 {
		t.Fatalf("初始应为空: %v", s)
	}

	s["jp:12345"] = map[string]any{"vertical": true, "blur": 5, "alpha": 200}
	if err := m.saveBGSettings(s); err != nil {
		t.Fatalf("saveBGSettings: %v", err)
	}
	got := m.loadBGSettings()
	entry, ok := got["jp:12345"]
	if !ok {
		t.Fatalf("回读缺少键: %v", got)
	}
	// JSON 数字回读为 float64
	if !bgBool(entry, "vertical") || bgInt(entry, "blur", 0) != 5 || bgInt(entry, "alpha", 0) != 200 {
		t.Errorf("回读不一致: %v", entry)
	}
}

func TestBGImagePath(t *testing.T) {
	m := &ProfileModule{staticDir: "/data/static"}
	got := m.profileBGImagePath("999", "cn")
	want := "/data/static/profile_bg/cn/999.jpg"
	if got != want {
		t.Errorf("profileBGImagePath=%q want %q", got, want)
	}
}

func TestSaveProfileBGResizesAndInitializesSettings(t *testing.T) {
	root := t.TempDir()
	m := &ProfileModule{staticDir: root}
	img := image.NewRGBA(image.Rect(0, 0, 4000, 2000))
	for y := 0; y < 2000; y++ {
		for x := 0; x < 4000; x++ {
			img.Set(x, y, color.RGBA{R: 255, A: 255})
		}
	}
	if err := m.saveProfileBG("123", "jp", img); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, "profile_bg", "jp", "123.jpg")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	decoded, format, err := image.Decode(strings.NewReader(string(data)))
	if err != nil || format != "jpeg" {
		t.Fatalf("format=%q err=%v", format, err)
	}
	if decoded.Bounds().Dx() != 3000 || decoded.Bounds().Dy() != 1500 {
		t.Fatalf("size=%v", decoded.Bounds())
	}
	settings := m.loadBGSettings()["jp:123"]
	if bgBool(settings, "vertical") || bgInt(settings, "blur", 0) != 1 || bgInt(settings, "alpha", 0) != 180 {
		t.Fatalf("default settings=%v", settings)
	}
}
