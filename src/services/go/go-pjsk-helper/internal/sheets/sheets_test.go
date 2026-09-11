package sheets

import (
	"strings"
	"testing"
)

func TestParsePrimaryAndEncode(t *testing.T) {
	raw := []byte("name,x,value,x,x,difficulty,id\nSong,x,31.5,x,x,master,12\nBad,x,no,x,x,master,13\n")
	rows := parsePrimary(raw)
	if len(rows) != 1 || rows[0].ID != 12 || rows[0].Value != 31.5 {
		t.Fatalf("rows=%+v", rows)
	}
	encoded := string(encodeRows(rows))
	if !strings.Contains(encoded, "12,master,31.5") {
		t.Fatalf("encoded=%q", encoded)
	}
}

func TestFirstNumber(t *testing.T) {
	if got := firstNumber("37.9(↑) 37.8(↑)"); got != 37.9 {
		t.Fatalf("got %v", got)
	}
}
