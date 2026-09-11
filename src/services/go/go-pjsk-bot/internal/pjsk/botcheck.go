package pjsk

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

type botcheckFile struct {
	Unibot []int64 `json:"unibot"`
}

// BotcheckModule 提供 uni 分布式账号的 superuser 管理命令。
// 账号列表与 Python 侧共享 ondemand/database/unibot.json。
type BotcheckModule struct {
	path    string
	supers  map[int64]bool
	client  *onebot.Client
	blocked map[int64]bool
	mu      sync.Mutex
}

func NewBotcheckModule(dataDir string, supers []int64) *BotcheckModule {
	set := make(map[int64]bool, len(supers))
	for _, userID := range supers {
		set[userID] = true
	}
	return &BotcheckModule{
		path:    filepath.Join(dataDir, "ondemand", "database", "unibot.json"),
		supers:  set,
		blocked: make(map[int64]bool),
	}
}

// SetClient 注入 OneBot 客户端，启用自动成员扫描。
func (m *BotcheckModule) SetClient(client *onebot.Client) {
	m.mu.Lock()
	m.client = client
	m.mu.Unlock()
}

// Run 每日刷新已记录的 uni 账号，并扫描当前 bot 所在群。
func (m *BotcheckModule) Run(ctx context.Context, interval time.Duration) {
	if interval <= 0 {
		interval = 24 * time.Hour
	}
	m.refreshGroups(ctx)
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			m.refreshGroups(ctx)
		}
	}
}

// CheckGroup 对齐 Python run_preprocessor：发现 uni 分布式时阻止 Go PJSK 命令。
// 由于 Go 没有 Python group_manager 的插件注册表，这里只治理 Go 自己接管的命令。
func (m *BotcheckModule) CheckGroup(ctx context.Context, event onebot.MessageEvent) (bool, *onebot.ActionRequest) {
	if !event.IsGroup() {
		return false, nil
	}
	if ctx == nil {
		ctx = context.Background()
	}
	checkCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	ctx = checkCtx
	m.mu.Lock()
	client := m.client
	m.mu.Unlock()
	if client == nil {
		return false, nil
	}
	ids, err := m.groupMembers(ctx, client, event.GroupID)
	if err != nil {
		return false, nil
	}
	m.mu.Lock()
	state, loadErr := m.loadLocked()
	if loadErr != nil {
		m.mu.Unlock()
		return false, nil
	}
	known := make(map[int64]bool, len(state.Unibot))
	for _, id := range state.Unibot {
		known[id] = true
	}
	found := int64(0)
	for _, id := range ids {
		if known[id] {
			found = id
			break
		}
	}
	wasBlocked := m.blocked[event.GroupID]
	if found != 0 {
		m.blocked[event.GroupID] = true
	} else {
		delete(m.blocked, event.GroupID)
	}
	m.mu.Unlock()
	if found == 0 {
		return false, nil
	}
	if wasBlocked {
		return true, nil
	}
	return true, onebot.ReplyText(event, fmt.Sprintf("自动检测：群内已有unibot分布式(%d)，已关闭 Go 烧烤相关功能(需再次开启请联系master)", found), false)
}

type botcheckHit struct{ groupID, userID int64 }

func (m *BotcheckModule) scanReport(ctx context.Context) []botcheckHit {
	m.mu.Lock()
	client := m.client
	m.mu.Unlock()
	if client == nil {
		return nil
	}
	data, err := client.Call(ctx, "get_group_list", map[string]any{})
	if err != nil {
		return nil
	}
	var groups []struct {
		GroupID int64 `json:"group_id"`
	}
	if json.Unmarshal(data, &groups) != nil {
		return nil
	}
	m.mu.Lock()
	state, err := m.loadLocked()
	m.mu.Unlock()
	if err != nil {
		return nil
	}
	known := make(map[int64]bool, len(state.Unibot))
	for _, id := range state.Unibot {
		known[id] = true
	}
	var hits []botcheckHit
	for _, group := range groups {
		members, err := m.groupMembers(ctx, client, group.GroupID)
		if err != nil {
			continue
		}
		for _, id := range members {
			if known[id] {
				hits = append(hits, botcheckHit{groupID: group.GroupID, userID: id})
			}
		}
	}
	return hits
}

func (m *BotcheckModule) refreshGroups(ctx context.Context) {
	m.mu.Lock()
	client := m.client
	m.mu.Unlock()
	if client == nil {
		return
	}
	data, err := client.Call(ctx, "get_group_list", map[string]any{})
	if err != nil {
		return
	}
	var groups []struct {
		GroupID int64 `json:"group_id"`
	}
	if json.Unmarshal(data, &groups) != nil {
		return
	}
	for _, group := range groups {
		blocked, action := m.CheckGroup(ctx, onebot.MessageEvent{GroupID: group.GroupID, MessageType: "group"})
		if blocked && action != nil {
			_ = client.SendContext(ctx, action)
		}
	}
}

