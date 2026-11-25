package cards

var UnitMainChars = map[string][]int{
	"light_sound":    {1, 2, 3, 4},
	"idol":           {5, 6, 7, 8},
	"street":         {9, 10, 11, 12},
	"theme_park":     {13, 14, 15, 16},
	"school_refusal": {17, 18, 19, 20},
	"piapro":         {21, 22, 23, 24, 25, 26},
}

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

// costume3ds.json 比较大，建一次索引避免反复扫表
type CardIndex struct {
	limitedCardIDs map[int]struct{}
	fesSupplyIDs   map[int]struct{}
}

func NewCardIndex(cardCostume3ds, costume3ds, cardSupplies []map[string]any) *CardIndex {
	index := &CardIndex{
		limitedCardIDs: make(map[int]struct{}),
		fesSupplyIDs:   make(map[int]struct{}),
	}

	hairCostumeIDs := make(map[int]struct{}, len(costume3ds))
	for _, item := range costume3ds {
		if mdStr(item, "partType") == "hair" {
			if costumeID := mdInt(item, "id"); costumeID != 0 {
				hairCostumeIDs[costumeID] = struct{}{}
			}
		}
	}
	for _, item := range cardCostume3ds {
		if _, ok := hairCostumeIDs[mdInt(item, "costume3dId")]; !ok {
			continue
		}
		if cardID := mdInt(item, "cardId"); cardID != 0 {
			index.limitedCardIDs[cardID] = struct{}{}
		}
	}
	for _, supply := range cardSupplies {
		switch mdStr(supply, "cardSupplyType") {
		case "colorful_festival_limited", "bloom_festival_limited":
			if supplyID := mdInt(supply, "id"); supplyID != 0 {
				index.fesSupplyIDs[supplyID] = struct{}{}
			}
		}
	}
	return index
}

func (i *CardIndex) IsLimited(cardID int) bool {
	if i == nil {
		return false
	}
	_, ok := i.limitedCardIDs[cardID]
	return ok
}

func (i *CardIndex) IsFes(card map[string]any) bool {
	if i == nil {
		return false
	}
	supplyID := mdInt(card, "cardSupplyId")
	if supplyID == 0 {
		return false
	}
	_, ok := i.fesSupplyIDs[supplyID]
	return ok
}

func CardType(cardID int, cardCostume3ds, costume3ds []map[string]any) int {
	if NewCardIndex(cardCostume3ds, costume3ds, nil).IsLimited(cardID) {
		return 1
	}
	return 0
}

func IsFes(card map[string]any, cardSupplies []map[string]any) bool {
	return NewCardIndex(nil, nil, cardSupplies).IsFes(card)
}

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
