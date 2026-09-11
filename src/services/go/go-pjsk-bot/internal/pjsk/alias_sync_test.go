package pjsk

import "testing"

func TestParseAliases(t *testing.T) {
	got, err := parseAliases([]byte(`{"aliases":["abc","  中文 ",""]}`))
	if err != nil || len(got) != 2 || got[1] != "中文" {
		t.Fatalf("got=%v err=%v", got, err)
	}
	got, err = parseAliases([]byte(`[{"alias":"one"},{"alias":""}]`))
	if err != nil || len(got) != 1 || got[0] != "one" {
		t.Fatalf("got=%v err=%v", got, err)
	}
}
