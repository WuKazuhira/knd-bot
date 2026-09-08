package pjsk

import (
	"math"
	"testing"
)

func TestParseDiffArg(t *testing.T) {
	cases := []struct {
		arg       string
		wantDiff  string
		wantLevel float64
		wantExact bool
	}{
		{"26", "master", 26, false},
		{"27 ma", "master", 27, false},
		{"28ex", "expert", 28, false},
		{"26.5", "master", 26.5, true},
		{"ex 30", "expert", 30, false},
		{"", "master", 0, false},
	}
	for _, c := range cases {
		diff, level, exact := parseDiffArg(c.arg)
		if diff != c.wantDiff || level != c.wantLevel || exact != c.wantExact {
			t.Errorf("parseDiffArg(%q) = (%s,%v,%v), want (%s,%v,%v)",
				c.arg, diff, level, exact, c.wantDiff, c.wantLevel, c.wantExact)
		}
	}
}

func TestAdjustConstants(t *testing.T) {
	// playLevel=32, ap 定数=33.5
	fp, fc, pl, has := adjustConstants(32, 33.5, true)
	if !has {
		t.Fatal("有定数应 has=true")
	}
	// fp = 33.5-32 = 1.5
	if math.Abs(fp-1.5) > 1e-9 {
		t.Errorf("fullPerfectAdjust = %v, want 1.5", fp)
	}
	// fc = fcrank(32,33.5)-32 = (33.5-1.5)-32 = 0
	if math.Abs(fc-0) > 1e-9 {
		t.Errorf("fullComboAdjust = %v, want 0", fc)
	}
	// pl = fc*2/3 + fp*1/3 = 0 + 0.5 = 0.5
	if math.Abs(pl-0.5) > 1e-9 {
		t.Errorf("playLevelAdjust = %v, want 0.5", pl)
	}

	// 无定数
	if _, _, _, has := adjustConstants(30, 0, false); has {
		t.Error("无定数应 has=false")
	}
}

func TestLevelLabel(t *testing.T) {
	cases := map[float64]string{
		0:    "",
		26:   "26",
		26.5: "26.5",
	}
	for in, want := range cases {
		if got := levelLabel(in); got != want {
			t.Errorf("levelLabel(%v) = %q, want %q", in, got, want)
		}
	}
}

func TestRound1(t *testing.T) {
	if round1(26.54) != 26.5 {
		t.Errorf("round1(26.54) = %v, want 26.5", round1(26.54))
	}
	if round1(26.55) != 26.6 {
		t.Errorf("round1(26.55) = %v, want 26.6", round1(26.55))
	}
}
