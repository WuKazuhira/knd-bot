package pjsk

import (
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

func TestParseFindArgs(t *testing.T) {
	// 角色名 + 筛选组合：ena 四星 限定
	alias, f := parseFindArgs("ena 四星 限定")
	if alias != "ena" {
		t.Errorf("alias = %q, want ena", alias)
	}
	if f.rarity != "rarity_4" {
		t.Errorf("rarity = %q, want rarity_4", f.rarity)
	}
	if f.limited != 1 {
		t.Errorf("limited = %d, want 1", f.limited)
	}

	// 团体 + fes + 技能
	_, f2 := parseFindArgs("25h fes 判分")
	if f2.unit != "school_refusal" {
		t.Errorf("unit = %q, want school_refusal", f2.unit)
	}
	if f2.fes != 1 || f2.limited != 1 {
		t.Errorf("fes 应同时置限定, got fes=%d limited=%d", f2.fes, f2.limited)
	}
	if f2.skill != "score_up" {
		t.Errorf("skill = %q, want score_up", f2.skill)
	}

	// 年份 + 常驻 + leak
	_, f3 := parseFindArgs("2023 常驻 leak")
	if f3.year != 2023 {
		t.Errorf("year = %d, want 2023", f3.year)
	}
	if f3.limited != 2 {
		t.Errorf("limited = %d, want 2 (常驻)", f3.limited)
	}
	if !f3.showLeak {
		t.Error("应识别 leak")
	}
}

func TestApplyCardFilter(t *testing.T) {
	skillSprite := map[int]string{100: "score_up"}
	now := int64(2_000_000_000_000)
	card := map[string]any{
		"id": float64(1), "characterId": float64(19), "skillId": float64(100),
		"cardRarityType": "rarity_4", "attr": "cool", "releaseAt": float64(1_000_000_000_000),
	}

	// 匹配：四星 + cool + score_up
	f := findCardFilter{rarity: "rarity_4", attr: "cool", skill: "score_up"}
	if !applyCardFilter(card, f, skillSprite, nil, nil, nil, nil, now) {
		t.Error("应通过筛选")
	}
	// 属性不匹配
	f2 := findCardFilter{attr: "cute"}
	if applyCardFilter(card, f2, skillSprite, nil, nil, nil, nil, now) {
		t.Error("cute 属性不应通过")
	}
	// 未发布过滤（releaseAt > now 且非 leak）
	future := map[string]any{"id": float64(2), "releaseAt": float64(9_000_000_000_000)}
	if applyCardFilter(future, findCardFilter{}, skillSprite, nil, nil, nil, nil, now) {
		t.Error("未发布卡默认应被过滤")
	}
	// leak 模式放行未发布
	if !applyCardFilter(future, findCardFilter{showLeak: true}, skillSprite, nil, nil, nil, nil, now) {
		t.Error("leak 模式应放行未发布卡")
	}
}

func TestIsAllDigits(t *testing.T) {
	if !isAllDigits("2023") || isAllDigits("20a3") || isAllDigits("") {
		t.Error("isAllDigits 判定错误")
	}
}

func TestFindCardRegisterNumericSuffix(t *testing.T) {
	r := router.New([]string{"/", ""}, router.ParseOwnership(`[
		"findcard"
	]`))
	(&FindCardModule{}).Register(r)

	for _, text := range []string{"查卡1254", "查询卡面1254", "/findcard1254"} {
		event := onebot.MessageEvent{
			SelfID: 1, UserID: 100, MessageID: 1,
			MessageType: "group", GroupID: 200,
			Message: onebot.Message{onebot.Text(text)},
		}
		req, _, ok := r.Match(event)
		if !ok || req.Command != "findcard" || req.Arg != "1254" {
			t.Errorf("%q => ok=%v command=%q arg=%q, want findcard/1254", text, ok, req.Command, req.Arg)
		}
	}
}
