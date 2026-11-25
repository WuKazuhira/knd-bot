package masterdata

import (
	"encoding/csv"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

type ConstantKey struct {
	MusicID int
	Diff    string
}

// 定数表放日服目录，三服共用
func (l *Loader) Constants() map[ConstantKey]float64 {
	path := filepath.Join(l.root, "jp", "realtime", "constants.csv")
	info, err := os.Stat(path)
	if err != nil {
		return map[ConstantKey]float64{}
	}
	stamp := fileStamp{exists: true, mtime: info.ModTime().UnixNano(), size: info.Size()}

	l.mu.RLock()
	if l.constants != nil && l.constantsStamp == stamp {
		cached := l.constants
		l.mu.RUnlock()
		return cached
	}
	l.mu.RUnlock()

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
	l.mu.Lock()
	l.constantsStamp = stamp
	l.constants = out
	l.mu.Unlock()
	return out
}
