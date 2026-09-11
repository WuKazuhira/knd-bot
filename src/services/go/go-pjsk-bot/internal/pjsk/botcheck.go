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
	path         string
	supers       map[int64]bool
	client       *onebot.Client
	blocked      map[int64]bool
	blockedUsers map[int64]int64
	mu           sync.Mutex
}

func NewBotcheckModule(dataDir string, supers []int64) *BotcheckModule {
	set := make(map[int64]bool, len(supers))
	for _, userID := range supers {
		set[userID] = true
	}
	return &BotcheckModule{
		path:         filepath.Join(dataDir, "ondemand", "database", "unibot.json"),
		supers:       set,
		blocked:      make(map[int64]bool),
		blockedUsers: make(map[int64]int64),
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

	// 服务启动时 OneBot 反向连接可能尚未建立；失败时按分钟重试，
	// 避免首次扫描失败后一直等到下一次日周期。
	retryTicker := time.NewTicker(time.Minute)
	defer retryTicker.Stop()
	for {
		if m.refreshGroups(ctx) {
			break
		}
		select {
		case <-ctx.Done():
			return
		case <-retryTicker.C:
		}
	}

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
// 群成员扫描由后台 refreshGroups 完成；消息处理路径只读取缓存，避免在
// OneBot WebSocket 读循环中同步等待自身 API 响应。
func (m *BotcheckModule) CheckGroup(_ context.Context, event onebot.MessageEvent) (bool, *onebot.ActionRequest) {
	if !event.IsGroup() {
		return false, nil
	}
	m.mu.Lock()
	blocked := m.blocked[event.GroupID]
	m.mu.Unlock()
	return blocked, nil
}

type botcheckHit struct{ groupID, userID int64 }

func (m *BotcheckModule) scanReport(_ context.Context) []botcheckHit {
	m.mu.Lock()
	defer m.mu.Unlock()
	hits := make([]botcheckHit, 0, len(m.blockedUsers))
	for groupID, userID := range m.blockedUsers {
		hits = append(hits, botcheckHit{groupID: groupID, userID: userID})
	}
	sort.Slice(hits, func(i, j int) bool {
		if hits[i].groupID == hits[j].groupID {
			return hits[i].userID < hits[j].userID
		}
		return hits[i].groupID < hits[j].groupID
	})
	return hits
}

func (m *BotcheckModule) refreshGroups(ctx context.Context) bool {
	m.mu.Lock()
	client := m.client
	m.mu.Unlock()
	if client == nil {
		return false
	}

	listCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	data, err := client.Call(listCtx, "get_group_list", map[string]any{})
	cancel()
	if err != nil {
		return false
	}
	var groups []struct {
		GroupID int64 `json:"group_id"`
	}
	if json.Unmarshal(data, &groups) != nil {
		return false
	}

	m.mu.Lock()
	state, err := m.loadLocked()
	m.mu.Unlock()
	if err != nil {
		return false
	}
	known := make(map[int64]bool, len(state.Unibot))
	for _, id := range state.Unibot {
		known[id] = true
	}

	for _, group := range groups {
		memberCtx, memberCancel := context.WithTimeout(ctx, 5*time.Second)
		members, memberErr := m.groupMembers(memberCtx, client, group.GroupID)
		memberCancel()
		if memberErr != nil {
			continue
		}
		found := int64(0)
		for _, id := range members {
			if known[id] {
				found = id
				break
			}
		}

		m.mu.Lock()
		wasBlocked := m.blocked[group.GroupID]
		if found != 0 {
			m.blocked[group.GroupID] = true
			m.blockedUsers[group.GroupID] = found
		} else {
			delete(m.blocked, group.GroupID)
			delete(m.blockedUsers, group.GroupID)
		}
		m.mu.Unlock()

		if found != 0 && !wasBlocked {
			action := onebot.ReplyText(
				onebot.MessageEvent{MessageType: "group", GroupID: group.GroupID},
				fmt.Sprintf("自动检测：群内已有unibot分布式(%d)，已关闭 Go 烧烤相关功能(需再次开启请联系master)", found),
				false,
			)
			_ = client.SendContext(ctx, action)
		}
	}
	return true
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
