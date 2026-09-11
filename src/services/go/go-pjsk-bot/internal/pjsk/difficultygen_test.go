package pjsk

import (
	"encoding/csv"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
)

func TestDifficultyGeneratorCSVJSONRoundtrip(t *testing.T) {
	root := t.TempDir()
	serverRoot := filepath.Join(root, "ondemand", "jp")
	writeJSON(t, filepath.Join(serverRoot, "musics.json"), []map[string]any{
		{"id": 1, "title": "First", "publishedAt": 100},
		{"id": 2, "title": "Second", "publishedAt": 200},
	})
	writeJSON(t, filepath.Join(serverRoot, "musicDifficulties.json"), difficultyRows(1, 20, 21, 22, 23, 24, 2, 25, 26, 27, 28, 29))
	writeJSON(t, filepath.Join(serverRoot, "realtime", "musicDifficulties.json"), customDifficultyRows(1, 2))

	generator := NewDifficultyGenerator(masterdata.New(root), root)
	count, err := generator.GenerateCSV(0)
	if err != nil {
		t.Fatal(err)
	}
	if count != 2 {
		t.Fatalf("CSV count=%d want 2", count)
	}
	csvPath := filepath.Join(serverRoot, "realtime", "musics.csv")
	file, err := os.Open(csvPath)
	if err != nil {
		t.Fatal(err)
	}
	reader := csv.NewReader(file)
	rows := make([][]string, 0, 3)
	for {
		row, readErr := reader.Read()
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			t.Fatal(readErr)
		}
		rows = append(rows, row)
	}
	_ = file.Close()
	if len(rows) != 3 || rows[1][0] != "Second" || rows[2][0] != "First" {
		t.Fatalf("CSV rows=%v", rows)
	}
	if rows[1][4] != "29.5" || rows[1][5] != "30.5" {
		t.Fatalf("CSV custom values=%v", rows[1])
	}

	updated, err := generator.GenerateJSON(0)
	if err != nil {
		t.Fatal(err)
	}
	if updated != 4 {
		t.Fatalf("JSON updated=%d want 4", updated)
	}
	data, err := os.ReadFile(filepath.Join(serverRoot, "realtime", "musicDifficulties.json"))
	if err != nil {
		t.Fatal(err)
	}
	var result []map[string]any
	if err := json.Unmarshal(data, &result); err != nil {
		t.Fatal(err)
	}
	if got := result[8]["fullComboAdjust"].(float64); got != 1.5 {
		t.Fatalf("expert FC adjust=%v", got)
	}
}

func difficultyRows(values ...any) []map[string]any {
	rows := make([]map[string]any, 0, len(values)/6)
	for i := 0; i < len(values); i += 6 {
		musicID := values[i].(int)
		rows = append(rows,
			map[string]any{"musicId": musicID, "musicDifficulty": "easy", "playLevel": values[i+1]},
			map[string]any{"musicId": musicID, "musicDifficulty": "normal", "playLevel": values[i+2]},
			map[string]any{"musicId": musicID, "musicDifficulty": "hard", "playLevel": values[i+3]},
			map[string]any{"musicId": musicID, "musicDifficulty": "expert", "playLevel": values[i+4]},
			map[string]any{"musicId": musicID, "musicDifficulty": "master", "playLevel": values[i+5]},
		)
	}
	return rows
}

func customDifficultyRows(ids ...int) []map[string]any {
	rows := make([]map[string]any, 0, len(ids)*5)
	for _, musicID := range ids {
		rows = append(rows,
			map[string]any{"musicId": musicID, "musicDifficulty": "easy"},
			map[string]any{"musicId": musicID, "musicDifficulty": "normal"},
			map[string]any{"musicId": musicID, "musicDifficulty": "hard"},
			map[string]any{"musicId": musicID, "musicDifficulty": "expert", "fullComboAdjust": 1.5, "fullPerfectAdjust": 2.5},
			map[string]any{"musicId": musicID, "musicDifficulty": "master", "fullComboAdjust": 2.5, "fullPerfectAdjust": 3.5},
		)
	}
	return rows
}
