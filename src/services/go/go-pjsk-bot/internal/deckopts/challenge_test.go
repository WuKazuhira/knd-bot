package deckopts

import "testing"

func resolveTestChara(alias string) int {
	m := map[string]int{"mnr": 5, "miku": 21, "ena": 19}
	return m[alias]
}

func TestBuildChallengeOptionsBasic(t *testing.T) {
	opts := BuildChallengeOptions("", nil, 15000, 3)
	if opts["live_type"] != "challenge" {
		t.Errorf("默认 live_type 应为 challenge, got %v", opts["live_type"])
	}
	if opts["target"] != "score" {
		t.Errorf("默认 target 应为 score, got %v", opts["target"])
	}
	if opts["algorithm"] != "all" {
		t.Errorf("默认 algorithm 应为 all, got %v", opts["algorithm"])
	}
	if opts["limit"] != 3 {
		t.Errorf("limit 应为 3, got %v", opts["limit"])
	}
	if opts["timeout_ms"] != 15000 {
		t.Errorf("timeout_ms 应为 15000, got %v", opts["timeout_ms"])
	}
}

func TestBuildChallengeOptionsAutoAndTarget(t *testing.T) {
	opts := BuildChallengeOptions("自动 综合力", nil, 15000, 3)
	if opts["live_type"] != "challenge_auto" {
		t.Errorf("自动 应为 challenge_auto, got %v", opts["live_type"])
	}
	if opts["target"] != "power" {
		t.Errorf("综合力 应为 power target, got %v", opts["target"])
	}
}

func TestBuildChallengeOptionsCardConfig(t *testing.T) {
	opts := BuildChallengeOptions("满技能 满破", nil, 15000, 3)
	cfg3 := opts["rarity_3_config"].(map[string]any)
	if cfg3["skill_max"] != true {
		t.Errorf("满技能应置 skill_max, got %v", cfg3["skill_max"])
	}
	if cfg3["master_max"] != true {
		t.Errorf("满破应置 master_max, got %v", cfg3["master_max"])
	}
}

func TestBuildChallengeOptionsChara(t *testing.T) {
	opts := BuildChallengeOptions("miku", resolveTestChara, 15000, 3)
	if opts["challenge_live_character_id"] != 21 {
		t.Errorf("miku 应解析为角色 21, got %v", opts["challenge_live_character_id"])
	}
}

func TestBuildChallengeOptionsDfs(t *testing.T) {
	opts := BuildChallengeOptions("dfs", nil, 15000, 3)
	if opts["algorithm"] != "dfs" {
		t.Errorf("dfs 应为单算法, got %v", opts["algorithm"])
	}
}
