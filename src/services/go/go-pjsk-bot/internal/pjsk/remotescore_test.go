package pjsk

import "testing"

func TestPickNumber(t *testing.T) {
	if p := pickNumber("分数=1900000 生命=1000", "分数", "base_score"); p == nil || *p != 1900000 {
		t.Errorf("分数 pick=%v want 1900000", p)
	}
	if p := pickNumber("生命：500", "生命", "life"); p == nil || *p != 500 {
		t.Errorf("生命 pick=%v want 500", p)
	}
	if p := pickNumber("life 250", "life"); p == nil || *p != 250 {
		t.Errorf("life pick=%v want 250", p)
	}
	if p := pickNumber("nothing here", "分数"); p != nil {
		t.Errorf("无匹配应为 nil, got %v", p)
	}
}

func TestFormatScoreConfig(t *testing.T) {
	bs, life := 1900000, 1000
	cfg := scoreConfig{
		Auto:  scoreFlow{BaseScore: &bs, Life: &life},
		Clear: scoreFlow{},
	}
	out := formatScoreConfig(cfg)
	if want := "base_score=1900000 life=1000"; !contains(out, want) {
		t.Errorf("格式化缺少 auto 值: %q", out)
	}
	if !contains(out, "base_score=<nil>") {
		t.Errorf("clear 空值应显示 <nil>: %q", out)
	}
}

func TestScoreClearResetRegex(t *testing.T) {
	if !reScoreClear.MatchString("clear 分数=100") {
		t.Error("应识别 clear")
	}
	if !reScoreClear.MatchString("顺序清谱 100") {
		t.Error("应识别 清谱/顺序")
	}
	if reScoreClear.MatchString("分数=100") {
		t.Error("不应误判 auto")
	}
	if !reScoreReset.MatchString("重置") || !reScoreReset.MatchString("reset") {
		t.Error("应识别重置/reset")
	}
}

func contains(s, sub string) bool {
	return len(s) >= len(sub) && (func() bool {
		for i := 0; i+len(sub) <= len(s); i++ {
			if s[i:i+len(sub)] == sub {
				return true
			}
		}
		return false
	})()
}
