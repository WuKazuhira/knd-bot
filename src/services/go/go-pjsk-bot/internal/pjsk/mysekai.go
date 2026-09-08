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

// Register 注册 msr 指令。
func (m *MysekaiModule) Register(r *router.Router) {
	r.Register("msr", []string{"msmap", "msa"}, m.handleMsr)
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
