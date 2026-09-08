package profile

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
)

func TestComputeScores(t *testing.T) {
	dir := t.TempDir()
	mdDir := filepath.Join(dir, "ondemand", "jp")
	if err := os.MkdirAll(mdDir, 0o755); err != nil {
		t.Fatal(err)
	}
	// 两首 master 谱面：music 1 lv33, music 2 lv33；一首 expert：music 1 lv25
	diffs := `[
		{"musicId":1,"musicDifficulty":"master","playLevel":33},
		{"musicId":2,"musicDifficulty":"master","playLevel":33},
		{"musicId":1,"musicDifficulty":"expert","playLevel":25}
	]`
	if err := os.WriteFile(filepath.Join(mdDir, "musicDifficulties.json"), []byte(diffs), 0o644); err != nil {
		t.Fatal(err)
	}

	f := &Fetcher{md: masterdata.New(dir)}
	p := &Profile{MasterScore: map[int]*ScoreEntry{}, ExpertScore: map[int]*ScoreEntry{}}
	for i := 26; i <= 37; i++ {
		p.MasterScore[i] = &ScoreEntry{}
	}
	for i := 21; i <= 31; i++ {
		p.ExpertScore[i] = &ScoreEntry{}
	}

	data := map[string]any{
		"userMusicResults": []any{
			// music 1 master: full_perfect -> AP
			map[string]any{"musicId": float64(1), "musicDifficulty": "master", "playResult": "full_perfect"},
			// music 2 master: full_combo -> FC (非 AP)
			map[string]any{"musicId": float64(2), "musicDifficulty": "master", "playResult": "full_combo"},
			// music 1 expert: clear
			map[string]any{"musicId": float64(1), "musicDifficulty": "expert", "playResult": "clear"},
		},
	}
	if err := f.computeScores(data, map[string]any{}, 0, p); err != nil {
		t.Fatal(err)
	}

	// lv33 total = 2
	if p.MasterScore[33][3] != 2 {
		t.Errorf("lv33 total = %d, want 2", p.MasterScore[33][3])
	}
	// lv33 AP=1(music1), FC=2(music1+2), clear=2
	if p.MasterScore[33][0] != 1 {
		t.Errorf("lv33 AP = %d, want 1", p.MasterScore[33][0])
	}
	if p.MasterScore[33][1] != 2 {
		t.Errorf("lv33 FC = %d, want 2", p.MasterScore[33][1])
	}
	if p.MasterScore[33][2] != 2 {
		t.Errorf("lv33 clear = %d, want 2", p.MasterScore[33][2])
	}
	// expert lv25: total=1, clear=1, AP=0
	if p.ExpertScore[25][3] != 1 || p.ExpertScore[25][2] != 1 || p.ExpertScore[25][0] != 0 {
		t.Errorf("expert lv25 = %v, want total1 clear1 ap0", *p.ExpertScore[25])
	}
}

func TestResultRank(t *testing.T) {
	cases := map[string]int{
		"full_perfect": 3, "allperfect": 3,
		"full_combo": 2, "fullcombo": 2,
		"clear": 1, "live_clear": 1,
		"": 0, "unknown": 0,
	}
	for in, want := range cases {
		if got := resultRank(in); got != want {
			t.Errorf("resultRank(%q) = %d, want %d", in, got, want)
		}
	}
}
