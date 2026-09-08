package pjsk

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
)

func TestCurrentRankMatch(t *testing.T) {
	dir := t.TempDir()
	mdDir := filepath.Join(dir, "ondemand", "jp")
	if err := os.MkdirAll(mdDir, 0o755); err != nil {
		t.Fatal(err)
	}
	seasons := `[
		{"id":1,"startAt":1000,"closedAt":2000},
		{"id":2,"startAt":2000,"closedAt":3000},
		{"id":3,"startAt":3000,"closedAt":4000}
	]`
	if err := os.WriteFile(filepath.Join(mdDir, "rankMatchSeasons.json"), []byte(seasons), 0o644); err != nil {
		t.Fatal(err)
	}
	md := masterdata.New(dir)

	// now 落在赛季 2 区间内
	if got := currentRankMatch(md, 0, 2500); got != 2 {
		t.Errorf("now=2500 应返回赛季 2, got %d", got)
	}
	// now 超出所有区间 → 返回最后一个
	if got := currentRankMatch(md, 0, 9999); got != 3 {
		t.Errorf("now=9999 应返回最后赛季 3, got %d", got)
	}
}

func TestFormatRankMatchMaster(t *testing.T) {
	// tierId=25 → grade=(24)/4+1=7 (Master)
	raw := `{"rankings":[{"rank":5,"userRankMatchSeason":{"rankMatchTierId":25,"tierPoint":3,"winCount":10,"loseCount":5,"drawCount":2,"penaltyCount":0,"maxConsecutiveWinCount":4}}],"updateTime":"2026-01-01"}`
	text, errMsg := formatRankMatch([]byte(raw))
	if errMsg != "" {
		t.Fatalf("不应有错误: %s", errMsg)
	}
	if !strings.Contains(text, "Master🎵×3") {
		t.Errorf("应含 Master 段位, got:\n%s", text)
	}
	if !strings.Contains(text, "排名：5") {
		t.Errorf("应含排名, got:\n%s", text)
	}
	// 胜率 = 10/15 = 66.67%
	if !strings.Contains(text, "66.67%") {
		t.Errorf("胜率计算错误, got:\n%s", text)
	}
}

func TestFormatRankMatchNonMaster(t *testing.T) {
	// tierId=5 → grade=(4)/4+1=2 (Bronze), kurasu=5-4=1
	raw := `{"rankings":[{"rank":100,"userRankMatchSeason":{"rankMatchTierId":5,"tierPoint":2,"winCount":3,"loseCount":1,"drawCount":0,"penaltyCount":1,"maxConsecutiveWinCount":2}}]}`
	text, errMsg := formatRankMatch([]byte(raw))
	if errMsg != "" {
		t.Fatalf("不应有错误: %s", errMsg)
	}
	if !strings.Contains(text, "BronzeClass 1(2/5)") {
		t.Errorf("应含 Bronze Class 1, got:\n%s", text)
	}
	// penaltyCount=1 → Lose 0+1
	if !strings.Contains(text, "Lose 0+1") {
		t.Errorf("penalty 应显示为 Lose 0+1, got:\n%s", text)
	}
}

func TestFormatRankMatchEmpty(t *testing.T) {
	_, errMsg := formatRankMatch([]byte(`{"rankings":[]}`))
	if errMsg != "未参加当期排位赛" {
		t.Errorf("空 rankings 应返回未参加, got %q", errMsg)
	}
}
