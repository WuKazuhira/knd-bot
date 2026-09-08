package masterdata

import (
	"encoding/csv"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// ConstantKey 是定数表的键：(musicId, 难度)。
type ConstantKey struct {
	MusicID int
	Diff    string
}

// Constants 读取定数表 ondemand/jp/realtime/constants.csv（id,difficulty,constant），
// 对齐 old-python diffrank.load_constants。定数表固定放在日服目录、三服共用。
// 文件缺失或损坏返回空表（调用方用整数 level 兜底）。
func (l *Loader) Constants() map[ConstantKey]float64 {
	path := filepath.Join(l.root, "jp", "realtime", "constants.csv")
	f, err := os.Open(path)
	if err != nil {
		return map[ConstantKey]float64{}
	}
	defer f.Close()

	reader := csv.NewReader(f)
	rows, err := reader.ReadAll()
	if err != nil || len(rows) < 2 {
		return map[ConstantKey]float64{}
	}
	// 表头定位各列
	header := rows[0]
	idCol, diffCol, constCol := -1, -1, -1
	for i, h := range header {
		switch strings.TrimSpace(strings.ToLower(h)) {
		case "id":
			idCol = i
		case "difficulty":
			diffCol = i
		case "constant":
			constCol = i
		}
	}
	if idCol < 0 || diffCol < 0 || constCol < 0 {
		return map[ConstantKey]float64{}
	}

	out := make(map[ConstantKey]float64, len(rows))
	for _, row := range rows[1:] {
		if idCol >= len(row) || diffCol >= len(row) || constCol >= len(row) {
			continue
		}
		mid, err := strconv.Atoi(strings.TrimSpace(row[idCol]))
		if err != nil {
			continue
		}
		c, err := strconv.ParseFloat(strings.TrimSpace(row[constCol]), 64)
		if err != nil {
			continue
		}
		out[ConstantKey{MusicID: mid, Diff: strings.ToLower(strings.TrimSpace(row[diffCol]))}] = c
	}
	return out
}
