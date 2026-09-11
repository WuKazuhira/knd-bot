package pjsk

import (
	"encoding/csv"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
)

// DifficultyGenerator 对齐 Python diffrank.data_source 的 CSV/JSON 互转工具。
type DifficultyGenerator struct {
	md      *masterdata.Loader
	dataDir string
}

func NewDifficultyGenerator(md *masterdata.Loader, dataDir string) *DifficultyGenerator {
	return &DifficultyGenerator{md: md, dataDir: dataDir}
}

func (g *DifficultyGenerator) serverRoot(server int) string {
	return filepath.Join(g.dataDir, "ondemand", serverCode(server))
}

func (g *DifficultyGenerator) GenerateCSV(server int) (int, error) {
	musics, err := g.md.Load("musics.json", server)
	if err != nil {
		return 0, err
	}
	raw, err := g.md.Load("musicDifficulties.json", server)
	if err != nil {
		return 0, err
	}
	custom, err := g.md.Load("realtime/musicDifficulties.json", server)
	if err != nil {
		return 0, err
	}

	type row struct {
		values     []string
		published  string
		publishedN float64
		order      int
	}
	rows := make([]row, 0, len(musics))
	for order, music := range musics {
		musicID := intField(music, "id")
		rawIndex := firstDifficultyIndex(raw, musicID)
		if rawIndex < 0 || rawIndex+4 >= len(raw) {
			continue
		}
		rawExpert := numberText(raw[rawIndex+3], "playLevel")
		rawMaster := numberText(raw[rawIndex+4], "playLevel")
		if rawExpert == "" || rawMaster == "" {
			continue
		}
		values := []string{strOrNumber(music, "title"), strconv.Itoa(musicID), strOrNumber(music, "publishedAt"), rawExpert}
		values = append(values, customAdjust(custom, musicID, 3, rawExpert)...)
		values = append(values, rawMaster)
		values = append(values, customAdjust(custom, musicID, 4, rawMaster)...)
		published := strOrNumber(music, "publishedAt")
		publishedN, _ := strconv.ParseFloat(published, 64)
		rows = append(rows, row{values: values, published: published, publishedN: publishedN, order: order})
	}
	sort.SliceStable(rows, func(i, j int) bool {
		if rows[i].publishedN != rows[j].publishedN {
			return rows[i].publishedN > rows[j].publishedN
		}
		if rows[i].published != rows[j].published {
			return rows[i].published > rows[j].published
		}
		return rows[i].order < rows[j].order
	})

	path := filepath.Join(g.serverRoot(server), "realtime", "musics.csv")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return 0, err
	}
	var out strings.Builder
	out.WriteString("\ufeff")
	writer := csv.NewWriter(&out)
	if err := writer.Write([]string{"曲名", "id", "time", "EXPERT", "FC定数", "AP定数", "MASTER", "FC定数", "AP定数"}); err != nil {
		return 0, err
	}
	for _, item := range rows {
		if err := writer.Write(item.values); err != nil {
			return 0, err
		}
	}
	writer.Flush()
	if err := writer.Error(); err != nil {
		return 0, err
	}
	if err := writeAtomic(path, []byte(out.String())); err != nil {
		return 0, err
	}
	return len(rows), nil
}

func (g *DifficultyGenerator) GenerateJSON(server int) (int, error) {
	diffData, err := g.md.Load("musicDifficulties.json", server)
	if err != nil {
		return 0, err
	}
	copyData := make([]map[string]any, len(diffData))
	for i, item := range diffData {
		copyData[i] = make(map[string]any, len(item))
		for key, value := range item {
			copyData[i][key] = value
		}
	}
	csvPath := filepath.Join(g.serverRoot(server), "realtime", "musics.csv")
	file, err := os.Open(csvPath)
	if err != nil {
		return 0, err
	}
	defer file.Close()
	reader := csv.NewReader(file)
	updated := 0
	for {
		line, readErr := reader.Read()
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			return updated, readErr
		}
		if len(line) < 9 || line[0] == "曲名" {
			continue
		}
		musicID, err := strconv.Atoi(strings.TrimSpace(line[1]))
		if err != nil {
			continue
		}
		index := firstDifficultyIndex(copyData, musicID)
		if index < 0 || index+4 >= len(copyData) {
			continue
		}
		if applyDifficultyRow(copyData[index+3], line[3], line[4], line[5]) {
			updated++
		}
		if applyDifficultyRow(copyData[index+4], line[6], line[7], line[8]) {
			updated++
		}
	}
	data, err := json.MarshalIndent(copyData, "", "    ")
	if err != nil {
		return updated, err
	}
	data = append(data, '\n')
	path := filepath.Join(g.serverRoot(server), "realtime", "musicDifficulties.json")
	if err := writeAtomic(path, data); err != nil {
		return updated, err
	}
	return updated, nil
}

func firstDifficultyIndex(items []map[string]any, musicID int) int {
	for i, item := range items {
		if intField(item, "musicId") == musicID {
			return i
		}
	}
	return -1
}

func customAdjust(items []map[string]any, musicID, offset int, rawLevel string) []string {
	index := firstDifficultyIndex(items, musicID)
	if index < 0 || index+offset >= len(items) {
		return []string{"", ""}
	}
	item := items[index+offset]
	fc, fcOK := numberField(item, "fullComboAdjust")
	ap, apOK := numberField(item, "fullPerfectAdjust")
	base, err := strconv.ParseFloat(rawLevel, 64)
	if err != nil {
		return []string{"", ""}
	}
	fcText, apText := "", ""
	if fcOK {
		fcText = formatNumber(fc + base)
	}
	if apOK {
		apText = formatNumber(ap + base)
	}
	return []string{fcText, apText}
}

func applyDifficultyRow(item map[string]any, rawLevel, fcText, apText string) bool {
	base, err := strconv.Atoi(strings.TrimSpace(rawLevel))
	if err != nil {
		return false
	}
	fc, err := strconv.ParseFloat(strings.TrimSpace(fcText), 64)
	if err != nil {
		return false
	}
	ap, err := strconv.ParseFloat(strings.TrimSpace(apText), 64)
	if err != nil {
		return false
	}
	item["fullComboAdjust"] = fc - float64(base)
	item["fullPerfectAdjust"] = ap - float64(base)
	item["playLevelAdjust"] = (fc-float64(base))*2/3 + (ap-float64(base))*1/3
	return true
}

func numberField(item map[string]any, key string) (float64, bool) {
	value, ok := item[key]
	if !ok || value == nil {
		return 0, false
	}
	switch n := value.(type) {
	case float64:
		return n, true
	case float32:
		return float64(n), true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	case json.Number:
		f, err := n.Float64()
		return f, err == nil
	case string:
		f, err := strconv.ParseFloat(strings.TrimSpace(n), 64)
		return f, err == nil
	default:
		return 0, false
	}
}

func numberText(item map[string]any, key string) string {
	value, ok := numberField(item, key)
	if !ok {
		return ""
	}
	return formatNumber(value)
}

func formatNumber(value float64) string {
	return strconv.FormatFloat(value, 'f', -1, 64)
}

func strOrNumber(item map[string]any, key string) string {
	if value, ok := item[key].(string); ok {
		return value
	}
	if value, ok := numberField(item, key); ok {
		return formatNumber(value)
	}
	return fmt.Sprint(item[key])
}
