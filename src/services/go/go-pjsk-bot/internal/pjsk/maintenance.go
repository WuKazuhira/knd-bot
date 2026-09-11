package pjsk

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/session"
	"gopkg.in/yaml.v3"
)

const updateStateTTL = 10 * time.Minute

var updateGroups = map[string][]string{
	"1": {"events.json", "worldBlooms.json", "rankMatchSeasons.json", "cheerfulCarnivalTeams.json", "bondsHonors.json", "virtualLives.json"},
	"2": {"eventCards.json", "eventDeckBonuses.json", "gameCharacterUnits.json", "eventCardBonusLimits.json", "eventHonorBonuses.json", "eventSkillScoreUpLimits.json"},
	"3": {"cardCostume3ds.json", "costume3ds.json", "gameCharacters.json", "cards.json", "cardEpisodes.json"},
	"4": {"honors.json", "honorGroups.json"},
	"5": {"musicVocals.json", "outsideCharacters.json"},
	"6": {"musicDifficulties.json", "musics.json"},
}

var translationFiles = []string{"music_titles", "event_name", "card_prefix", "cheerful_carnival_teams"}

// UpdateModule 实现 superuser 维护命令：难度表、主数据更新、活动号查询和资源去重。
type UpdateModule struct {
	md      *masterdata.Loader
	dataDir string
	helper  string
	gen     *DifficultyGenerator
	supers  map[int64]bool
	http    *http.Client
	pending *session.Manager
}

func NewUpdateModule(md *masterdata.Loader, dataDir, helperURL string, supers []int64) *UpdateModule {
	set := make(map[int64]bool, len(supers))
	for _, userID := range supers {
		set[userID] = true
	}
	return &UpdateModule{
		md:      md,
		dataDir: dataDir,
		helper:  strings.TrimRight(helperURL, "/"),
		gen:     NewDifficultyGenerator(md, dataDir),
		supers:  set,
		http:    &http.Client{Timeout: 90 * time.Second},
		pending: session.New(time.Minute),
	}
}

func (m *UpdateModule) Close() { m.pending.Close() }

func (m *UpdateModule) Register(r *router.Router) {
	r.Register("生成难度csv", []string{"生成难度json"}, m.handleGenerateDifficulty)
	r.Register("pjsk更新", nil, m.handleUpdate)
	r.Register("pjsk活动更新", nil, m.handleEventUpdate)
	r.Register("pjsk数据去重", []string{"pjsk资源去重"}, m.handleDedup)
}

func (m *UpdateModule) isSuper(event onebot.MessageEvent) bool { return m.supers[event.UserID] }

func (m *UpdateModule) denied(event onebot.MessageEvent) *onebot.ActionRequest {
	return onebot.ReplyText(event, "仅超级用户可用", true)
}

func (m *UpdateModule) handleGenerateDifficulty(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if !m.isSuper(req.Event) {
		return m.denied(req.Event)
	}
	var count int
	var err error
	if strings.Contains(strings.ToLower(req.RawCmd), "json") {
		count, err = m.gen.GenerateJSON(int(req.Server))
	} else {
		count, err = m.gen.GenerateCSV(int(req.Server))
	}
	if err != nil {
		return onebot.ReplyText(req.Event, "生成难度表失败: "+err.Error(), true)
	}
	return onebot.ReplyText(req.Event, fmt.Sprintf("成功%s（处理 %d 条）", req.RawCmd, count), true)
}

func updateKey(event onebot.MessageEvent) string {
	return "pjsk-update:" + strconv.FormatInt(event.UserID, 10) + ":" + strconv.FormatInt(event.GroupID, 10)
}

func (m *UpdateModule) handleUpdate(_ context.Context, req router.Request) *onebot.ActionRequest {
	if !m.isSuper(req.Event) {
		return m.denied(req.Event)
	}
	if strings.TrimSpace(req.Arg) == "" {
		m.pending.Set(updateKey(req.Event), int(req.Server), updateStateTTL)
		return onebot.ReplyText(req.Event, "请发送需要更新的资源类型序号（0-7）", true)
	}
	return m.executeUpdate(req.Event, int(req.Server), req.Arg)
}

