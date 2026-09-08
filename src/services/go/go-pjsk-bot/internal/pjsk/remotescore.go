package pjsk

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

// RemoteScoreModule 实现「打歌分数」查询/设置（superuser），对齐 old-python
// remote._score。通过 sekai-api 的 /config/score 端点读写 auto/clear 两套
// base_score / life 配置。纯请求-响应，无后台状态。
type RemoteScoreModule struct {
	apiURL   string
	apiToken string
	supers   map[int64]bool
	http     *http.Client
}

// NewRemoteScoreModule 创建模块。apiURL 为 sekai-api 基址（SEKAI_API_URL），
// apiToken 为 SEKAI_API_TOKEN，supers 为超级用户 QQ 列表。
func NewRemoteScoreModule(apiURL, apiToken string, supers []int64) *RemoteScoreModule {
	set := make(map[int64]bool, len(supers))
	for _, s := range supers {
		set[s] = true
	}
	return &RemoteScoreModule{
		apiURL:   strings.TrimRight(apiURL, "/"),
		apiToken: apiToken,
		supers:   set,
		http:     &http.Client{Timeout: 90 * time.Second},
	}
}

// Register 注册「打歌分数」「设置打歌分数」及别名。
func (m *RemoteScoreModule) Register(r *router.Router) {
	r.Register("打歌分数", []string{"分数配置", "pjsk分数"}, m.handleQuery)
	r.Register("设置打歌分数", []string{"修改打歌分数", "设置分数"}, m.handleSet)
}

func (m *RemoteScoreModule) isSuper(e onebot.MessageEvent) bool { return m.supers[e.UserID] }

// scoreConfig 是 /config/score 返回体中的 config 字段。
type scoreConfig struct {
	Auto  scoreFlow `json:"auto"`
	Clear scoreFlow `json:"clear"`
}

type scoreFlow struct {
	BaseScore *int `json:"baseScore"`
	Life      *int `json:"life"`
}

func (f scoreFlow) fmtField(p *int) string {
	if p == nil {
		return "<nil>"
	}
	return strconv.Itoa(*p)
}

func formatScoreConfig(cfg scoreConfig) string {
	return "当前打歌分数配置：\n" +
		fmt.Sprintf("【单独打歌 auto】base_score=%s life=%s\n", cfg.Auto.fmtField(cfg.Auto.BaseScore), cfg.Auto.fmtField(cfg.Auto.Life)) +
		fmt.Sprintf("【顺序清谱 clear】base_score=%s life=%s", cfg.Clear.fmtField(cfg.Clear.BaseScore), cfg.Clear.fmtField(cfg.Clear.Life))
}

// scoreGet 查询当前配置。
func (m *RemoteScoreModule) scoreGet(ctx context.Context) (scoreConfig, error) {
	var out struct {
		Config scoreConfig `json:"config"`
	}
	if m.apiToken == "" {
		return scoreConfig{}, fmt.Errorf("SEKAI_API_TOKEN 未配置")
	}
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, m.apiURL+"/config/score", nil)
	req.Header.Set("X-Haruki-Sekai-Token", m.apiToken)
	resp, err := m.http.Do(req)
	if err != nil {
		return scoreConfig{}, fmt.Errorf("查询分数配置失败：%v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		return scoreConfig{}, fmt.Errorf("查询分数配置失败（%d）：%s", resp.StatusCode, strings.TrimSpace(string(body)))
	}
	if err := json.Unmarshal(body, &out); err != nil {
		return scoreConfig{}, fmt.Errorf("查询分数配置失败：解析响应出错")
	}
	return out.Config, nil
}

