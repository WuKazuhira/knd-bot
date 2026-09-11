package forecast

import "testing"

func TestEstimateSlopeUsesRecentPoints(t *testing.T) {
	points := []point{{score: 100, ts: 0}, {score: 200, ts: 100}, {score: 500, ts: 200}}
	if got := estimateSlope(points); got != 2.0 {
		t.Fatalf("slope=%v", got)
	}
}

func TestFilterPointsDeduplicatesAndBounds(t *testing.T) {
	points := filterPoints([]point{{1, 10}, {2, 11}, {3, 11}, {4, 20}}, 10, 11)
	if len(points) != 2 || points[0].ts != 10 || points[1].score != 2 {
		t.Fatalf("points=%+v", points)
	}
}
