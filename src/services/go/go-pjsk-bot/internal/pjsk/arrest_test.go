package pjsk

import (
	"strings"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/profile"
)

func newTestProfile() *profile.Profile {
	p := &profile.Profile{
		Name:        "TestPlayer",
		MasterScore: map[int]*profile.ScoreEntry{},
		ExpertScore: map[int]*profile.ScoreEntry{},
	}
	for i := 26; i <= 37; i++ {
		p.MasterScore[i] = &profile.ScoreEntry{}
	}
	// expert(3)/master(4) 清关数
	p.Clear[3], p.FullCombo[3], p.FullPerfect[3] = 100, 80, 60
	p.Clear[4], p.FullCombo[4], p.FullPerfect[4] = 50, 30, 20
	// Lv33: AP=5 FC=8 total=40
	p.MasterScore[33] = &profile.ScoreEntry{5, 8, 8, 40}
	// Lv32: AP=3 FC=6 total=30
	p.MasterScore[32] = &profile.ScoreEntry{3, 6, 6, 30}
	return p
}

func TestBuildProgressTextPublic(t *testing.T) {
	m := NewArrestModule(nil, nil, nil, nil, nil, nil)
	text := m.buildProgressText(newTestProfile(), "1234567890123", false)

	if !strings.Contains(text, "TestPlayer - 1234567890123") {
		t.Errorf("公开模式应含 name - uid, got:\n%s", text)
	}
	if !strings.Contains(text, "expert进度:FC 80/100 AP60/100") {
		t.Errorf("expert 进度错误, got:\n%s", text)
	}
	if !strings.Contains(text, "master进度:FC 30/50 AP20/50") {
		t.Errorf("master 进度错误, got:\n%s", text)
	}
	if !strings.Contains(text, "Lv.33及以上AP进度：5/40") {
		t.Errorf("Lv33 AP 错误, got:\n%s", text)
	}
	if !strings.Contains(text, "Lv.32AP进度：3/30") {
		t.Errorf("Lv32 AP 错误, got:\n%s", text)
	}
}

func TestBuildProgressTextPrivate(t *testing.T) {
	m := NewArrestModule(nil, nil, nil, nil, nil, nil)
	text := m.buildProgressText(newTestProfile(), "1234567890123", true)
	if strings.Contains(text, "1234567890123") {
		t.Errorf("隐私模式不应显示 uid, got:\n%s", text)
	}
	if !strings.HasPrefix(text, "TestPlayer\n") {
		t.Errorf("隐私模式应只显示 name, got:\n%s", text)
	}
}
