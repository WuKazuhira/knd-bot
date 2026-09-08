package pjsk

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"strconv"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/gameapi"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/settings"
	"github.com/kazuhira/go-pjsk-bot/internal/store"
)

// rankMatchGrades 段位名表，对齐 old-python rankmatchgrades。
var rankMatchGrades = map[int]string{
	1: "Beginner", 2: "Bronze", 3: "Silver", 4: "Gold",
	5: "Platinum", 6: "Diamond", 7: "Master",
}

func serverCode(serverType int) string {
	switch serverType {
	case 1:
		return "tw"
	case 2:
		return "cn"
	default:
		return "jp"
	}
}

// currentRankMatch 读取 rankMatchSeasons.json，返回当前赛季 id（对齐 currentrankmatch）。
func currentRankMatch(md *masterdata.Loader, serverType int, nowMS int64) int {
	seasons, err := md.Load("rankMatchSeasons.json", serverType)
	if err != nil || len(seasons) == 0 {
		return 0
	}
	for _, s := range seasons {
		start := int64(intField(s, "startAt"))
		end := int64(intField(s, "closedAt"))
		if start < nowMS && nowMS < end {
			return intField(s, "id")
		}
	}
	return intField(seasons[len(seasons)-1], "id")
}

// RkModule 实现排位查询（rk），输出文本（过长时交 pjsk-draw 文本转图）。
type RkModule struct {
	api      *gameapi.Client
	md       *masterdata.Loader
	store    *store.Store
	settings *settings.Settings
	draw     *draw.Client
	nowMS    func() int64
}

// NewRkModule 创建 rk 模块。
func NewRkModule(api *gameapi.Client, md *masterdata.Loader, s *store.Store, set *settings.Settings, d *draw.Client, nowMS func() int64) *RkModule {
	return &RkModule{api: api, md: md, store: s, settings: set, draw: d, nowMS: nowMS}
}

// Register 注册 rk 指令。
func (m *RkModule) Register(r *router.Router) {
	r.Register("rk", nil, m.handle)
}

func (m *RkModule) handle(ctx context.Context, req router.Request) *onebot.ActionRequest {
	base := m.settings.RankMatchAPIBaseURL()
	if base == "" {
		return onebot.ReplyText(req.Event, "排位查询 API 未配置", false)
	}
	rankmatchid := currentRankMatch(m.md, int(req.Server), m.nowMS())
	apiURL := fmt.Sprintf("%s/%s/rank-match-season/%d/ranking", base, serverCode(int(req.Server)), rankmatchid)

	arg := digitsOnly(req.Arg)
	params := url.Values{}
	if arg == "" {
		// 无参数：取 at 目标或发送者的绑定 uid
		if m.store == nil {
			return onebot.ReplyText(req.Event, errNotBind, true)
		}
		qid := req.Event.UserID
		ats := req.Event.Message.AtTargets()
		if len(ats) > 0 && ats[0] != req.Event.SelfID {
			qid = ats[0]
		}
		uid, isPrivate, exists, err := m.store.GetUserBind(ctx, qid, int(req.Server))
		if err != nil || !exists {
			who := "你"
			if qid != req.Event.UserID {
				who = "用户"
			}
			return onebot.ReplyText(req.Event, who+"还没有绑定"+req.Server.Name()+"哦，国服/台服指令请加cn/tw前缀，日服无需前缀", true)
		}
		if isPrivate && qid != req.Event.UserID {
			return onebot.ReplyText(req.Event, errRefused, true)
		}
		params.Set("targetUserId", strconv.FormatInt(uid, 10))
	} else {
		// 有数字参数：长度 >8 视为 uid，否则视为排名
		if len(arg) > 8 {
			params.Set("targetUserId", arg)
		} else {
			params.Set("targetRank", arg)
		}
	}

	data, err := m.api.Get(ctx, apiURL+"?"+params.Encode())
	if err != nil {
		return onebot.ReplyText(req.Event, apiErrText(err), true)
	}
	text, errMsg := formatRankMatch(data)
	if errMsg != "" {
		return onebot.ReplyText(req.Event, errMsg, true)
	}
	// 文本较短直接发；OneBot 发送失败的兜底转图由调用侧无法感知，这里统一直接发文本。
	return onebot.ReplyText(req.Event, text, false)
}

// formatRankMatch 解析排位 API 响应并格式化成文本，对齐 old-python 逻辑。
// 返回 (文本, 错误文案)；错误文案非空时应直接回复。
func formatRankMatch(raw []byte) (string, string) {
	var resp struct {
		Rankings []struct {
			Rank                int `json:"rank"`
			UserRankMatchSeason struct {
				RankMatchTierID        int `json:"rankMatchTierId"`
				TierPoint              int `json:"tierPoint"`
				WinCount               int `json:"winCount"`
				LoseCount              int `json:"loseCount"`
				DrawCount              int `json:"drawCount"`
				PenaltyCount           int `json:"penaltyCount"`
				MaxConsecutiveWinCount int `json:"maxConsecutiveWinCount"`
			} `json:"userRankMatchSeason"`
		} `json:"rankings"`
		UpdateTime string `json:"updateTime"`
	}
	if err := json.Unmarshal(raw, &resp); err != nil {
		return "", errBug
	}
	if len(resp.Rankings) == 0 {
		return "", "未参加当期排位赛"
	}
	r := resp.Rankings[0]
	rk := r.UserRankMatchSeason
	grade := (rk.RankMatchTierID-1)/4 + 1
	if grade > 7 {
		grade = 7
	}
	gradename := rankMatchGrades[grade]
	kurasu := rk.RankMatchTierID - 4*(grade-1)
	if kurasu == 0 {
		kurasu = 4
	}
	total := rk.WinCount + rk.LoseCount
	winrate := 0.0
	if total > 0 {
		winrate = float64(rk.WinCount) / float64(total)
	}
	text := ""
	if grade == 7 {
		text += fmt.Sprintf("%s🎵×%d\n排名：%d\n", gradename, rk.TierPoint, r.Rank)
	} else {
		text += fmt.Sprintf("%sClass %d(%d/5)\n排名：%d\n", gradename, kurasu, rk.TierPoint, r.Rank)
	}
	text += fmt.Sprintf("Win %d | Draw %d | ", rk.WinCount, rk.DrawCount)
	if rk.PenaltyCount == 0 {
		text += fmt.Sprintf("Lose %d\n", rk.LoseCount)
	} else {
		text += fmt.Sprintf("Lose %d+%d\n", rk.LoseCount-rk.PenaltyCount, rk.PenaltyCount)
	}
	text += fmt.Sprintf("胜率(除去平局)：%s%%\n", strconv.FormatFloat(round2(winrate*100), 'f', -1, 64))
	text += fmt.Sprintf("最高连胜：%d\n", rk.MaxConsecutiveWinCount)
	if resp.UpdateTime != "" {
		text += fmt.Sprintf("更新时间：%s\n", resp.UpdateTime)
	}
	return text, ""
}
