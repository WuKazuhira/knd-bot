package deckopts

import "testing"

func TestBuildEventOptionsContract(t *testing.T) {
	o := BuildEventOptions("活动123 单人 综合力 满技能 #miku", 99, func(s string) int {
		if s == "miku" {
			return 21
		}
		return 0
	}, 15000, 7)
	if o["event_id"] != int64(123) || o["live_type"] != "solo" || o["target"] != "power" {
		t.Fatalf("基础活动参数错误: %#v", o)
	}
	if o["algorithm"] != "all" || o["limit"] != 7 || o["timeout_ms"] != 15000 {
		t.Fatalf("默认算法/数量/超时错误: %#v", o)
	}
	if o["forced_leader_character_id"] != 21 {
		t.Fatalf("固定角色未写入队长: %#v", o)
	}
	if o["music_id"] != int64(10000) || o["music_diff"] != "master" {
		t.Fatalf("默认歌曲契约错误: %#v", o)
	}
}

func TestBuildNoEventAndBonusOptions(t *testing.T) {
	noEvent := BuildNoEventOptions("dfs 满破", nil, 45000, 7)
	if noEvent["algorithm"] != "dfs" || noEvent["music_id"] != int64(10000) || noEvent["event_id"] != nil {
		t.Fatalf("长草 options 错误: %#v", noEvent)
	}
	bonus := BuildBonusOptions("120", 321, 15000, 7)
	if bonus["target"] != "bonus" || bonus["event_id"] != int64(321) {
		t.Fatalf("加成 options 错误: %#v", bonus)
	}
	got := bonus["target_bonus_list"].([]int)
	if len(got) != 1 || got[0] != 120 {
		t.Fatalf("加成目标错误: %#v", got)
	}
	cfg := bonus["rarity_4_config"].(map[string]any)
	if cfg["level_max"] != false {
		t.Fatalf("加成应保持 nochange 配置: %#v", cfg)
	}
}
