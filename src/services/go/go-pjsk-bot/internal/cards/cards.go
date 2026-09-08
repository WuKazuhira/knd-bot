// Package cards 提供卡面相关的团体/角色映射与卡面类型判定，
// 对齐 old-python _card_utils（cardtype / is_fes_card / UNIT 映射 / vs 角色）。
package cards

// UnitMainChars 团体内部名 -> 主要角色 characterId 列表，对齐 UNIT_MAIN_CHARS。
var UnitMainChars = map[string][]int{
	"light_sound":    {1, 2, 3, 4},
	"idol":           {5, 6, 7, 8},
	"street":         {9, 10, 11, 12},
	"theme_park":     {13, 14, 15, 16},
	"school_refusal": {17, 18, 19, 20},
	"piapro":         {21, 22, 23, 24, 25, 26},
}

// UnitKeyToInternal 筛选关键词 -> 团体内部名，对齐 UNIT_KEY_TO_INTERNAL。
var UnitKeyToInternal = map[string]string{
	"ln": "light_sound", "leo": "light_sound", "leoneed": "light_sound", "light_sound": "light_sound",
	"mmj": "idol", "moremorejump": "idol", "idol": "idol",
	"vbs": "street", "vivid": "street", "street": "street",
	"ws": "theme_park", "wonderlands": "theme_park", "theme_park": "theme_park",
	"25h": "school_refusal", "25ji": "school_refusal", "25": "school_refusal",
	"25时": "school_refusal", "nightcord": "school_refusal", "school_refusal": "school_refusal",
	"vs": "piapro", "virtual": "piapro", "piapro": "piapro", "v": "piapro",
}

func mdInt(m map[string]any, key string) int {
	switch n := m[key].(type) {
	case float64:
		return int(n)
	case int:
		return n
	case int64:
		return int(n)
	}
	return 0
}

func mdStr(m map[string]any, key string) string {
	if v, ok := m[key].(string); ok {
		return v
	}
	return ""
}

// CardType 判定卡面是否限定（1=限定 0=常驻），对齐 cardtype：
// 依据 cardCostume3ds 关联的 costume3d 是否为 hair 部件。
func CardType(cardID int, cardCostume3ds, costume3ds []map[string]any) int {
	hairCostumes := map[int]bool{}
	for _, item := range costume3ds {
		if mdStr(item, "partType") == "hair" {
			hairCostumes[mdInt(item, "id")] = true
		}
	}
	for _, item := range cardCostume3ds {
		if hairCostumes[mdInt(item, "costume3dId")] && mdInt(item, "cardId") == cardID {
			return 1
		}
	}
	return 0
}

// IsFes 判定卡面是否 fes 限定，对齐 is_fes_card。
// card 为卡面对象，cardSupplies 为 cardSupplies.json 数据。
func IsFes(card map[string]any, cardSupplies []map[string]any) bool {
	supplyID := mdInt(card, "cardSupplyId")
	if supplyID == 0 {
		return false
	}
	for _, supply := range cardSupplies {
		if mdInt(supply, "id") == supplyID {
			t := mdStr(supply, "cardSupplyType")
			return t == "colorful_festival_limited" || t == "bloom_festival_limited"
		}
	}
	return false
}

// UnitVsChars 返回属于指定团体的虚拟歌手 characterId（21-26），对齐 get_unit_vs_chars。
func UnitVsChars(unitInternal string, gameCharacterUnits []map[string]any) []int {
	if unitInternal == "piapro" {
		return nil
	}
	seen := map[int]bool{}
	var out []int
	for _, e := range gameCharacterUnits {
		cid := mdInt(e, "gameCharacterId")
		if cid < 21 || cid > 26 {
			continue
		}
		if mdStr(e, "unit") == unitInternal && !seen[cid] {
			out = append(out, cid)
			seen[cid] = true
		}
	}
	sortInts(out)
	return out
}

func sortInts(a []int) {
	for i := 1; i < len(a); i++ {
		for j := i; j > 0 && a[j-1] > a[j]; j-- {
			a[j-1], a[j] = a[j], a[j-1]
		}
	}
}
