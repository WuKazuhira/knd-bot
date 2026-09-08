package pjsk

import "testing"

func TestParseUnitArgRest(t *testing.T) {
	cases := []struct {
		in       string
		wantUnit string
		wantRest string
	}{
		{"miku ln", "light_sound", "miku"},
		{"ln miku", "light_sound", "miku"},
		{"miku", "", "miku"},
		{"", "", ""},
		{"25 ena", "school_refusal", "ena"},
		// 只吃第一个命中的团名，其余保留
		{"ln mmj miku", "light_sound", "mmj miku"},
	}
	for _, c := range cases {
		unit, rest := parseUnitArgRest(c.in)
		if unit != c.wantUnit || rest != c.wantRest {
			t.Errorf("parseUnitArgRest(%q)=(%q,%q) want (%q,%q)", c.in, unit, rest, c.wantUnit, c.wantRest)
		}
	}
}

func TestStripKeywords(t *testing.T) {
	rest, hit := stripKeywords("miku all id", []string{"all", "id"})
	if rest != "miku" {
		t.Errorf("rest=%q want miku", rest)
	}
	if !hit["all"] || !hit["id"] {
		t.Errorf("hit=%v want all+id", hit)
	}

	rest2, hit2 := stripKeywords("miku ln", []string{"all", "id"})
	if rest2 != "miku ln" {
		t.Errorf("rest2=%q want 'miku ln'", rest2)
	}
	if len(hit2) != 0 {
		t.Errorf("hit2=%v want empty", hit2)
	}
}

func TestParseIntToken(t *testing.T) {
	cases := []struct {
		in     string
		want   int
		wantOK bool
	}{
		{"123", 123, true},
		{"-5", -5, true},
		{"0", 0, true},
		{"", 0, false},
		{"-", 0, false},
		{"12a", 0, false},
		{"1.5", 0, false},
	}
	for _, c := range cases {
		n, ok := parseIntToken(c.in)
		if ok != c.wantOK || (ok && n != c.want) {
			t.Errorf("parseIntToken(%q)=(%d,%v) want (%d,%v)", c.in, n, ok, c.want, c.wantOK)
		}
	}
}

func TestParseUnitArg(t *testing.T) {
	if got := parseUnitArg("foo vbs bar"); got != "street" {
		t.Errorf("parseUnitArg=%q want street", got)
	}
	if got := parseUnitArg("nothing here"); got != "" {
		t.Errorf("parseUnitArg=%q want empty", got)
	}
}
