package pjsk

import (
	"context"
	"encoding/base64"
	"sort"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/profile"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

// B30Module 实现 b30 查询：按谱面定数计算个人最佳 30，出图走 pjsk-draw 的 "b30" 任务。
type B30Module struct {
	fetcher  *profile.Fetcher
	md       *masterdata.Loader
	resolver *UserResolver
	draw     *draw.Client
}

// NewB30Module 创建 b30 模块。
func NewB30Module(f *profile.Fetcher, md *masterdata.Loader, resolver *UserResolver, d *draw.Client) *B30Module {
	return &B30Module{fetcher: f, md: md, resolver: resolver, draw: d}
}

// Register 注册 b30 指令。
func (m *B30Module) Register(r *router.Router) {
	r.RegisterNumericSuffix("pjsk b30", []string{"pjskb30", "烧烤b30", "烧烤 b30", "b30"}, m.handle)
}

// fcrank 计算 FC 定数：level<=32 为 ap-1.5，否则 ap-1（对齐 old-python fcrank）。
func fcrank(playLevel int, ap float64) float64 {
	if playLevel <= 32 {
		return ap - 1.5
	}
	return ap - 1
}

func (m *B30Module) handle(ctx context.Context, req router.Request) *onebot.ActionRequest {
	userid, isPrivate, errMsg := m.resolver.Resolve(ctx, req)
	if errMsg != "" {
		return onebot.ReplyText(req.Event, errMsg, true)
	}

	p, err := m.fetcher.GetSuite(ctx, userid, int(req.Server))
	if err != nil {
		return onebot.ReplyText(req.Event, apiErrText(err), true)
	}

	diffs, err := m.md.Load("musicDifficulties.json", int(req.Server))
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	constants := m.md.Constants()

	// 为每个谱面计算 AP/FC 定数，建 (musicId,difficulty) 索引。
	type diffKey struct {
		musicID int
		diff    string
	}
	index := make(map[diffKey]map[string]any, len(diffs))
	items := make([]map[string]any, 0, len(diffs))
	for _, d := range diffs {
		mid := intField(d, "musicId")
		dname := strField(d, "musicDifficulty")
		playLevel := intField(d, "playLevel")
		ap := float64(playLevel)
		hasConst := false
		if c, ok := constants[masterdata.ConstantKey{MusicID: mid, Diff: dname}]; ok {
			ap = c
			hasConst = true
		}
		item := map[string]any{
			"musicId":         mid,
			"musicDifficulty": dname,
			"playLevel":       playLevel,
			"result":          0,
			"rank":            0.0,
			"has_constant":    hasConst,
			"aplevel+":        ap,
			"fclevel+":        fcrank(playLevel, ap),
		}
		index[diffKey{mid, dname}] = item
		items = append(items, item)
	}

	// highest：按 AP 定数降序取前 30 的均值。
	sort.SliceStable(items, func(i, j int) bool {
		return items[i]["aplevel+"].(float64) > items[j]["aplevel+"].(float64)
	})
	topCount := 30
	if len(items) < topCount {
		topCount = len(items)
	}
	highest := 0.0
	for i := 0; i < topCount; i++ {
		highest += items[i]["aplevel+"].(float64)
	}
	if topCount > 0 {
		highest = round2(highest / float64(topCount))
	}

	// 用个人成绩填 result/rank：full_perfect → AP 定数；full_combo → FC 定数（取更好）。
	for _, mr := range p.MusicResults() {
		music, ok := mr.(map[string]any)
		if !ok {
			continue
		}
		mid := intField(music, "musicId")
		dname := strField(music, "musicDifficultyType")
		if dname == "" {
			dname = strField(music, "musicDifficulty")
		}
		item, ok := index[diffKey{mid, dname}]
		if !ok {
			continue
		}
		switch strField(music, "playResult") {
		case "full_perfect":
			item["result"] = 2
			item["rank"] = item["aplevel+"].(float64)
		case "full_combo":
			if item["result"].(int) < 1 {
				item["result"] = 1
				item["rank"] = item["fclevel+"].(float64)
			}
		}
	}

	// 按 rank 降序取前 30。
	sort.SliceStable(items, func(i, j int) bool {
		return items[i]["rank"].(float64) > items[j]["rank"].(float64)
	})
	top := items
	if len(top) > 30 {
		top = top[:30]
	}

	img, err := m.draw.Render(ctx, "b30", map[string]any{
		"diff":      top,
		"highest":   highest,
		"header":    p.HeaderPayload(isPrivate),
		"pjsk_type": int(req.Server),
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64.StdEncoding.EncodeToString(img))
}