// scoreUpdate 修改配置；reset=true 时清空覆盖回到 config.yaml。
func (m *RemoteScoreModule) scoreUpdate(ctx context.Context, flow string, baseScore, life *int, reset bool) (scoreConfig, error) {
	if m.apiToken == "" {
		return scoreConfig{}, fmt.Errorf("SEKAI_API_TOKEN 未配置")
	}
	payload := map[string]any{"flow": flow}
	if reset {
		payload["reset"] = true
	}
	if baseScore != nil {
		payload["baseScore"] = *baseScore
	}
	if life != nil {
		payload["life"] = *life
	}
	raw, _ := json.Marshal(payload)
	req, _ := http.NewRequestWithContext(ctx, http.MethodPost, m.apiURL+"/config/score", bytes.NewReader(raw))
	req.Header.Set("X-Haruki-Sekai-Token", m.apiToken)
	req.Header.Set("Content-Type", "application/json")
	resp, err := m.http.Do(req)
	if err != nil {
		return scoreConfig{}, fmt.Errorf("修改分数配置失败：%v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		msg := strings.TrimSpace(string(body))
		var e struct {
			Error string `json:"error"`
		}
		if json.Unmarshal(body, &e) == nil && e.Error != "" {
			msg = e.Error
		}
		return scoreConfig{}, fmt.Errorf("%s", msg)
	}
	var out struct {
		Config scoreConfig `json:"config"`
	}
	if err := json.Unmarshal(body, &out); err != nil {
		return scoreConfig{}, fmt.Errorf("修改分数配置失败：解析响应出错")
	}
	return out.Config, nil
}

func (m *RemoteScoreModule) handleQuery(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if !m.isSuper(req.Event) {
		return nil
	}
	cfg, err := m.scoreGet(ctx)
	if err != nil {
		return onebot.ReplyText(req.Event, "查询失败："+err.Error(), false)
	}
	return onebot.ReplyText(req.Event, formatScoreConfig(cfg), false)
}

var (
	reScoreClear = regexp.MustCompile(`(?i)(clear|清谱|顺序)`)
	reScoreReset = regexp.MustCompile(`(重置|reset|还原)`)
	reAllDigits  = regexp.MustCompile(`\d+`)
)

// pickNumber 在 text 中按字段名列表匹配 `字段[=＝:：]数字`，返回首个命中值。
func pickNumber(text string, keys ...string) *int {
	for _, key := range keys {
		re := regexp.MustCompile(key + `\s*[=＝:：]?\s*(\d+)`)
		if mm := re.FindStringSubmatch(text); mm != nil {
			if n, err := strconv.Atoi(mm[1]); err == nil {
				return &n
			}
		}
	}
	return nil
}

func (m *RemoteScoreModule) handleSet(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if !m.isSuper(req.Event) {
		return nil
	}
	text := strings.TrimSpace(req.Arg)
	if text == "" {
		return onebot.ReplyText(req.Event,
			"用法：设置打歌分数 [clear] 分数=1900000 生命=1000\n"+
				"  · 不写 clear 则修改单独打歌（auto）\n"+
				"  · 分数/生命可只写其一\n"+
				"  · 设置打歌分数 重置    恢复配置文件里的值", false)
	}

	flow := "auto"
	if reScoreClear.MatchString(text) {
		flow = "clear"
		text = strings.TrimSpace(reScoreClear.ReplaceAllString(text, ""))
	}

	if reScoreReset.MatchString(text) {
		cfg, err := m.scoreUpdate(ctx, flow, nil, nil, true)
		if err != nil {
			return onebot.ReplyText(req.Event, "重置失败："+err.Error(), false)
		}
		return onebot.ReplyText(req.Event, "✅ 已恢复配置文件里的值\n"+formatScoreConfig(cfg), false)
	}

	baseScore := pickNumber(text, "分数", "base_score", "baseScore", "score")
	life := pickNumber(text, "生命", "血量", "life")

	// 没写字段名时，按"第一个数字是分数、第二个是生命"兜底。
	if baseScore == nil && life == nil {
		nums := reAllDigits.FindAllString(text, -1)
		if len(nums) == 0 {
			return onebot.ReplyText(req.Event, "没识别到数字，用法：设置打歌分数 分数=1900000 生命=1000", false)
		}
		if n, err := strconv.Atoi(nums[0]); err == nil {
			baseScore = &n
		}
		if len(nums) > 1 {
			if n, err := strconv.Atoi(nums[1]); err == nil {
				life = &n
			}
		}
	}

	cfg, err := m.scoreUpdate(ctx, flow, baseScore, life, false)
	if err != nil {
		return onebot.ReplyText(req.Event, "修改失败："+err.Error(), false)
	}
	changed := make([]string, 0, 2)
	if baseScore != nil {
		changed = append(changed, "base_score="+strconv.Itoa(*baseScore))
	}
	if life != nil {
		changed = append(changed, "life="+strconv.Itoa(*life))
	}
	return onebot.ReplyText(req.Event,
		fmt.Sprintf("✅ 已修改 %s：%s\n%s", flow, strings.Join(changed, " "), formatScoreConfig(cfg)), false)
}