// HandleMessage 处理 pjsk更新 的下一条纯文本消息，供主消息 handler 在路由前调用。
func (m *UpdateModule) HandleMessage(event onebot.MessageEvent) *onebot.ActionRequest {
	value, ok := m.pending.Get(updateKey(event))
	if !ok {
		return nil
	}
	m.pending.Delete(updateKey(event))
	if !m.isSuper(event) {
		return m.denied(event)
	}
	server, ok := value.(int)
	if !ok {
		return onebot.ReplyText(event, "更新状态已失效，请重新发送 pjsk更新", true)
	}
	return m.executeUpdate(event, server, event.Message.PlainText())
}

func (m *UpdateModule) executeUpdate(event onebot.MessageEvent, server int, arg string) *onebot.ActionRequest {
	groups, err := parseUpdateGroups(arg)
	if err != nil {
		return onebot.ReplyText(event, "参数有误，操作取消", true)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	lines := []string{fmt.Sprintf("%s PJSK 更新完成：", serverCode(server))}
	for _, group := range groups {
		if group == "7" {
			if err := m.refreshTranslations(ctx, server); err != nil {
				lines = append(lines, "7 游戏翻译资源：失败（"+err.Error()+"）")
			} else {
				lines = append(lines, "7 游戏翻译资源：完成")
			}
			continue
		}
		files := updateGroups[group]
		failed := 0
		for _, file := range files {
			if err := m.refreshMasterdata(ctx, server, file); err != nil {
				failed++
			}
		}
		if failed == 0 {
			lines = append(lines, fmt.Sprintf("%s：完成（%d 个文件）", updateGroupName(group), len(files)))
		} else {
			lines = append(lines, fmt.Sprintf("%s：完成 %d/%d 个文件，失败 %d 个", updateGroupName(group), len(files)-failed, len(files), failed))
		}
	}
	return onebot.ReplyText(event, strings.Join(lines, "\n"), true)
}

func parseUpdateGroups(arg string) ([]string, error) {
	clean := strings.ReplaceAll(strings.ToLower(strings.TrimSpace(arg)), " ", "")
	if clean == "0" {
		return []string{"1", "2", "3", "4", "5", "6", "7"}, nil
	}
	seen := make(map[string]bool)
	groups := make([]string, 0, len(clean))
	for _, r := range clean {
		group := string(r)
		if _, ok := updateGroups[group]; !ok && group != "7" {
			return nil, fmt.Errorf("invalid update group %q", group)
		}
		if !seen[group] {
			seen[group] = true
			groups = append(groups, group)
		}
	}
	if len(groups) == 0 {
		return nil, fmt.Errorf("empty update groups")
	}
	sort.Strings(groups)
	return groups, nil
}

func updateGroupName(group string) string {
	return map[string]string{
		"1": "活动信息资源", "2": "活动相关资源", "3": "卡面相关资源",
		"4": "个人信息资源", "5": "谱面信息资源", "6": "歌曲信息资源",
		"7": "游戏翻译资源",
	}[group]
}

func (m *UpdateModule) refreshMasterdata(ctx context.Context, server int, file string) error {
	if m.helper == "" {
		return fmt.Errorf("helper URL 未配置")
	}
	endpoint := m.helper + "/masterdata/refresh?region=" + url.QueryEscape(serverCode(server)) + "&file=" + url.QueryEscape(file)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, nil)
	if err != nil {
		return err
	}
	resp, err := m.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return fmt.Errorf("helper HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(body)))
	}
	return nil
}

