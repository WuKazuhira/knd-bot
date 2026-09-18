package assets

import (
	"os"
	"path/filepath"
	"testing"
)

func TestStoreDownloadedReusesMatchingJPForCNAndTW(t *testing.T) {
	cases := []struct {
		name   string
		region string
		path   string
		raw    string
	}{
		{name: "cn jacket", region: "cn", path: "startapp/music/jacket", raw: "100.png"},
		{name: "tw chart", region: "tw", path: "charts", raw: "100/master.txt"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			root := t.TempDir()
			d := &Downloader{outDir: root}
			data := []byte("same asset")
			relative := filepath.Join(tc.path, tc.raw)
			source := filepath.Join(root, "jp", relative)
			target := filepath.Join(root, tc.region, relative)
			if err := os.MkdirAll(filepath.Dir(source), 0o755); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(source, data, 0o644); err != nil {
				t.Fatal(err)
			}

			if err := d.storeDownloaded(tc.region, tc.path, tc.raw, target, data); err != nil {
				t.Fatal(err)
			}
			sourceInfo, err := os.Stat(source)
			if err != nil {
				t.Fatal(err)
			}
			targetInfo, err := os.Stat(target)
			if err != nil {
				t.Fatal(err)
			}
			if !os.SameFile(sourceInfo, targetInfo) {
				t.Fatal("matching CN/TW asset should reuse the JP inode")
			}
		})
	}
}

func TestStoreDownloadedFallsBackToRegularFile(t *testing.T) {
	cases := []struct {
		name   string
		region string
		path   string
		raw    string
		jpData []byte
		data   []byte
	}{
		{name: "non candidate", region: "cn", path: "event/1", raw: "bg.png", jpData: []byte("same"), data: []byte("same")},
		{name: "different content", region: "tw", path: "startapp/music/jacket", raw: "101.png", jpData: []byte("jp"), data: []byte("region")},
		{name: "missing JP", region: "cn", path: "startapp/music/jacket", raw: "102.png", data: []byte("region")},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			root := t.TempDir()
			d := &Downloader{outDir: root}
			relative := filepath.Join(tc.path, tc.raw)
			source := filepath.Join(root, "jp", relative)
			target := filepath.Join(root, tc.region, relative)
			if tc.jpData != nil {
				if err := os.MkdirAll(filepath.Dir(source), 0o755); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(source, tc.jpData, 0o644); err != nil {
					t.Fatal(err)
				}
			}

			if err := d.storeDownloaded(tc.region, tc.path, tc.raw, target, tc.data); err != nil {
				t.Fatal(err)
			}
			sourceInfo, sourceErr := os.Stat(source)
			targetInfo, targetErr := os.Stat(target)
			if targetErr != nil {
				t.Fatal(targetErr)
			}
			if sourceErr == nil && os.SameFile(sourceInfo, targetInfo) {
				t.Fatal("non-matching asset should not reuse the JP inode")
			}
			stored, err := os.ReadFile(target)
			if err != nil {
				t.Fatal(err)
			}
			if string(stored) != string(tc.data) {
				t.Fatalf("stored=%q want=%q", stored, tc.data)
			}
		})
	}
}

func TestIsDedupCandidate(t *testing.T) {
	if !isDedupCandidate("startapp/thumbnail/chara/card.png") {
		t.Fatal("thumbnail path should be a dedup candidate")
	}
	if isDedupCandidate("event/1/bg.png") {
		t.Fatal("event path should not be a dedup candidate")
	}
}
