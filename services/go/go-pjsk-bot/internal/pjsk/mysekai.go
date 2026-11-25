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
	"github.com/kazuhira/go-pjsk-bot/internal/msrsub"
	"github.com/kazuhira/go-pjsk-bot/internal/mysekaidata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/store"
)

type MysekaiModule struct {
	fetcher   *mysekaidata.Fetcher
	store     *store.Store
	draw      *draw.Client
	md        *masterdata.Loader
	chara     *cards.CharaAliasResolver
	dataDir   string
	staticDir string
	msrSub    *msrsub.Store
}

func NewMysekaiModule(f *mysekaidata.Fetcher, s *store.Store, d *draw.Client, md *masterdata.Loader, chara *cards.CharaAliasResolver, dataDir, staticDir string, msrSub *msrsub.Store) *MysekaiModule {
	return &MysekaiModule{fetcher: f, store: s, draw: d, md: md, chara: chara, dataDir: dataDir, staticDir: staticDir, msrSub: msrSub}
}

func (m *MysekaiModule) Register(r *router.Router) {
	cn := m.withCnCheck
	r.Register("msr", []string{"msmap", "msa", "pjsk mysekai res", "mysekai 资源"}, cn(m.handleMsr))
	r.Register("msg", []string{"msgate", "pjsk mysekai gate", "msgall", "msgln", "msgmmj", "msgvbs", "msgws", "msg25"}, cn(m.handleGate))
	r.Register("msm", []string{"mss", "mssong", "pjsk mysekai musicrecord"}, cn(m.handleMusicRecord))
	r.Register("烤森材料", []string{"mysekai材料", "pjsk mysekai材料"}, cn(m.handleMaterial))
	r.RegisterNumericSuffix("msb", []string{"mysekai蓝图", "mysekaiblueprint", "mysekai blueprint", "mysekai 蓝图", "pjsk mysekai blueprint"}, cn(m.handleBlueprint))
	r.RegisterNumericSuffix("msf", []string{"mysekai家具", "家具列表", "mysekaifurniture", "mysekai 家具", "pjsk mysekai fixture", "pjsk mysekai furniture"}, cn(m.handleFurniture))
	r.Register("msd", []string{"烤森抓包", "烤森抓包数据", "pjsk烤森抓包", "pjsk烤森抓包数据"}, cn(m.handleData))
	r.RegisterNumericSuffix("msp", []string{"mysekai照片", "mysekaiphoto", "mysekai 照片", "pjsk mysekai photo", "pjsk mysekai picture"}, cn(m.handlePhoto))
	if m.msrSub != nil {
		r.Register("msr订阅", []string{"msr推送订阅", "msr自动推送"}, m.handleMsrSubscribe)
		r.Register("msr取消订阅", []string{"msr推送取消", "msr取消推送"}, m.handleMsrUnsubscribe)
	}
}

func (m *MysekaiModule) withCnCheck(h router.Handler) router.Handler {
	return func(ctx context.Context, req router.Request) *onebot.ActionRequest {
		if errMsg := assertCnMsrAllowed(m.dataDir, m.staticDir, req.Event.GroupID, int(req.Server)); errMsg != "" {
			return onebot.ReplyText(req.Event, errMsg, true)
		}
		return h(ctx, req)
	}
}

func (m *MysekaiModule) handleMsrSubscribe(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	if !req.Event.IsGroup() {
		return onebot.ReplyText(req.Event, "MSR 自动推送只能在群聊中订阅", true)
	}
	if errMsg := assertCnMsrAllowed(m.dataDir, m.staticDir, req.Event.GroupID, server); errMsg != "" {
		return onebot.ReplyText(req.Event, errMsg, true)
	}
	if m.fetcher == nil || !m.fetcher.SupportsUploadTime(server) {
		return onebot.ReplyText(req.Event, req.Server.Name()+" 暂不支持 MySekai 自动推送", true)
	}
	if m.store == nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	uid, _, exists, err := m.store.GetUserBind(ctx, req.Event.UserID, server)
	if err != nil || !exists {
		return onebot.ReplyText(req.Event, "你还没有绑定"+req.Server.Name()+"账号哦", true)
	}
	if aerr := m.msrSub.Add(ctx, itoa64(req.Event.UserID), itoa64(req.Event.GroupID), serverCode(server), itoa64(uid), "latest"); aerr != nil {
		return onebot.ReplyText(req.Event, "订阅失败，请稍后再试", true)
	}
	return onebot.ReplyText(req.Event, "已订阅 "+req.Server.Name()+" MySekai 数据更新自动推送", false)
}

func (m *MysekaiModule) handleMsrUnsubscribe(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	if errMsg := assertCnMsrAllowed(m.dataDir, m.staticDir, req.Event.GroupID, server); errMsg != "" {
		return onebot.ReplyText(req.Event, errMsg, true)
	}
	removed, err := m.msrSub.Remove(ctx, itoa64(req.Event.UserID), serverCode(server))
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	if removed {
		return onebot.ReplyText(req.Event, "已取消 "+req.Server.Name()+" MySekai 自动推送订阅", false)
	}
	return onebot.ReplyText(req.Event, "你没有订阅 "+req.Server.Name()+" MySekai 自动推送", true)
}

