package serverconfig

import "testing"

func TestAssetURLs(t *testing.T) {
	dir := t.TempDir()
	writeServersYAML(t, dir, testYAML)
	c, err := Load(dir)
	if err != nil {
		t.Fatal(err)
	}
	urls := c.AssetURLs(0, "startapp/character/member/foo", "card_normal.png")
	want := []string{
		"https://assets/jp-assets/startapp/character/member/foo/card_normal.png",
		"https://best/jp-assets/startapp/character/member/foo/card_normal.png",
	}
	if len(urls) != len(want) {
		t.Fatalf("urls=%v", urls)
	}
	for i := range want {
		if urls[i] != want[i] {
			t.Fatalf("urls[%d]=%q want %q", i, urls[i], want[i])
		}
	}
}
