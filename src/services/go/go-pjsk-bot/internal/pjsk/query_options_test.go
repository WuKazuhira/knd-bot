package pjsk

import "testing"

func TestParseQueryOptions(t *testing.T) {
	got := parseQueryOptions("-refresh 2024 leak --refresh")
	if !got.Refresh || got.Arg != "2024 leak" {
		t.Fatalf("options=%+v", got)
	}
	got = parseQueryOptions("-1")
	if got.Refresh || got.Arg != "-1" {
		t.Fatalf("negative id was misparsed: %+v", got)
	}
}

func TestOrderedMasterItem(t *testing.T) {
	items := []map[string]any{
		{"id": float64(10), "releaseAt": float64(300)},
		{"id": float64(20), "releaseAt": float64(100)},
		{"id": float64(30), "releaseAt": float64(200)},
		{"id": float64(31), "releaseAt": float64(200)},
	}
	if item, ok := orderedMasterItem(items, -1, "releaseAt"); !ok || intField(item, "id") != 10 {
		t.Fatalf("-1 = %#v, ok=%v", item, ok)
	}
	if item, ok := orderedMasterItem(items, -2, "releaseAt"); !ok || intField(item, "id") != 31 {
		t.Fatalf("-2 = %#v, ok=%v", item, ok)
	}
	if item, ok := orderedMasterItem(items, 20, "releaseAt"); !ok || intField(item, "id") != 20 {
		t.Fatalf("real id = %#v, ok=%v", item, ok)
	}
	if _, ok := orderedMasterItem(items, -5, "releaseAt"); ok {
		t.Fatal("out-of-range negative id should not resolve")
	}
}
