package pjsk

import (
	"reflect"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/cards"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

func TestParseWorldBloomFrameColorAndRoleMappings(t *testing.T) {
	aliases := cards.NewCharaAliasResolver("")
	for _, test := range []struct {
		color string
		frame int
	}{
		{"100", 20001}, {"tb", 20001}, {"1000", 20002}, {"tk", 20002},
	} {
		got, err := parseWorldBloomFrameArgs("wl3 "+test.color+" mnr", aliases)
		if err != nil {
			t.Fatalf("color %q: %v", test.color, err)
		}
		if got.FrameID != test.frame || !reflect.DeepEqual(got.CharacterIDs, []int{5, 5, 5, 5, 5, 5}) {
			t.Errorf("color %q parsed as %+v", test.color, got)
		}
	}
}

func TestParseWorldBloomFrameSixRolesCardAndTraining(t *testing.T) {
	aliases := cards.NewCharaAliasResolver("")
	got, err := parseWorldBloomFrameArgs("wl3 100 mnr hrk airi szk an emu 123 花后", aliases)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got.CharacterIDs, []int{5, 6, 7, 8, 10, 14}) {
		t.Errorf("roles = %v", got.CharacterIDs)
	}
	if got.CardID != 123 || got.Trained == nil || !*got.Trained {
		t.Errorf("card/training parsed as %+v", got)
	}
	before, err := parseWorldBloomFrameArgs("wl3 tb mnr 1 花前", aliases)
	if err != nil {
		t.Fatal(err)
	}
	if before.CardID != 1 || before.Trained == nil || *before.Trained {
		t.Errorf("before-training args parsed as %+v", before)
	}
}

func TestParseWorldBloomFrameRejectsInvalidArguments(t *testing.T) {
	aliases := cards.NewCharaAliasResolver("")
	for _, raw := range []string{
		"wl3 500 mnr",
		"wl2 100 mnr",
		"wl3 100 invalid",
		"wl3 100 mnr hrk",
		"wl3 100 mnr 0",
		"wl3 100 mnr 123 花前 花后",
	} {
		if _, err := parseWorldBloomFrameArgs(raw, aliases); err == nil {
			t.Errorf("parseWorldBloomFrameArgs(%q) unexpectedly succeeded", raw)
		}
	}
}

func TestParseWorldBloomFrameHelp(t *testing.T) {
	got, err := parseWorldBloomFrameArgs("help", cards.NewCharaAliasResolver(""))
	if err != nil || !got.Help {
		t.Fatalf("help args = %+v, error = %v", got, err)
	}
}

func TestWorldBloomFrameRegistersCommands(t *testing.T) {
	r := router.New([]string{""}, router.All())
	NewWorldBloomFrameModule(nil, nil, nil, nil, nil).Register(r)
	if got, want := r.TriggerCount(), 3; got != want {
		t.Fatalf("registered triggers = %d, want %d (JP/CN/TW commands only)", got, want)
	}
}

func TestWorldBloomHelpIsDelegatedToBuiltinHelp(t *testing.T) {
	for _, raw := range []string{"help", "帮助", "说明", "用法"} {
		if !isWorldBloomHelpRequest(raw) {
			t.Errorf("%q should be passed to the built-in help matcher", raw)
		}
	}
	if isWorldBloomHelpRequest("wl3 100 mnr") {
		t.Fatal("frame generation args must remain owned by Go")
	}
}
