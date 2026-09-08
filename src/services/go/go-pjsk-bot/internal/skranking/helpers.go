package skranking

import "strconv"

func formatInt(v int64) string {
	return strconv.FormatInt(v, 10)
}

func sortInts(a []int) {
	for i := 1; i < len(a); i++ {
		for j := i; j > 0 && a[j-1] > a[j]; j-- {
			a[j-1], a[j] = a[j], a[j-1]
		}
	}
}
