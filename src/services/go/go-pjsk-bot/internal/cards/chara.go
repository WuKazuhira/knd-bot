package cards

import (
	"os"
	"path/filepath"
	"strconv"

	"gopkg.in/yaml.v3"
)

// builtinCharaAlias 内置角色缩写 -> characterId，对齐 old-python findcard 的兜底 dic。
var builtinCharaAlias = map[string]int{
	"ick": 1, "saki": 2, "hnm": 3, "shiho": 4,
	"mnr": 5, "hrk": 6, "airi": 7, "szk": 8,
	"khn": 9, "an": 10, "akt": 11, "toya": 12,
	"tks": 13, "emu": 14, "nene": 15, "rui": 16,
	"knd": 17, "mfy": 18, "ena": 19, "mzk": 20,
	"miku": 21, "rin": 22, "len": 23, "luka": 24, "meiko": 25, "kaito": 26,
}

// CharaAliasResolver 把角色别名解析成 characterId。
type CharaAliasResolver struct {
	aliasToID map[string]int
}

type charaNicknamesFile struct {
	Nicknames []struct {
		ID        int      `yaml:"id"`
		Nicknames []string `yaml:"nicknames"`
	} `yaml:"nicknames"`
}

// NewCharaAliasResolver 从 static/character_nicknames.yaml 读取别名表，
// 叠加内置缩写兜底。文件缺失时仅用内置缩写。
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

// Resolve 返回别名对应的 characterId，未找到返回 0；纯数字 1~26 也视为角色 ID。
func (r *CharaAliasResolver) Resolve(alias string) int {
	if id, err := strconv.Atoi(alias); err == nil && id >= 1 && id <= 26 {
		return id
	}
	return r.aliasToID[alias]
}
