package pjsk

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

// CnMsrModule 管理 CN 服 MSR 群白名单（superuser 专用），对齐 old-python
// mysekai 的 cnmsr启用/禁用/白名单 三条指令。白名单文件与 Python 侧共享，
// 位于 staticDir/cn_msr_allowed_groups.json，格式 {"groups": [sorted ints]}。
type CnMsrModule struct {
	staticDir string
	supers    map[int64]bool
	mu        sync.Mutex
}

// NewCnMsrModule 创建白名单管理模块。supers 为超级用户 QQ 列表。
func NewCnMsrModule(staticDir string, supers []int64) *CnMsrModule {
	set := make(map[int64]bool, len(supers))
	for _, s := range supers {
		set[s] = true
	}
	return &CnMsrModule{staticDir: staticDir, supers: set}
}

// cnMsrGroupsFile 返回 CN MSR 白名单文件路径（与 Python 共享）。
func cnMsrGroupsFile(staticDir string) string {
	return filepath.Join(staticDir, "cn_msr_allowed_groups.json")
}

// loadCnMsrGroups 读取 CN MSR 白名单群号集合，兼容 [ints] 与 {"groups":[ints]} 两种格式。
func loadCnMsrGroups(staticDir string) map[int64]bool {
	set := map[int64]bool{}
	raw, err := os.ReadFile(cnMsrGroupsFile(staticDir))
	if err != nil {
		return set
	}
	var obj struct {
		Groups []int64 `json:"groups"`
	}
	if json.Unmarshal(raw, &obj) == nil && obj.Groups != nil {
		for _, g := range obj.Groups {
			set[g] = true
		}
		return set
	}
	var arr []int64
	if json.Unmarshal(raw, &arr) == nil {
		for _, g := range arr {
			set[g] = true
		}
	}
	return set
}

// assertCnMsrAllowed 校验 cn 服指令的群白名单：仅 pjsk_type==2（cn）需白名单，
// 其它服直接放行。对齐 assert_cn_msr_allowed。允许返回空串，否则返回错误提示。
func assertCnMsrAllowed(staticDir string, groupID int64, serverType int) string {
	if serverType != 2 {
		return ""
	}
	if groupID == 0 {
		return "CN 服 MSR 系列指令仅在已加入白名单的群内可用"
	}
	if !loadCnMsrGroups(staticDir)[groupID] {
		return "当前群暂未加入 CN 服 MSR 白名单，请联系管理员开通"
	}
	return ""
}

// Register 注册 cnmsr启用 / cnmsr禁用 / cnmsr白名单 指令。
func (m *CnMsrModule) Register(r *router.Router) {
	r.Register("cnmsr启用", nil, m.handleEnable)
	r.Register("cnmsr禁用", nil, m.handleDisable)
	r.Register("cnmsr白名单", nil, m.handleList)
}

func (m *CnMsrModule) groupsFile() string {
	return filepath.Join(m.staticDir, "cn_msr_allowed_groups.json")
}

// loadGroups 读取白名单，兼容 [ints] 与 {"groups": [ints]} 两种格式。
func (m *CnMsrModule) loadGroups() (map[int64]bool, error) {
	raw, err := os.ReadFile(m.groupsFile())
	if err != nil {
		if os.IsNotExist(err) {
			return map[int64]bool{}, nil
		}
		return nil, err
	}
	set := map[int64]bool{}
	// 先试对象格式
	var obj struct {
		Groups []int64 `json:"groups"`
	}
	if json.Unmarshal(raw, &obj) == nil && obj.Groups != nil {
		for _, g := range obj.Groups {
			set[g] = true
		}
		return set, nil
	}
	// 再试裸数组格式
	var arr []int64
	if json.Unmarshal(raw, &arr) == nil {
		for _, g := range arr {
			set[g] = true
		}
	}
	return set, nil
}

// saveGroups 以 {"groups": [sorted ints]} 写回，与 Python 侧保持一致。
func (m *CnMsrModule) saveGroups(set map[int64]bool) error {
	groups := make([]int64, 0, len(set))
	for g := range set {
		groups = append(groups, g)
	}
	sort.Slice(groups, func(i, j int) bool { return groups[i] < groups[j] })
	payload := struct {
		Groups []int64 `json:"groups"`
	}{Groups: groups}
	data, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(m.staticDir, 0o755); err != nil {
		return err
	}
	return os.WriteFile(m.groupsFile(), data, 0o644)
}

// isSuper 判定事件发送者是否超级用户。
func (m *CnMsrModule) isSuper(e onebot.MessageEvent) bool {
	return m.supers[e.UserID]
}

// parseGroupID 解析群号参数，对齐 _parse_group_id。
func parseGroupID(arg string) (int64, string) {
	arg = strings.TrimSpace(arg)
	if arg == "" {
		return 0, "请提供群号，例如「/cnmsr启用 123456」"
	}
	n, err := strconv.ParseInt(arg, 10, 64)
	if err != nil {
		return 0, "群号必须是数字"
	}
	return n, ""
}

func (m *CnMsrModule) handleEnable(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if !m.isSuper(req.Event) {
		return nil
	}
	gid, errMsg := parseGroupID(req.Arg)
	if errMsg != "" {
		return onebot.ReplyText(req.Event, errMsg, true)
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	groups, err := m.loadGroups()
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	if groups[gid] {
		return onebot.ReplyText(req.Event, "群 "+strconv.FormatInt(gid, 10)+" 已在 CN MSR 白名单中", false)
	}
	groups[gid] = true
	if err := m.saveGroups(groups); err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyText(req.Event, "已将群 "+strconv.FormatInt(gid, 10)+" 加入 CN MSR 白名单", false)
}

func (m *CnMsrModule) handleDisable(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if !m.isSuper(req.Event) {
		return nil
	}
	gid, errMsg := parseGroupID(req.Arg)
	if errMsg != "" {
		return onebot.ReplyText(req.Event, errMsg, true)
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	groups, err := m.loadGroups()
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	if !groups[gid] {
		return onebot.ReplyText(req.Event, "群 "+strconv.FormatInt(gid, 10)+" 不在 CN MSR 白名单中", false)
	}
	delete(groups, gid)
	if err := m.saveGroups(groups); err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyText(req.Event, "已将群 "+strconv.FormatInt(gid, 10)+" 移出 CN MSR 白名单", false)
}

func (m *CnMsrModule) handleList(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if !m.isSuper(req.Event) {
		return nil
	}
	m.mu.Lock()
	groups, err := m.loadGroups()
	m.mu.Unlock()
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	if len(groups) == 0 {
		return onebot.ReplyText(req.Event, "CN MSR 白名单为空", false)
	}
	ids := make([]int64, 0, len(groups))
	for g := range groups {
		ids = append(ids, g)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	lines := make([]string, 0, len(ids)+1)
	lines = append(lines, "CN MSR 白名单：")
	for _, g := range ids {
		lines = append(lines, strconv.FormatInt(g, 10))
	}
	return onebot.ReplyText(req.Event, strings.Join(lines, "\n"), false)
}
