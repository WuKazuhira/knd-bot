package serverconfig

import (
	"os"
	"path/filepath"
	"testing"
)

// writeServersYAML 在 configDir/pjsk/servers.yaml 写入测试配置。
func writeServersYAML(t *testing.T, configDir, content string) {
	t.Helper()
	dir := filepath.Join(configDir, "pjsk")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "servers.yaml"), []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

const testYAML = `jp:
  api:
    suite_api_url: https://suite/jp/{uid}
    profile_api_url: https://profile/jp/{uid}
    mysekai_api_url: https://mysekai/jp/{uid}
    ranking_border_api_url: https://border/jp/{event_id}
    ranking_top100_api_url: https://top100/jp/{event_id}
  rip:
    sources:
    - name: haruki
      base_url: https://assets/jp-assets/
    - name: sekai.best
      base_url: https://best/jp-assets/
cn:
  api:
    suite_api_url: https://suite/cn/{uid}
    mysekai_api_url: https://mysekai/cn/{uid}
    mysekai_upload_time_api_url: https://upload/cn
    mysekai_photo_api_url: https://photo/cn
tw:
  api:
    suite_api_url: https://suite/tw/{uid}
`

func TestLoadAndURLs(t *testing.T) {
	dir := t.TempDir()
	writeServersYAML(t, dir, testYAML)
	c, err := Load(dir)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}

	// uid 填充（serverType 0=jp）
	if got := c.SuiteURL(0, "12345"); got != "https://suite/jp/12345" {
		t.Errorf("SuiteURL jp=%q", got)
	}
	if got := c.ProfileURL(0, "999"); got != "https://profile/jp/999" {
		t.Errorf("ProfileURL jp=%q", got)
	}
	if got := c.MysekaiURL(2, "abc"); got != "https://mysekai/cn/abc" {
		t.Errorf("MysekaiURL cn=%q", got)
	}
	// event_id 填充
	if got := c.RankingBorderURL(0, 150); got != "https://border/jp/150" {
		t.Errorf("RankingBorderURL=%q", got)
	}
	if got := c.RankingTop100URL(0, 150); got != "https://top100/jp/150" {
		t.Errorf("RankingTop100URL=%q", got)
	}
}

func TestUploadTimeAndPhotoURL(t *testing.T) {
	dir := t.TempDir()
	writeServersYAML(t, dir, testYAML)
	c, _ := Load(dir)

	// cn 服配置了 upload_time / photo
	if got := c.MysekaiUploadTimeURL(2); got != "https://upload/cn" {
		t.Errorf("cn upload_time=%q", got)
	}
	if got := c.MysekaiPhotoURL(2); got != "https://photo/cn" {
		t.Errorf("cn photo=%q", got)
	}
	// jp 服未配置 upload_time → 空串
	if got := c.MysekaiUploadTimeURL(0); got != "" {
		t.Errorf("jp upload_time 应为空, got %q", got)
	}
	// tw 服未配置 mysekai → 空串
	if got := c.MysekaiURL(1, "x"); got != "" {
		t.Errorf("tw mysekai 应为空, got %q", got)
	}
}

func TestMissingKeyReturnsEmpty(t *testing.T) {
	dir := t.TempDir()
	writeServersYAML(t, dir, testYAML)
	c, _ := Load(dir)
	// cn 未配置 profile_api_url
	if got := c.ProfileURL(2, "1"); got != "" {
		t.Errorf("cn profile 应为空, got %q", got)
	}
}

func TestNilConfigSafe(t *testing.T) {
	var c *Config
	if got := c.SuiteURL(0, "1"); got != "" {
		t.Errorf("nil Config 应返回空串, got %q", got)
	}
}

func TestLoadMissingFile(t *testing.T) {
	if _, err := Load(t.TempDir()); err == nil {
		t.Error("缺失 servers.yaml 应返回错误")
	}
}

func TestServerNameMapping(t *testing.T) {
	cases := map[int]string{0: "jp", 1: "tw", 2: "cn", 99: "jp"}
	for st, want := range cases {
		if got := serverName(st); got != want {
			t.Errorf("serverName(%d)=%q want %q", st, got, want)
		}
	}
}