func (m *MysekaiModule) handleMsr(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	showAll := false
	for _, w := range strings.Fields(strings.ToLower(req.Arg)) {
		if w == "all" {
			showAll = true
		}
	}

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

var unitAliases = map[string]string{
	"ln": "light_sound", "leo": "light_sound", "l/n": "light_sound", "leoneed": "light_sound", "leo/need": "light_sound", "星星": "light_sound",
	"mmj": "idol", "mm": "idol", "moremorejump": "idol", "偶像": "idol",
	"vbs": "street", "vividbadsquad": "street", "街头": "street",
	"ws": "theme_park", "wxs": "theme_park", "wonderlands": "theme_park", "游乐园": "theme_park",
	"25": "school_refusal", "n25": "school_refusal", "ニーゴ": "school_refusal", "25时": "school_refusal", "25時": "school_refusal", "nightcord": "school_refusal",
	"vs": "piapro", "vocaloid": "piapro", "piapro": "piapro",
}

var unitGateID = map[string]int{
	"light_sound": 1, "idol": 2, "street": 3, "theme_park": 4, "school_refusal": 5,
}

func parseUnitArg(args string) string {
	for _, p := range strings.Fields(strings.ToLower(args)) {
		if u, ok := unitAliases[p]; ok {
			return u
		}
	}
	return ""
}

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

	// 只保留门抽中可见的 cuid
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

func (m *MysekaiModule) handleFurniture(ctx context.Context, req router.Request) *onebot.ActionRequest {
	server := int(req.Server)
	if m.md == nil || m.chara == nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	args := strings.ToLower(strings.TrimSpace(req.Arg))

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

	img, err := m.draw.Render(ctx, "mysekai_fixture_list", map[string]any{
		"profile": nil, "is_private": false, "mysekai_info": nil,
		"only_craftable": false, "pjsk_type": server,
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64Encode(img))
}

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

var msgCmdUnitSuffixes = map[string]string{
	"ln":  "light_sound",
	"mmj": "idol",
	"vbs": "street",
	"ws":  "theme_park",
	"25":  "school_refusal",
}

func cmdGateSuffix(rawCmd string) string {
	raw := strings.ToLower(strings.TrimSpace(rawCmd))
	if strings.HasPrefix(raw, "cn") {
		raw = raw[2:]
	} else if strings.HasPrefix(raw, "tw") {
		raw = raw[2:]
	}
	if strings.HasPrefix(raw, "msg") && raw != "msg" && raw != "msgate" {
		return raw[3:]
	}
	return ""
}

func currentGateFromSuite(suiteData map[string]any) int {
	raw, _ := suiteData["userMysekaiGates"].([]any)
	bestID := 0
	bestLv := -1
	for _, item := range raw {
		row, ok := item.(map[string]any)
		if !ok {
			continue
		}
		gid := runtimeInt(row, "mysekaiGateId")
		if gid <= 0 {
			continue
		}
		lv := runtimeInt(row, "mysekaiGateLevel")
		if lv > bestLv || (lv == bestLv && gid < bestID) {
			bestID = gid
			bestLv = lv
		}
	}
	return bestID
}

func (m *MysekaiModule) resolveCurrentGateID(ctx context.Context, uidStr string, server int, suiteData map[string]any) int {
	if info, _, err := m.fetcher.GetMysekaiInfo(ctx, uidStr, server, "latest", true); err == nil && info != nil {
		if visit, ok := info["userMysekaiGateCharacterVisit"].(map[string]any); ok {
			if gate, ok := visit["userMysekaiGate"].(map[string]any); ok {
				if gid := runtimeInt(gate, "mysekaiGateId"); gid > 0 {
					return gid
				}
			}
		}
	}
	return currentGateFromSuite(suiteData)
}

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

	suffix := cmdGateSuffix(req.RawCmd)
	rest, hits := stripKeywords(strings.ToLower(strings.TrimSpace(req.Arg)), []string{"all"})
	showAll := suffix == "all" || hits["all"]

	var gateID any
	switch {
	case showAll:
		gateID = nil
	case msgCmdUnitSuffixes[suffix] != "":
		if id, ok := unitGateID[msgCmdUnitSuffixes[suffix]]; ok {
			gateID = id
		}
	default:
		if unit := parseUnitArg(rest); unit != "" {
			if id, ok := unitGateID[unit]; ok {
				gateID = id
			}
		} else if rest != "" {
			return onebot.ReplyText(req.Event, "未识别的组合名；可用 msg（当前门）、msg all（全部门）或 msg ln / mmj / vbs / ws / 25", true)
		} else if gid := m.resolveCurrentGateID(ctx, uidStr, server, suiteData); gid > 0 {
			gateID = gid
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
