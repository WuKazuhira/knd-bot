package pjsk

import (
	"context"
	"encoding/base64"
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/profile"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/store"
)

type DiffRankModule struct {
	md      *masterdata.Loader
	fetcher *profile.Fetcher
	store   *store.Store
	draw    *draw.Client
}

func NewDiffRankModule(md *masterdata.Loader, f *profile.Fetcher, s *store.Store, d *draw.Client) *DiffRankModule {
	return &DiffRankModule{md: md, fetcher: f, store: s, draw: d}
}

func (m *DiffRankModule) Register(r *router.Router) {
	r.RegisterNumericSuffix("难度排行", []string{"ap难度排行", "fc难度排行"}, m.handle)
}

var diffAliasMap = map[string]string{
	"ma": "master", "master": "master",
	"ex": "expert", "expert": "expert",
	"hd": "hard", "hard": "hard",
	"nm": "normal", "normal": "normal",
	"ez": "easy", "easy": "easy",
}

func parseDiffArg(arg string) (difficulty string, levelValue float64, levelExact bool) {
	difficulty = "master"
	arg = strings.TrimSpace(arg)
	levelText := arg
	// 长的键放前面，避免 ex 先把 expert 截断
	keys := []string{"master", "expert", "hard", "normal", "easy", "ma", "ex", "hd", "nm", "ez"}
	for _, k := range keys {
		if strings.HasSuffix(arg, k) {
			difficulty = diffAliasMap[k]
			levelText = strings.TrimSpace(strings.TrimSuffix(arg, k))
			break
		}
		if strings.HasPrefix(arg, k) {
			difficulty = diffAliasMap[k]
			levelText = strings.TrimSpace(strings.TrimPrefix(arg, k))
			break
		}
	}
	levelText = strings.TrimSpace(levelText)
	if levelText != "" {
		if v, err := strconv.ParseFloat(levelText, 64); err == nil {
			levelValue = v
		}
	}
	levelExact = strings.Contains(levelText, ".")
	return difficulty, levelValue, levelExact
}

type diffItem struct {
	musicID     int
	playLevel   int
	displayAdj  float64
	hasConstant bool
}

