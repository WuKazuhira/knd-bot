// Package deckopts 构建 deck-service 组卡请求的 options，对齐 old-python
// deck._options 的各类提取器。本文件实现挑战组卡（参数最少的一类）。
package deckopts

import "strings"

// 关键词表，对齐 _options.py。
var (
	powerTargetKeywords = []string{"综合力", "综合", "总合力", "总和", "power"}
	skillTargetKeywords = []string{"倍率", "实效", "skill", "时效"}
	skillMaxKeywords    = []string{"满技能", "满技", "skillmax", "技能满级", "slv4"}
	masterMaxKeywords   = []string{"满突破", "满破", "rankmax", "mastermax", "5破", "五破"}
	episodeReadKeywords = []string{"剧情已读", "满剧情", "前后篇已读", "前后篇", "已读"}
	canvasKeywords      = []string{"满画布", "全画布", "画布", "满画板", "全画板", "画板"}
	disableKeywords     = []string{"禁用", "disable"}
)

// cardConfig 是单个稀有度的卡配置，字段对齐 _default_card_config_*。
func defaultConfig12() map[string]any {
	return map[string]any{"disable": false, "level_max": true, "episode_read": true, "master_max": true, "skill_max": true}
}

func defaultConfig34bd() map[string]any {
	return map[string]any{"disable": false, "level_max": true, "episode_read": false, "master_max": false, "skill_max": false}
}

// firstKeyword 若 args 含关键词列表中任一，返回 (去除后的 args, true)。
func firstKeyword(args string, keywords []string) (string, bool) {
	for _, kw := range keywords {
		if strings.Contains(args, kw) {
			return strings.TrimSpace(strings.Replace(args, kw, "", 1)), true
		}
	}
	return args, false
}

// BuildChallengeOptions 构建挑战组卡 options，对齐 build_challenge_options。
// resolveChara 把角色昵称解析为 characterId（0 表示未识别）；返回组卡 options。
// timeoutMS 为组卡超时毫秒；returnNum 为返回卡组数。
func BuildChallengeOptions(args string, resolveChara func(string) int, timeoutMS, returnNum int) map[string]any {
	options := map[string]any{}
	args = strings.TrimSpace(args)

	// live 类型：挑战组卡为 challenge / challenge_auto
	liveType := "challenge"
	if strings.Contains(args, "自动") || strings.Contains(args, "auto") {
		liveType = "challenge_auto"
		args = strings.TrimSpace(strings.NewReplacer("自动", "", "auto", "").Replace(args))
	}
	options["live_type"] = liveType

	// 组卡目标
	target := "score"
	if a, ok := firstKeyword(args, powerTargetKeywords); ok {
		args, target = a, "power"
	} else if a, ok := firstKeyword(args, skillTargetKeywords); ok {
		args, target = a, "skill"
	}
	options["target"] = target

	// 卡配置（各稀有度）
	cfg1, cfg2 := defaultConfig12(), defaultConfig12()
	cfg3, cfg4, cfgBd := defaultConfig34bd(), defaultConfig34bd(), defaultConfig34bd()
	allCfgs := []map[string]any{cfg1, cfg2, cfg3, cfg4, cfgBd}
	applyFlag := func(keywords []string, field string) {
		if a, ok := firstKeyword(args, keywords); ok {
			args = a
			for _, c := range allCfgs {
				c[field] = true
			}
		}
	}
	applyFlag(skillMaxKeywords, "skill_max")
	applyFlag(masterMaxKeywords, "master_max")
	applyFlag(episodeReadKeywords, "episode_read")
	applyFlag(canvasKeywords, "canvas")
	applyFlag(disableKeywords, "disable")
	options["rarity_1_config"] = cfg1
	options["rarity_2_config"] = cfg2
	options["rarity_3_config"] = cfg3
	options["rarity_4_config"] = cfg4
	options["rarity_birthday_config"] = cfgBd

	// 算法：dfs 单算法则快，否则 all
	algorithm := "all"
	if strings.Contains(args, "dfs") {
		algorithm = "dfs"
		args = strings.TrimSpace(strings.Replace(args, "dfs", "", 1))
	}
	options["algorithm"] = algorithm
	options["timeout_ms"] = timeoutMS

	// 指定角色（挑战组卡专有）
	if resolveChara != nil {
		for _, seg := range strings.Fields(args) {
			if hasDigit(seg) {
				continue
			}
			if cid := resolveChara(strings.ToLower(seg)); cid != 0 {
				options["challenge_live_character_id"] = cid
				args = strings.TrimSpace(strings.Replace(args, seg, "", 1))
				break
			}
		}
	}

	options["limit"] = returnNum
	return options
}

func hasDigit(s string) bool {
	for _, c := range s {
		if c >= '0' && c <= '9' {
			return true
		}
	}
	return false
}