func (m *BotcheckModule) groupMembers(ctx context.Context, client *onebot.Client, groupID int64) ([]int64, error) {
	data, err := client.Call(ctx, "get_group_member_list", map[string]any{"group_id": groupID})
	if err != nil {
		return nil, err
	}
	var members []struct {
		UserID int64 `json:"user_id"`
	}
	if err := json.Unmarshal(data, &members); err != nil {
		return nil, err
	}
	ids := make([]int64, 0, len(members))
	for _, member := range members {
		if member.UserID > 0 {
			ids = append(ids, member.UserID)
		}
	}
	return ids, nil
}

func (m *BotcheckModule) Register(r *router.Router) {
	r.Register("查询uni分布式", nil, m.handleQuery)
	r.Register("添加uni分布式", []string{"删除uni分布式"}, m.handleModify)
}

func (m *BotcheckModule) isSuper(event onebot.MessageEvent) bool { return m.supers[event.UserID] }

func (m *BotcheckModule) handleQuery(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if !m.isSuper(req.Event) {
		return onebot.ReplyText(req.Event, "仅超级用户可用", true)
	}
	if report := m.scanReport(ctx); len(report) > 0 {
		lines := []string{"当前 bot 发现 uni 分布式的群："}
		for _, item := range report {
			lines = append(lines, fmt.Sprintf("群(%d)内用户(%d)", item.groupID, item.userID))
		}
		return onebot.ReplyText(req.Event, strings.Join(lines, "\n"), true)
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	state, err := m.loadLocked()
	if err != nil {
		return onebot.ReplyText(req.Event, "读取uni分布式记录失败: "+err.Error(), true)
	}
	if len(state.Unibot) == 0 {
		return onebot.ReplyText(req.Event, "当前暂无检测出uni分布式", true)
	}
	sort.Slice(state.Unibot, func(i, j int) bool { return state.Unibot[i] < state.Unibot[j] })
	lines := []string{"当前记录的uni分布式账号："}
	for _, userID := range state.Unibot {
		lines = append(lines, strconv.FormatInt(userID, 10))
	}
	return onebot.ReplyText(req.Event, strings.Join(lines, "\n"), true)
}

func (m *BotcheckModule) handleModify(_ context.Context, req router.Request) *onebot.ActionRequest {
	if !m.isSuper(req.Event) {
		return onebot.ReplyText(req.Event, "仅超级用户可用", true)
	}
	value := strings.TrimSpace(req.Arg)
	userID, err := strconv.ParseInt(value, 10, 64)
	if err != nil || userID <= 0 {
		return onebot.ReplyText(req.Event, "请输入QQ账号", true)
	}
	remove := strings.Contains(req.RawCmd, "删除")
	m.mu.Lock()
	defer m.mu.Unlock()
	state, err := m.loadLocked()
	if err != nil {
		return onebot.ReplyText(req.Event, "读取uni分布式记录失败: "+err.Error(), true)
	}
	found := false
	for i, current := range state.Unibot {
		if current != userID {
			continue
		}
		found = true
		if remove {
			state.Unibot = append(state.Unibot[:i], state.Unibot[i+1:]...)
		}
		break
	}
	if !remove && !found {
		state.Unibot = append(state.Unibot, userID)
	}
	if err := m.saveLocked(state); err != nil {
		return onebot.ReplyText(req.Event, "保存uni分布式记录失败: "+err.Error(), true)
	}
	if remove {
		return onebot.ReplyText(req.Event, "删除成功", true)
	}
	return onebot.ReplyText(req.Event, "添加成功", true)
}

func (m *BotcheckModule) loadLocked() (botcheckFile, error) {
	data, err := os.ReadFile(m.path)
	if os.IsNotExist(err) {
		return botcheckFile{Unibot: []int64{}}, nil
	}
	if err != nil {
		return botcheckFile{}, err
	}
	var state botcheckFile
	if err := json.Unmarshal(data, &state); err != nil {
		return botcheckFile{}, err
	}
	if state.Unibot == nil {
		state.Unibot = []int64{}
	}
	return state, nil
}

func (m *BotcheckModule) saveLocked(state botcheckFile) error {
	if err := os.MkdirAll(filepath.Dir(m.path), 0o755); err != nil {
		return err
	}
	data, err := json.MarshalIndent(state, "", "    ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	return writeAtomic(m.path, data)
}
