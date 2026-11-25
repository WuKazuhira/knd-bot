package skranking

import (
	"regexp"
	"strconv"
	"strings"
)

var RankLevels = []int{
	1, 2, 3, 4, 5, 10, 20, 30, 40, 50, 100, 200, 300, 400, 500,
	1000, 2000, 3000, 4000, 5000, 10000, 20000, 30000, 40000, 50000, 100000,
}

var rankRangeRe = regexp.MustCompile(`^(\d+)-(\d+)$`)
var rankListRe = regexp.MustCompile(`^\d+(?:\s+\d+)*$`)

func ParseRankArgs(arg string, defaultRanks []int, limit int) []int {
	raw := strings.TrimSpace(arg)
	if raw == "" {
		out := make([]int, len(defaultRanks))
		copy(out, defaultRanks)
		return out
	}
	if m := rankRangeRe.FindStringSubmatch(raw); m != nil {
		start, _ := strconv.Atoi(m[1])
		end, _ := strconv.Atoi(m[2])
		if start <= 0 || end < start || end-start+1 > limit {
			return nil
		}
		out := make([]int, 0, end-start+1)
		for r := start; r <= end; r++ {
			out = append(out, r)
		}
		return out
	}
	if rankListRe.MatchString(raw) {
		seen := map[int]bool{}
		var ranks []int
		for _, v := range strings.Fields(raw) {
			n, _ := strconv.Atoi(v)
			if n <= 0 {
				return nil
			}
			if !seen[n] {
				seen[n] = true
				ranks = append(ranks, n)
			}
		}
		if len(ranks) <= limit {
			return ranks
		}
	}
	return nil
}
