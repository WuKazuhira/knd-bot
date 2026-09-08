package pjsk

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/cards"
	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
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
	md      *masterdata.Loader
	chara   *cards.CharaAliasResolver
}

// NewMysekaiModule 创建 mysekai 模块。
func NewMysekaiModule(f *mysekaidata.Fetcher, s *store.Store, d *draw.Client, md *masterdata.Loader, chara *cards.CharaAliasResolver) *MysekaiModule {
	return &MysekaiModule{fetcher: f, store: s, draw: d, md: md, chara: chara}
}

// Register 注册 msr / msgate / msm / msmat / msb / msf 指令。
func (m *MysekaiModule) Register(r *router.Router) {
	r.Register("msr", []string{"msmap", "msa"}, m.handleMsr)
	r.Register("msg", []string{"msgate"}, m.handleGate)
	r.Register("msm", []string{"mss", "mssong"}, m.handleMusicRecord)
	r.Register("烤森材料", []string{"mysekai材料"}, m.handleMaterial)
	r.Register("msb", []string{"mysekai蓝图", "mysekaiblueprint"}, m.handleBlueprint)
	r.Register("msf", []string{"mysekai家具", "家具列表", "mysekaifurniture"}, m.handleFurniture)
	r.Register("msd", []string{"烤森抓包", "烤森抓包数据", "pjsk烤森抓包"}, m.handleData)
	r.Register("msp", []string{"mysekai照片", "mysekaiphoto"}, m.handlePhoto)
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

// parseUnitArgRest 解析团名并返回剩余文本（去掉首个命中的团名词）。对齐 parse_unit_arg 的 (unit, rest)。
func parseUnitArgRest(args string) (string, string) {
	unit := ""
	rest := make([]string, 0)
	for _, p := range strings.Fields(strings.TrimSpace(args)) {
		if u, ok := unitAliases[strings.ToLower(p)]; ok && unit == "" {
			unit = u
			continue
		}
		rest = append(rest, p)
	}
	return unit, strings.TrimSpace(strings.Join(rest, " "))
}

// stripKeywords 从 args 中剔除命中的关键字，返回 (其余文本, 命中集合)。对齐 _strip_keywords。
func stripKeywords(args string, keywords []string) (string, map[string]bool) {
	hit := map[string]bool{}
	fields := strings.Fields(args)
	for _, kw := range keywords {
		kept := make([]string, 0, len(fields))
		for _, f := range fields {
			if f == kw {
				hit[kw] = true
			} else {
				kept = append(kept, f)
			}
		}
		fields = kept
	}
	return strings.TrimSpace(strings.Join(fields, " ")), hit
}

// resolveCharaUnitID 把 characterId 解析到 game_character_units.id。
// V 家角色（cid 21~26）出现在多个组合，需配合 unit 参数消歧。对齐 _resolve_chara_unit_id。
func (m *MysekaiModule) resolveCharaUnitID(cid int, unit string, server int) (int, error) {
	gcu, _ := m.md.Load("gameCharacterUnits.json", server)
	cuList := make([]map[string]any, 0)
	for _, cu := range gcu {
		if intField(cu, "gameCharacterId") == cid {
			cuList = append(cuList, cu)
		}
	}
	if len(cuList) == 0 {
		return 0, fmt.Errorf("找不到角色 %d 的组合数据", cid)
	}

	// 只保留 mysekai 门抽中真正可见的 cuid。
	if lottery, err := m.md.Load("mysekaiGateCharacterLotteries.json", server); err == nil {
		valid := map[int]bool{}
		for _, item := range lottery {
			if id := intField(item, "gameCharacterUnitId"); id != 0 {
				valid[id] = true
			}
		}
		if len(valid) > 0 {
			filtered := make([]map[string]any, 0, len(cuList))
			for _, cu := range cuList {
				if valid[intField(cu, "id")] {
					filtered = append(filtered, cu)
				}
			}
			cuList = filtered
		}
	}
	if len(cuList) == 0 {
		return 0, fmt.Errorf("该角色暂无 mysekai 对话数据")
	}
	if len(cuList) == 1 {
		return intField(cuList[0], "id"), nil
	}
	if unit == "" {
		units := make([]string, 0, len(cuList))
		for _, cu := range cuList {
			u := strField(cu, "unit")
			if u == "" {
				u = "?"
			}
			units = append(units, u)
		}
		return 0, fmt.Errorf("该角色存在多个组合（%s），请同时指定组合，例如「/msb miku ln」", strings.Join(units, "/"))
	}
	for _, cu := range cuList {
		if strField(cu, "unit") == unit {
			return intField(cu, "id"), nil
		}
	}
	return 0, fmt.Errorf("找不到「%s」组合下的该角色", unit)
}

// handleBlueprint 实现 msb（蓝图/家具可制作列表；带角色名则出对话进度）。
func (m *MysekaiModule) handleBlueprint(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	if m.store == nil || m.md == nil || m.chara == nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	uid, isPrivate, exists, err := m.store.GetUserBind(ctx, req.Event.UserID, server)
	if err != nil || !exists {
		return onebot.ReplyText(req.Event, "你还没有绑定"+req.Server.Name()+"账号哦", true)
	}
	uidStr := itoa64(uid)

	args := strings.ToLower(strings.TrimSpace(req.Arg))
	args, hits := stripKeywords(args, []string{"all", "id"})
	showAllTalks := hits["all"]
	unit, rest := parseUnitArgRest(args)
	cid := m.chara.Resolve(rest)

	mysekaiInfo, _, err := m.fetcher.GetMysekaiInfo(ctx, uidStr, server, "latest", true)
	if err != nil {
		return onebot.ReplyText(req.Event, err.Error(), true)
	}
	profile := mysekaidata.ProfileFromSuiteData(uidStr, nil)

	var img []byte
	if cid == 0 {
		img, err = m.draw.Render(ctx, "mysekai_fixture_list", map[string]any{
			"profile": profile, "is_private": isPrivate, "mysekai_info": mysekaiInfo,
			"only_craftable": true, "pjsk_type": server,
		})
	} else {
		cuid, rerr := m.resolveCharaUnitID(cid, unit, server)
		if rerr != nil {
			return onebot.ReplyText(req.Event, rerr.Error(), true)
		}
		suiteData, _ := m.fetcher.GetSuiteData(ctx, uidStr, server)
		profile = mysekaidata.ProfileFromSuiteData(uidStr, suiteData)
		img, err = m.draw.Render(ctx, "mysekai_talk_list", map[string]any{
			"profile": profile, "is_private": isPrivate, "mysekai_info": mysekaiInfo,
			"suite_data": suiteData, "cuid": cuid,
			"show_all_talks": showAllTalks, "pjsk_type": server,
		})
	}
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}

// handleFurniture 实现 msf（家具列表/家具详情/角色对话进度）。
func (m *MysekaiModule) handleFurniture(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	if m.md == nil || m.chara == nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	args := strings.ToLower(strings.TrimSpace(req.Arg))

	// 全数字参数视为家具 ID 详情。
	tokens := strings.Fields(args)
	allDigit := len(tokens) > 0
	fids := make([]int, 0, len(tokens))
	for _, t := range tokens {
		n, ok := parseIntToken(t)
		if !ok {
			allDigit = false
			break
		}
		fids = append(fids, n)
	}
	if allDigit {
		if len(fids) > 10 {
			return onebot.ReplyText(req.Event, "最多一次查询 10 个家具", true)
		}
		img, err := m.draw.Render(ctx, "mysekai_fixture_detail", map[string]any{
			"fids": fids, "pjsk_type": server,
		})
		if err != nil {
			return onebot.ReplyText(req.Event, errBug, false)
		}
		return onebot.ReplyImage(req.Event, base64Encode(img))
	}

	// 尝试把参数当作角色名查询对话进度。
	rest, hits := stripKeywords(args, []string{"all", "id"})
	showAllTalks := hits["all"]
	unit, rest := parseUnitArgRest(rest)
	if cid := m.chara.Resolve(rest); cid != 0 {
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
		cuid, rerr := m.resolveCharaUnitID(cid, unit, server)
		if rerr != nil {
			return onebot.ReplyText(req.Event, rerr.Error(), true)
		}
		suiteData, _ := m.fetcher.GetSuiteData(ctx, uidStr, server)
		profile := mysekaidata.ProfileFromSuiteData(uidStr, suiteData)
		img, err := m.draw.Render(ctx, "mysekai_talk_list", map[string]any{
			"profile": profile, "is_private": isPrivate, "mysekai_info": mysekaiInfo,
			"suite_data": suiteData, "cuid": cuid,
			"show_all_talks": showAllTalks, "pjsk_type": server,
		})
		if err != nil {
			return onebot.ReplyText(req.Event, errBug, false)
		}
		return onebot.ReplyImage(req.Event, base64Encode(img))
	}

	// 缺省：全家具列表。
	img, err := m.draw.Render(ctx, "mysekai_fixture_list", map[string]any{
		"profile": nil, "is_private": false, "mysekai_info": nil,
		"only_craftable": false, "pjsk_type": server,
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}

// handleData 实现 msd（烤森抓包状态）：报告最近一次 mysekai 数据的上传时间。
func (m *MysekaiModule) handleData(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	if m.store == nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	uid, _, exists, err := m.store.GetUserBind(ctx, req.Event.UserID, server)
	if err != nil || !exists {
		return onebot.ReplyText(req.Event, "你还没有绑定"+req.Server.Name()+"账号哦", true)
	}
	uidStr := itoa64(uid)

	var text string
	info, msg, ferr := m.fetcher.GetMysekaiInfo(ctx, uidStr, server, "latest", true)
	if ferr != nil {
		text = fmt.Sprintf("%s MySekai数据\n获取失败：%s\n", uidStr, ferr.Error())
	} else {
		up := "未知"
		if ts := intField(info, "upload_time"); ts != 0 {
			up = time.Unix(int64(ts), 0).Format("01-02 15:04:05")
		}
		text = fmt.Sprintf("%s (%s) MySekai数据\n获取成功：%s\n", uidStr, req.Server.Name(), up)
		if msg != "" {
			text += fmt.Sprintf("提示：%s\n", msg)
		}
	}
	text += "---\n发送 /抓包 获取抓包教程"
	return onebot.ReplyText(req.Event, text, false)
}

// handlePhoto 实现 msp（照片）：按编号取 MySekai 照片，返回图片 + 拍摄时间。
func (m *MysekaiModule) handlePhoto(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	if m.store == nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	seq, ok := parseIntToken(strings.TrimSpace(req.Arg))
	if !ok {
		return onebot.ReplyText(req.Event, "请输入正确的照片编号（从 1 或 -1 开始）", true)
	}
	uid, _, exists, err := m.store.GetUserBind(ctx, req.Event.UserID, server)
	if err != nil || !exists {
		return onebot.ReplyText(req.Event, "你还没有绑定"+req.Server.Name()+"账号哦", true)
	}
	uidStr := itoa64(uid)

	img, ts, perr := m.fetcher.GetPhoto(ctx, uidStr, server, seq)
	if perr != nil {
		return onebot.ReplyText(req.Event, perr.Error(), true)
	}
	shot := time.Unix(ts, 0).Format("2006-01-02 15:04")
	msg := onebot.Message{
		onebot.ImageBytes(base64Encode(img)),
		onebot.Text("拍摄时间：" + shot),
	}
	return onebot.SendMessageAction(req.Event, msg)
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
