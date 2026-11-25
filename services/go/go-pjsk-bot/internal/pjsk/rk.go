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
)

var rankMatchGrades = map[int]string{
	1: "Beginner", 2: "Bronze", 3: "Silver", 4: "Gold",
	5: "Platinum", 6: "Diamond", 7: "Master",
}

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

type RkModule struct {
	api      *gameapi.Client
	md       *masterdata.Loader
	resolver *UserResolver
	settings *settings.Settings
	draw     *draw.Client
	nowMS    func() int64
}

func NewRkModule(api *gameapi.Client, md *masterdata.Loader, resolver *UserResolver, set *settings.Settings, d *draw.Client, nowMS func() int64) *RkModule {
	return &RkModule{api: api, md: md, resolver: resolver, settings: set, draw: d, nowMS: nowMS}
}

func (m *RkModule) Register(r *router.Router) {
	r.RegisterNumericSuffix("rk", nil, m.handle)
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
		uid, isPrivate, errMsg := m.resolver.Resolve(ctx, req)
		if errMsg != "" {
			return onebot.ReplyText(req.Event, errMsg, true)
		}
		if isPrivate && len(req.Event.Message.AtTargets()) > 0 {
			return onebot.ReplyText(req.Event, errRefused, true)
		}
		params.Set("targetUserId", uid)
	} else {
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
	return onebot.ReplyText(req.Event, text, false)
}

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
