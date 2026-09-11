package translation

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/kazuhira/go-pjsk-helper/internal/masterdata"
)

func TestMergeCreatesSharedYAMLAndPreservesExisting(t *testing.T) {
	dir := t.TempDir()
	s := NewSyncer(masterdata.Config{"jp": masterdata.RegionConfig{}}, dir)
	changed, err := s.merge("jp", "music_titles", []byte(`{"1":"臺灣與學樂","2":"新曲"}`))
	if err != nil || !changed {
		t.Fatalf("merge changed=%v err=%v", changed, err)
	}
	changed, err = s.merge("jp", "music_titles", []byte(`{"1":"不同內容","3":"追加"}`))
	if err != nil || !changed {
		t.Fatalf("second merge changed=%v err=%v", changed, err)
	}
	data, err := os.ReadFile(filepath.Join(dir, "jp", "translate.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(data)
	if !contains(text, "台湾与学乐") || !contains(text, "追加") || contains(text, "不同内容") {
		t.Fatalf("unexpected yaml: %s", text)
	}
}

func contains(text, value string) bool {
	for i := 0; i+len(value) <= len(text); i++ {
		if text[i:i+len(value)] == value {
			return true
		}
	}
	return false
}