func (m *DiffRankModule) handle(ctx context.Context, req router.Request) *onebot.ActionRequest {
	fcap := 0
	if strings.Contains(req.RawCmd, "ap") {
		fcap = 2
	} else if strings.Contains(req.RawCmd, "fc") {
		fcap = 1
	}
	difficulty, levelValue, levelExact := parseDiffArg(req.Arg)
	if strings.TrimSpace(req.Arg) == "" && fcap == 0 {
		fcap = 2
		difficulty = "master"
	}

	diffs, err := m.md.Load("musicDifficulties.json", int(req.Server))
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	musics, _ := m.md.Load("musics.json", int(req.Server))
	publishedAt := make(map[int]int64, len(musics))
	for _, mu := range musics {
		publishedAt[intField(mu, "id")] = int64(intField(mu, "publishedAt"))
	}
	constants := m.md.Constants()
	nowMS := nowMSDefault()

	var title, modeText string
	switch fcap {
	case 1:
		title = fmt.Sprintf("%s %s FC难度表（仅供参考）", strings.ToUpper(difficulty), levelLabel(levelValue))
		modeText = "FC"
	case 2:
		title = fmt.Sprintf("%s %s AP难度表（仅供参考）", strings.ToUpper(difficulty), levelLabel(levelValue))
		modeText = "AP"
	default:
		title = fmt.Sprintf("%s %s 难度表（仅供参考）", strings.ToUpper(difficulty), levelLabel(levelValue))
		modeText = "CLEAR"
	}

	var target []diffItem
	for _, d := range diffs {
		mid := intField(d, "musicId")
		if nowMS < publishedAt[mid] {
			continue
		}
		if strField(d, "musicDifficulty") != difficulty {
			continue
		}
		playLevel := intField(d, "playLevel")
		fp, fc, clear, hasC := adjustConstants(playLevel, constants[masterdata.ConstantKey{MusicID: mid, Diff: difficulty}], hasConstant(constants, mid, difficulty))
		var adj float64
		switch fcap {
		case 1:
			adj = fc
		case 2:
			adj = fp
		default:
			adj = clear
		}
		display := float64(playLevel) + adj
		if levelValue != 0 {
			if levelExact {
				if round1(display) != round1(levelValue) {
					continue
				}
			} else if int(display) != int(levelValue) {
				continue
			}
		}
		target = append(target, diffItem{musicID: mid, playLevel: playLevel, displayAdj: adj, hasConstant: hasC})
	}

	if len(target) == 0 {
		return onebot.ReplyText(req.Event,
			fmt.Sprintf("没有找到 %s %s 的难度排行数据，请检查定数/难度参数是否正确。",
				strings.ToUpper(difficulty), levelLabel(levelValue)), false)
	}

	sort.SliceStable(target, func(i, j int) bool {
		return float64(target[i].playLevel)+target[i].displayAdj > float64(target[j].playLevel)+target[j].displayAdj
	})
	musicData := map[string][]int{}
	var order []string
	for _, it := range target {
		var levelRound string
		if it.hasConstant {
			levelRound = fmt.Sprintf("%.1f", float64(it.playLevel)+it.displayAdj)
		} else {
			levelRound = fmt.Sprintf("%d.?", it.playLevel)
		}
		if _, ok := musicData[levelRound]; !ok {
			order = append(order, levelRound)
		}
		musicData[levelRound] = append(musicData[levelRound], it.musicID)
	}

	var musicResult map[string][]int
	var header any
	if m.store != nil && m.fetcher != nil {
		if uid, isPriv, exists, _ := m.store.GetUserBind(ctx, req.Event.UserID, int(req.Server)); exists && !isPriv {
			if p, err := m.fetcher.GetSuite(ctx, strconv.FormatInt(uid, 10), int(req.Server)); err == nil {
				musicResult = map[string][]int{}
				for mid, arr := range p.MusicResult {
					musicResult[strconv.Itoa(mid)] = arr[:]
				}
				header = p.HeaderPayload(false)
			}
		}
	}

	oneRowCount := any(nil)
	if levelValue == 0 {
		oneRowCount = 5
	}
	img, err := m.draw.Render(ctx, "diffrank", map[string]any{
		"music_data":    musicData,
		"difficulty":    difficulty,
		"music_result":  musicResult,
		"header":        header,
		"one_row_count": oneRowCount,
		"title":         strings.TrimSpace(title),
		"mode_text":     modeText,
		"is_private":    false,
		"server_label":  strings.ToUpper(serverDirName(int(req.Server))),
		"pjsk_type":     int(req.Server),
	})
	if err != nil {
		return onebot.ReplyText(req.Event, errBug, false)
	}
	return onebot.ReplyImage(req.Event, base64.StdEncoding.EncodeToString(img))
}

func adjustConstants(playLevel int, apConstant float64, has bool) (float64, float64, float64, bool) {
	if !has {
		return 0, 0, 0, false
	}
	fp := apConstant - float64(playLevel)
	fc := fcrank(playLevel, apConstant) - float64(playLevel)
	pl := fc*2/3 + fp*1/3
	return fp, fc, pl, true
}

func hasConstant(constants map[masterdata.ConstantKey]float64, mid int, diff string) bool {
	_, ok := constants[masterdata.ConstantKey{MusicID: mid, Diff: diff}]
	return ok
}

func levelLabel(v float64) string {
	if v == 0 {
		return ""
	}
	if v == math.Trunc(v) {
		return strconv.Itoa(int(v))
	}
	return strconv.FormatFloat(v, 'f', -1, 64)
}

func round1(x float64) float64 {
	return math.Round(x*10) / 10
}
