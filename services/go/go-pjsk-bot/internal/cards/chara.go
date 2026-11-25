package cards

import (
	"os"
	"path/filepath"
	"strconv"

	"gopkg.in/yaml.v3"
)

var builtinCharaAlias = map[string]int{
	"ick": 1, "saki": 2, "hnm": 3, "shiho": 4,
	"mnr": 5, "hrk": 6, "airi": 7, "szk": 8,
	"khn": 9, "an": 10, "akt": 11, "toya": 12,
	"tks": 13, "emu": 14, "nene": 15, "rui": 16,
	"knd": 17, "mfy": 18, "ena": 19, "mzk": 20,
	"miku": 21, "rin": 22, "len": 23, "luka": 24, "meiko": 25, "kaito": 26,
}

type CharaAliasResolver struct {
	aliasToID map[string]int
}

type charaNicknamesFile struct {
	Nicknames []struct {
		ID        int      `yaml:"id"`
		Nicknames []string `yaml:"nicknames"`
	} `yaml:"nicknames"`
}

func NewCharaAliasResolver(staticDir string) *CharaAliasResolver {
	m := map[string]int{}
	for k, v := range builtinCharaAlias {
		m[k] = v
	}
	path := filepath.Join(staticDir, "character_nicknames.yaml")
	if raw, err := os.ReadFile(path); err == nil {
		var parsed charaNicknamesFile
		if yaml.Unmarshal(raw, &parsed) == nil {
			for _, entry := range parsed.Nicknames {
				for _, nick := range entry.Nicknames {
					if nick != "" {
						m[nick] = entry.ID
					}
				}
			}
		}
	}
	return &CharaAliasResolver{aliasToID: m}
}

func (r *CharaAliasResolver) Resolve(alias string) int {
	if id, err := strconv.Atoi(alias); err == nil && id >= 1 && id <= 26 {
		return id
	}
	return r.aliasToID[alias]
}
