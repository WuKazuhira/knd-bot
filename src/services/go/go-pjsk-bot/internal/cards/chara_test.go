package cards

import "testing"

func TestCharaAliasResolverResolveNumericID(t *testing.T) {
	resolver := &CharaAliasResolver{aliasToID: map[string]int{"ena": 19}}
	for input, want := range map[string]int{"1": 1, "17": 17, "26": 26, "0": 0, "27": 0, "ena": 19} {
		if got := resolver.Resolve(input); got != want {
			t.Errorf("Resolve(%q) = %d, want %d", input, got, want)
		}
	}
}
