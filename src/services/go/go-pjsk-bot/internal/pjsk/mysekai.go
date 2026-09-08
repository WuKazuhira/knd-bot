package pjsk

import (
	"context"
	"strings"
	"sync"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/mysekaidata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/store"
)

// MysekaiModule 实现 MySekai 资源查询（msr：三张图 summary/res_list/map）。
//
// 取绑定 uid → 拉 mysekai/suite 数据 → 并发渲染三图（走 pjsk-draw）→ 合并发送。
// CN 服白名单校验、msr 订阅推送、msb/msf/msgate 等其它指令作为后续增量。
type MysekaiModule struct {
	fetcher *mysekaidata.Fetcher
	store   *store.Store
	draw    *draw.Client
}

// NewMysekaiModule 创建 mysekai 模块。
func NewMysekaiModule(f *mysekaidata.Fetcher, s *store.Store, d *draw.Client) *MysekaiModule {
	return &MysekaiModule{fetcher: f, store: s, draw: d}
}

// Register 注册 msr / msgate / msm 指令。
func (m *MysekaiModule) Register(r *router.Router) {
	r.Register("msr", []string{"msmap", "msa"}, m.handleMsr)
	r.Register("msg", []string{"msgate"}, m.handleGate)
	r.Register("msm", []string{"mss", "mssong"}, m.handleMusicRecord)
	r.Register("烤森材料", []string{"mysekai材料"}, m.handleMaterial)
}

func (m *MysekaiModule) handleMsr(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	showAll := false
	for _, w := range strings.Fields(strings.ToLower(req.Arg)) {
		if w == "all" {
			showAll = true
		}
	}

	// 取绑定 uid
	if m.store == nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	uid, isPrivate, exists, err := m.store.GetUserBind(ctx, req.Event.UserID, server)
	if err != nil || !exists {
		return onebot.ReplyText(req.Event, "你还没有绑定"+req.Server.Name()+"账号哦", true)
	}
	uidStr := itoa64(uid)

	mysekaiInfo, pmsg, err := m.fetcher.GetMysekaiInfo(ctx, uidStr, server, "latest", true)
	if err != nil {
		return onebot.ReplyText(req.Event, err.Error(), true)
	}
	suiteData, suiteMsg := m.fetcher.GetSuiteData(ctx, uidStr, server)
	profile := mysekaidata.ProfileFromSuiteData(uidStr, suiteData)

	base := map[string]any{
		"profile":      profile,
		"is_private":   isPrivate,
		"mysekai_info": mysekaiInfo,
		"pjsk_type":    server,
	}
	dataMsg := pmsg
	if dataMsg == "" {
		dataMsg = suiteMsg
	}

	// 并发渲染三图（任一失败则整体失败，对齐 asyncio.gather）。
	tasks := []struct {
		name    string
		payload map[string]any
	}{
		{"mysekai_summary", merge(base, map[string]any{"suite_data": suiteData, "data_msg": dataMsg})},
		{"mysekai_res_list", merge(base, map[string]any{"show_harvested": showAll, "data_msg": pmsg})},
		{"mysekai_map", merge(base, map[string]any{"show_harvested": showAll})},
	}
	images := make([][]byte, len(tasks))
	errs := make([]error, len(tasks))
	var wg sync.WaitGroup
	for i, t := range tasks {
		wg.Add(1)
		go func(i int, name string, payload map[string]any) {
			defer wg.Done()
			images[i], errs[i] = m.draw.Render(ctx, name, payload)
		}(i, t.name, t.payload)
	}
	wg.Wait()
	for _, e := range errs {
		if e != nil {
			return onebot.ReplyText(req.Event, errBug, false)
		}
	}

	msg := make(onebot.Message, 0, len(images))
	for _, img := range images {
		msg = append(msg, onebot.ImageBytes(base64Encode(img)))
	}
	return onebot.SendMessageAction(req.Event, msg)
}

// merge 浅合并两个 map（b 覆盖 a），返回新 map。
func merge(a, b map[string]any) map[string]any {
	out := make(map[string]any, len(a)+len(b))
	for k, v := range a {
		out[k] = v
	}
	for k, v := range b {
		out[k] = v
	}
	return out
}