func (m *UpdateModule) refreshTranslations(ctx context.Context, server int) error {
	if m.helper != "" {
		for _, name := range translationFiles {
			endpoint := m.helper + "/translation/refresh?region=" + url.QueryEscape(serverCode(server)) + "&file=" + url.QueryEscape(name)
			req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, nil)
			if err != nil {
				return err
			}
			resp, err := m.http.Do(req)
			if err != nil {
				return err
			}
			resp.Body.Close()
			if resp.StatusCode != http.StatusOK {
				return fmt.Errorf("helper translation HTTP %d", resp.StatusCode)
			}
		}
		return nil
	}
	path := filepath.Join(m.dataDir, "ondemand", serverCode(server), "translate.yaml")
	translations := map[string]any{}
	if raw, err := os.ReadFile(path); err == nil {
		_ = yaml.Unmarshal(raw, &translations)
	}
	for _, name := range translationFiles {
		endpoint := "https://raw.githubusercontent.com/Sekai-World/sekai-i18n/main/zh-TW/" + name + ".json"
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
		if err != nil {
			return err
		}
		resp, err := m.http.Do(req)
		if err != nil {
			return err
		}
		body, readErr := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
		resp.Body.Close()
		if readErr != nil {
			return readErr
		}
		if resp.StatusCode != http.StatusOK {
			return fmt.Errorf("translation %s HTTP %d", name, resp.StatusCode)
		}
		var data any
		if err := json.Unmarshal(body, &data); err != nil {
			return err
		}
		translations[name] = data
	}
	encoded, err := yaml.Marshal(translations)
	if err != nil {
		return err
	}
	return writeAtomic(path, encoded)
}

func (m *UpdateModule) handleEventUpdate(_ context.Context, req router.Request) *onebot.ActionRequest {
	if !m.isSuper(req.Event) {
		return m.denied(req.Event)
	}
	if arg := strings.TrimSpace(req.Arg); arg != "" {
		if _, err := strconv.Atoi(arg); err == nil {
			return onebot.ReplyText(req.Event, fmt.Sprintf("%s pjsk暂不支持手动指定活动号", serverCode(int(req.Server))), true)
		}
	}
	events, err := m.md.Load("events.json", int(req.Server))
	if err != nil {
		return onebot.ReplyText(req.Event, fmt.Sprintf("%s pjsk更新活动号失败", serverCode(int(req.Server))), true)
	}
	id := currentEventID(events, time.Now().UnixMilli())
	if id == 0 {
		return onebot.ReplyText(req.Event, fmt.Sprintf("%s pjsk更新活动号失败", serverCode(int(req.Server))), true)
	}
	return onebot.ReplyText(req.Event, fmt.Sprintf("%s pjsk活动号当前为 %d", serverCode(int(req.Server)), id), true)
}

func (m *UpdateModule) handleDedup(_ context.Context, req router.Request) *onebot.ActionRequest {
	if !m.isSuper(req.Event) {
		return m.denied(req.Event)
	}
	tokens := strings.Fields(strings.ToLower(req.Arg))
	apply := false
	regions := make([]string, 0, 2)
	for _, token := range tokens {
		switch token {
		case "执行", "apply", "run":
			apply = true
		case "cn", "tw":
			regions = append(regions, token)
		}
	}
	if len(regions) == 0 {
		regions = []string{"cn", "tw"}
	}
	stats, err := deduplicate(filepath.Join(m.dataDir, "ondemand"), regions, apply)
	if err != nil {
		return onebot.ReplyText(req.Event, "PJSK 资源去重失败: "+err.Error(), true)
	}
	mode := "预览"
	if apply {
		mode = "已执行"
	}
	lines := []string{"PJSK 资源去重" + mode + "："}
	for _, region := range regions {
		item := stats[region]
		lines = append(lines, fmt.Sprintf("%s：扫描%d，候选%d，重复%d，可节省%s，硬链接%d，软链接%d，跳过%d，错误%d", strings.ToUpper(region), item.Scanned, item.Candidates, item.Duplicates, formatBytes(item.SavedBytes), item.Linked, item.Symlinked, item.Skipped, item.Errors))
	}
	if !apply {
		lines = append(lines, "如需实际替换，请发送：pjsk数据去重 执行")
	}
	return onebot.ReplyText(req.Event, strings.Join(lines, "\n"), true)
}

func formatBytes(value int64) string {
	return fmt.Sprintf("%.2fMiB", float64(value)/(1024*1024))
}
