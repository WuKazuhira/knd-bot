package pjsk

import (
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/skranking"
)

func TestUniqueCfRankingsKeepsFirstRankForEachUID(t *testing.T) {
	rows := uniqueCfRankings([]skranking.Ranking{
		{UID: "player", Rank: 96},
		{UID: "other", Rank: 97},
		{UID: "player", Rank: 98},
	})
	if len(rows) != 2 || rows[0].UID != "player" || rows[0].Rank != 96 || rows[1].UID != "other" {
		t.Fatalf("unique rankings = %+v, want T96 player and T97 other", rows)
	}
}