// 团名别名 → 内部名，对齐 mysekai UNIT_ALIASES。
var unitAliases = map[string]string{
	"ln": "light_sound", "leo": "light_sound", "l/n": "light_sound", "星星": "light_sound",
	"mmj": "idol", "mm": "idol", "偶像": "idol",
	"vbs": "street", "街头": "street",
	"ws": "theme_park", "wxs": "theme_park", "游乐园": "theme_park",
	"25": "school_refusal", "n25": "school_refusal", "ニーゴ": "school_refusal", "25时": "school_refusal",
	"vs": "piapro", "vocaloid": "piapro", "piapro": "piapro",
}

// 内部名 → gate_id（piapro 无门），对齐 UNIT_GATEID_MAP。
var unitGateID = map[string]int{
	"light_sound": 1, "idol": 2, "street": 3, "theme_park": 4, "school_refusal": 5,
}

// parseUnitArg 从参数解析团名，返回内部名（未识别为空）。对齐 parse_unit_arg。
func parseUnitArg(args string) string {
	for _, p := range strings.Fields(strings.ToLower(args)) {
		if u, ok := unitAliases[p]; ok {
			return u
		}
	}
	return ""
}

// handleGate 实现 msgate（门/来访角色）：取 suite → 渲染门图。
func (m *MysekaiModule) handleGate(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	if m.store == nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	uid, isPrivate, exists, err := m.store.GetUserBind(ctx, req.Event.UserID, server)
	if err != nil || !exists {
		return onebot.ReplyText(req.Event, "你还没有绑定"+req.Server.Name()+"账号哦", true)
	}
	uidStr := itoa64(uid)

	suiteData, suiteMsg := m.fetcher.GetSuiteData(ctx, uidStr, server)
	if suiteData == nil {
		return onebot.ReplyText(req.Event, "查询失败："+suiteMsg, true)
	}
	profile := mysekaidata.ProfileFromSuiteData(uidStr, suiteData)

	var gateID any
	if unit := parseUnitArg(req.Arg); unit != "" {
		if id, ok := unitGateID[unit]; ok {
			gateID = id
		}
	}

	img, err := m.draw.Render(ctx, "mysekai_gate", map[string]any{
		"profile":    profile,
		"is_private": isPrivate,
		"suite_data": suiteData,
		"gate_id":    gateID,
		"pjsk_type":  server,
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}

// handleMusicRecord 实现 msm/mss/mssong（唱片）：取 mysekai 数据 → 渲染唱片图。
func (m *MysekaiModule) handleMusicRecord(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	if m.store == nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	uid, isPrivate, exists, err := m.store.GetUserBind(ctx, req.Event.UserID, server)
	if err != nil || !exists {
		return onebot.ReplyText(req.Event, "你还没有绑定"+req.Server.Name()+"账号哦", true)
	}
	uidStr := itoa64(uid)

	mysekaiInfo, _, err := m.fetcher.GetMysekaiInfo(ctx, uidStr, server, "latest", true)
	if err != nil {
		return onebot.ReplyText(req.Event, err.Error(), true)
	}
	suiteData, _ := m.fetcher.GetSuiteData(ctx, uidStr, server)
	profile := mysekaidata.ProfileFromSuiteData(uidStr, suiteData)

	showID := false
	for _, w := range strings.Fields(strings.ToLower(req.Arg)) {
		if w == "id" {
			showID = true
		}
	}

	img, err := m.draw.Render(ctx, "mysekai_musicrecord", map[string]any{
		"profile":      profile,
		"is_private":   isPrivate,
		"mysekai_info": mysekaiInfo,
		"show_id":      showID,
		"pjsk_type":    server,
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}

// handleMaterial 实现 烤森材料/mysekai材料：取 suite → 渲染材料图。
func (m *MysekaiModule) handleMaterial(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	if m.store == nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	uid, isPrivate, exists, err := m.store.GetUserBind(ctx, req.Event.UserID, server)
	if err != nil || !exists {
		return onebot.ReplyText(req.Event, "你还没有绑定"+req.Server.Name()+"账号哦", true)
	}
	uidStr := itoa64(uid)

	suiteData, suiteMsg := m.fetcher.GetSuiteData(ctx, uidStr, server)
	if suiteData == nil {
		return onebot.ReplyText(req.Event, "查询失败："+suiteMsg, true)
	}
	profile := mysekaidata.ProfileFromSuiteData(uidStr, suiteData)

	showAll := false
	for _, w := range strings.Fields(strings.ToLower(req.Arg)) {
		if w == "all" {
			showAll = true
		}
	}

	img, err := m.draw.Render(ctx, "mysekai_material", map[string]any{
		"profile":    profile,
		"is_private": isPrivate,
		"suite_data": suiteData,
		"show_all":   showAll,
		"pjsk_type":  server,
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}
