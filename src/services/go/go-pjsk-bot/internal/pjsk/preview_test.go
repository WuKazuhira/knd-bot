package pjsk

import "testing"

func TestParsePreviewArgs(t *testing.T) {
	cases := []struct {
		arg, diff, query string
	}{
		{"expert song", "expert", "song"},
		{"song ex", "expert", "song"},
		{"append song", "append", "song"},
		{"song", "master", "song"},
	}
	for _, tc := range cases {
		diff, query := parsePreviewArgs(tc.arg)
		if diff != tc.diff || query != tc.query {
			t.Errorf("parsePreviewArgs(%q)=(%q,%q), want (%q,%q)", tc.arg, diff, query, tc.diff, tc.query)
		}
	}
}
