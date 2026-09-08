package pjsk

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
)

func TestNormalizeSongQuery(t *testing.T) {
	cases := map[string]string{
		"Hello World!":    "helloworld",
		"Tell Your World": "tellyourworld",
		"７４":              "74", // 全角数字 NFKC -> 半角
		"ミク":              "ミク", // 日文保留
		"  a b c  ":       "abc",
		"!!!":             "", // 纯符号
	}
	for in, want := range cases {
		if got := normalizeSongQuery(in); got != want {
			t.Errorf("normalizeSongQuery(%q) = %q, want %q", in, got, want)
		}
	}
}

func newSongModule(t *testing.T) (*SongModule, string) {
	t.Helper()
	dir := t.TempDir()
	mdDir := filepath.Join(dir, "ondemand", "jp")
	if err := os.MkdirAll(mdDir, 0o755); err != nil {
		t.Fatal(err)
	}
	musics := `[
		{"id":74,"title":"Tell Your World","publishedAt":1000},
		{"id":100,"title":"ミラクルペイント","publishedAt":9999999999999}
	]`
	if err := os.WriteFile(filepath.Join(mdDir, "musics.json"), []byte(musics), 0o644); err != nil {
		t.Fatal(err)
	}
	return NewSongModule(masterdata.New(dir), nil, nil, dir), dir
}

func TestFindSong(t *testing.T) {
	m, _ := newSongModule(t)
	ctx := context.Background()

	// 按 id
	if res := m.findSong(ctx, "74", 0); !res.found || res.musicID != 74 {
		t.Errorf("按 id 查找失败: %+v", res)
	}
	// 按标题（归一化后匹配，大小写/空格无关）
	if res := m.findSong(ctx, "tell your world", 0); !res.found || res.musicID != 74 {
		t.Errorf("按标题查找失败: %+v", res)
	}
	// 未找到
	if res := m.findSong(ctx, "不存在的歌", 0); res.found {
		t.Errorf("不应找到: %+v", res)
	}
}

func TestIsLeak(t *testing.T) {
	m, _ := newSongModule(t)
	// id=74 publishedAt=1000（早已公开）→ 非 leak
	if m.isLeak(74, 0) {
		t.Error("已公开歌曲不应为 leak")
	}
	// id=100 publishedAt 在遥远未来 → leak
	if !m.isLeak(100, 0) {
		t.Error("未公开歌曲应为 leak")
	}
}
