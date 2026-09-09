package pjsk

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
)

// writeMD 在 dataDir/ondemand/jp/ 写一个 masterdata JSON fixture。
func writeMD(t *testing.T, dataDir, filename string, v any) {
	t.Helper()
	dir := filepath.Join(dataDir, "ondemand", "jp")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(v)
	if err := os.WriteFile(filepath.Join(dir, filename), raw, 0o644); err != nil {
		t.Fatal(err)
	}
}

func setupBanEventMD(t *testing.T) *masterdata.Loader {
	dir := t.TempDir()
	// 活动：10=marathon(有歌,箱活), 11=cheerful_carnival(有歌,箱活),
	//       12=marathon(无歌,非箱活), 13=world_bloom(非箱活)
	writeMD(t, dir, "events.json", []map[string]any{
		{"id": 10, "eventType": "marathon", "name": "E10", "startAt": 3000},
		{"id": 11, "eventType": "cheerful_carnival", "name": "E11", "startAt": 1000},
		{"id": 12, "eventType": "marathon", "name": "E12", "startAt": 2000},
		{"id": 13, "eventType": "world_bloom", "name": "E13", "startAt": 4000},
	})
	writeMD(t, dir, "eventMusics.json", []map[string]any{
		{"eventId": 10, "musicId": 100, "seq": 1},
		{"eventId": 11, "musicId": 101, "seq": 1},
		// 12 无歌
	})
	// 活动卡：event10 → 卡[500(FES),501(普通,chara=19)]；event11 → 卡[600(普通,chara=19)]
	writeMD(t, dir, "eventCards.json", []map[string]any{
		{"eventId": 10, "cardId": 500},
		{"eventId": 10, "cardId": 501},
		{"eventId": 11, "cardId": 600},
	})
	writeMD(t, dir, "cards.json", []map[string]any{
		{"id": 500, "characterId": 21, "cardSupplyId": 1}, // FES 卡
		{"id": 501, "characterId": 19, "cardSupplyId": 2}, // 普通，最小非FES
		{"id": 600, "characterId": 19, "cardSupplyId": 2},
	})
	writeMD(t, dir, "cardSupplies.json", []map[string]any{
		{"id": 1, "cardSupplyType": "festival_limited"},
		{"id": 2, "cardSupplyType": "normal"},
	})
	return masterdata.New(dir)
}

func TestBanEventIDSet(t *testing.T) {
	md := setupBanEventMD(t)
	set := banEventIDSet(md, 0)
	// 10/11 是箱活（marathon/cc + 有歌）；12 无歌、13 非箱活类型
	if !set[10] || !set[11] {
		t.Errorf("10/11 应为箱活: %v", set)
	}
	if set[12] || set[13] {
		t.Errorf("12/13 不应为箱活: %v", set)
	}
	if !set[74] {
		t.Error("74 应被 SDL3 特判加入")
	}
}

func TestEventBannerCharaID(t *testing.T) {
	md := setupBanEventMD(t)
	// event10：卡 500(FES,跳过) / 501(普通,chara19) → 最小非FES=501 → chara 19
	if got := eventBannerCharaID(md, 0, 10); got != 19 {
		t.Errorf("event10 banner chara=%d want 19", got)
	}
	// event11：卡 600(chara19) → 19
	if got := eventBannerCharaID(md, 0, 11); got != 19 {
		t.Errorf("event11 banner chara=%d want 19", got)
	}
}

func TestCharaBanEvents(t *testing.T) {
	md := setupBanEventMD(t)
	// 角色 19(ena) 的箱活：event10 和 event11，按 startAt 升序 → [11(1000), 10(3000)]
	evs := charaBanEvents(md, 0, 19)
	if len(evs) != 2 {
		t.Fatalf("ena 应有 2 次箱活, got %d", len(evs))
	}
	if evs[0].ID != 11 || evs[0].BanIndex != 1 {
		t.Errorf("第1次箱活应为 event11(ban_index=1), got id=%d idx=%d", evs[0].ID, evs[0].BanIndex)
	}
	if evs[1].ID != 10 || evs[1].BanIndex != 2 {
		t.Errorf("第2次箱活应为 event10(ban_index=2), got id=%d idx=%d", evs[1].ID, evs[1].BanIndex)
	}
	// 无箱活的角色
	if evs := charaBanEvents(md, 0, 5); len(evs) != 0 {
		t.Errorf("角色5 应无箱活, got %d", len(evs))
	}
	if evs := charaBanEvents(md, 0, 0); evs != nil {
		t.Error("charaID=0 应返回 nil")
	}
}

func TestExtractBanEventArg(t *testing.T) {
	md := setupBanEventMD(t)
	// resolver: ena→19（其它→0）
	resolve := func(a string) int {
		if a == "ena" {
			return 19
		}
		return 0
	}

	// "ena1 查卡" → ena 第1次箱活(event11)，剩余 "查卡"
	ev, rest, errMsg := extractBanEventArg(md, 0, "ena1 查卡", resolve)
	if errMsg != "" || ev == nil {
		t.Fatalf("ena1 应命中: ev=%v err=%q", ev, errMsg)
	}
	if ev.ID != 11 || rest != "查卡" {
		t.Errorf("ena1 => id=%d rest=%q want 11/查卡", ev.ID, rest)
	}

	// "ena2" → 第2次箱活(event10)
	ev, _, _ = extractBanEventArg(md, 0, "ena2", resolve)
	if ev == nil || ev.ID != 10 {
		t.Errorf("ena2 => %v want event10", ev)
	}

	// "ena9" → 超出次数，返回错误提示
	ev, _, errMsg = extractBanEventArg(md, 0, "ena9", resolve)
	if ev != nil || errMsg == "" {
		t.Errorf("ena9 应超范围报错: ev=%v err=%q", ev, errMsg)
	}

	// "miku3" → 角色无法识别（resolver 返回0）→ 不命中，原样返回
	ev, rest, errMsg = extractBanEventArg(md, 0, "miku3", resolve)
	if ev != nil || errMsg != "" || rest != "miku3" {
		t.Errorf("miku3 未识别应原样返回: ev=%v rest=%q", ev, rest)
	}

	// 无 token 的纯文本 → 原样
	ev, rest, _ = extractBanEventArg(md, 0, "查活动", resolve)
	if ev != nil || rest != "查活动" {
		t.Errorf("无 token 应原样: ev=%v rest=%q", ev, rest)
	}

	// 边界：ena0 序号<=0 不命中
	ev, _, _ = extractBanEventArg(md, 0, "ena0", resolve)
	if ev != nil {
		t.Errorf("ena0 序号0 不应命中: %v", ev)
	}
}
