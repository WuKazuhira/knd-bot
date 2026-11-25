package pjsk

import (
	"context"
	"fmt"
	"net/url"
	"strings"

	"github.com/kazuhira/go-pjsk-bot/internal/gameapi"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/profile"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/settings"
)

type ArrestModule struct {
	fetcher  *profile.Fetcher
	api      *gameapi.Client
	md       *masterdata.Loader
	resolver *UserResolver
	settings *settings.Settings
	nowMS    func() int64
}

func NewArrestModule(f *profile.Fetcher, api *gameapi.Client, md *masterdata.Loader, resolver *UserResolver, set *settings.Settings, nowMS func() int64) *ArrestModule {
	return &ArrestModule{fetcher: f, api: api, md: md, resolver: resolver, settings: set, nowMS: nowMS}
}

func (m *ArrestModule) Register(r *router.Router) {
	r.RegisterNumericSuffix("逮捕", nil, m.handle)
}

func (m *ArrestModule) handle(ctx context.Context, req router.Request) *onebot.ActionRequest {
	userid, isPrivate, errMsg := m.resolver.Resolve(ctx, req)
	if errMsg != "" {
		return onebot.ReplyText(req.Event, errMsg, true)
	}
	p, err := m.fetcher.GetSuite(ctx, userid, int(req.Server))
	if err != nil {
		return onebot.ReplyText(req.Event, apiErrText(err), true)
	}

	text := m.buildProgressText(p, userid, isPrivate)
	if rk := m.queryRankText(ctx, int(req.Server), userid); rk != "" {
		text += "\n\n" + rk
	}
	return onebot.ReplyText(req.Event, text, false)
}

func (m *ArrestModule) buildProgressText(p *profile.Profile, userid string, isPrivate bool) string {
	var b strings.Builder
	if isPrivate {
		fmt.Fprintf(&b, "%s\n", p.Name)
	} else {
		fmt.Fprintf(&b, "%s - %s\n", p.Name, userid)
	}
	fmt.Fprintf(&b, "expert进度:FC %d/%d AP%d/%d\n",
		p.FullCombo[3], p.Clear[3], p.FullPerfect[3], p.Clear[3])
	fmt.Fprintf(&b, "master进度:FC %d/%d AP%d/%d\n",
		p.FullCombo[4], p.Clear[4], p.FullPerfect[4], p.Clear[4])

	var ap33, fc33, total33 int
	for lv := 33; lv <= 37; lv++ {
		if e := p.MasterScore[lv]; e != nil {
			ap33 += e[0]
			fc33 += e[1]
			total33 += e[3]
		}
	}
	if ap33 != 0 {
		fmt.Fprintf(&b, "\nLv.33及以上AP进度：%d/%d", ap33, total33)
	}
	if fc33 != 0 {
		fmt.Fprintf(&b, "\nLv.33及以上FC进度：%d/%d", fc33, total33)
	}
	if e := p.MasterScore[32]; e != nil {
		if e[0] != 0 {
			fmt.Fprintf(&b, "\nLv.32AP进度：%d/%d", e[0], e[3])
		}
		if e[1] != 0 {
			fmt.Fprintf(&b, "\nLv.32FC进度：%d/%d", e[1], e[3])
		}
	}
	return b.String()
}

func (m *ArrestModule) queryRankText(ctx context.Context, serverType int, userid string) string {
	if m.settings == nil {
		return ""
	}
	base := m.settings.RankMatchAPIBaseURL()
	if base == "" {
		return ""
	}
	rankmatchid := currentRankMatch(m.md, serverType, m.nowMS())
	if rankmatchid == 0 {
		return ""
	}
	params := url.Values{}
	params.Set("targetUserId", userid)
	apiURL := fmt.Sprintf("%s/%s/rank-match-season/%d/ranking?%s",
		base, serverCode(serverType), rankmatchid, params.Encode())
	data, err := m.api.Get(ctx, apiURL)
	if err != nil {
		return ""
	}
	text, errMsg := formatRankMatch(data)
	if errMsg != "" {
		return ""
	}
	return text
}
