package pjsk

import (
	"context"
	"fmt"
	"math/rand"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"

	"gopkg.in/yaml.v3"

	"github.com/kazuhira/go-pjsk-bot/internal/cards"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/store"
)

// GuessRenderer 是 pjsk-draw 客户端所需的最小接口；生产环境传入 *draw.Client。
type GuessRenderer interface {
	RenderMulti(context.Context, string, map[string]any) ([][]byte, error)
}

type guessMode string

const (
	guessModeCard    guessMode = "card"
	guessModeMusic   guessMode = "music"
	guessModeChart   guessMode = "chart"
	guessModeListen  guessMode = "listen"
	guessModeReverse guessMode = "reverse"
	guessModeLyrics  guessMode = "lyrics"
)

type guessSpec struct {
	mode       guessMode
	difficulty int
	server     int
}

const (
	guessCardPattern          = `(?i)^(cn|tw|jp)?\s*(?:pjsk|sekai)\s*(普通|正常|阴间|非人类)?\s*猜卡面\s*([123]?)$`
	guessMusicPattern         = `(?i)^(cn|tw|jp)?\s*(?:pjsk|sekai)\s*(普通|正常|阴间|非人类)?\s*(?:猜曲|识曲)\s*([123]?)$`
	guessSpecialPattern       = `(?i)^(cn|tw|jp)?\s*(?:pjsk|sekai)\s*(听歌|倒放|歌词)(?:\s*(?:猜曲|识曲))?\s*$`
	guessSpecialNumberPattern = `(?i)^(cn|tw|jp)?\s*(?:pjsk|sekai)\s*(?:猜曲|识曲)\s*([457])$`
	guessChartPattern         = `(?i)^(cn|tw|jp)?\s*(?:pjsk|sekai)\s*(?:谱面\s*(?:猜曲|识曲)\s*6?|猜谱面|(?:猜曲|识曲)\s*6)\s*$`
	guessRankPattern          = `(?i)^(cn|tw|jp)?\s*(?:pjsk|sekai)\s*(正常|阴间|非人类|听歌|倒放|谱面|歌词)?\s*(猜曲|猜卡面|猜谱面)\s*排(?:行|名)?榜\s*([0-9]*)$`
)

var (
	guessCardRE          = regexp.MustCompile(guessCardPattern)
	guessMusicRE         = regexp.MustCompile(guessMusicPattern)
	guessSpecialRE       = regexp.MustCompile(guessSpecialPattern)
	guessSpecialNumberRE = regexp.MustCompile(guessSpecialNumberPattern)
	guessChartRE         = regexp.MustCompile(guessChartPattern)
	guessRankRE          = regexp.MustCompile(guessRankPattern)
)

// parseGuessCommand 只接受完整的、第一阶段支持的图片题命令。
// 返回的 server 与 Router 的 cn/tw 约定一致：0=JP、1=TW、2=CN。
func parseGuessCommand(text string) (guessSpec, bool) {
	text = strings.TrimSpace(text)
	if groups := guessSpecialRE.FindStringSubmatch(text); groups != nil {
		mode := guessModeLyrics
		difficulty := 7
		switch strings.TrimSpace(groups[2]) {
		case "听歌":
			mode, difficulty = guessModeListen, 4
		case "倒放":
			mode, difficulty = guessModeReverse, 5
		}
		return guessSpec{mode: mode, difficulty: difficulty, server: guessServer(groups[1])}, true
	}
	if groups := guessSpecialNumberRE.FindStringSubmatch(text); groups != nil {
		difficulty, _ := strconv.Atoi(groups[2])
		mode := map[int]guessMode{4: guessModeListen, 5: guessModeReverse, 7: guessModeLyrics}[difficulty]
		return guessSpec{mode: mode, difficulty: difficulty, server: guessServer(groups[1])}, mode != ""
	}
	if groups := guessCardRE.FindStringSubmatch(text); groups != nil {
		difficulty, ok := parseGuessDifficulty(groups[2], groups[3])
		if !ok {
			return guessSpec{}, false
		}
		return guessSpec{mode: guessModeCard, difficulty: difficulty, server: guessServer(groups[1])}, true
	}
	if groups := guessMusicRE.FindStringSubmatch(text); groups != nil {
		difficulty, ok := parseGuessDifficulty(groups[2], groups[3])
		if !ok {
			return guessSpec{}, false
		}
		return guessSpec{mode: guessModeMusic, difficulty: difficulty, server: guessServer(groups[1])}, true
	}
	if guessChartRE.MatchString(text) {
		return guessSpec{mode: guessModeChart, difficulty: 6, server: guessServer(guessChartRE.FindStringSubmatch(text)[1])}, true
	}
	return guessSpec{}, false
}

func parseGuessDifficulty(kind, number string) (int, bool) {
	if kind != "" {
		switch kind {
		case "普通", "正常":
			return 1, true
		case "阴间":
			return 2, true
		case "非人类":
			return 3, true
		default:
			return 0, false
		}
	}
	if number == "" {
		return 1, true
	}
	n, err := strconv.Atoi(number)
	return n, err == nil && n >= 1 && n <= 3
}

func guessServer(prefix string) int {
	switch strings.ToLower(prefix) {
	case "tw":
		return 1
	case "cn":
		return 2
	default:
		return 0
	}
}

// GuessModule 实现 PJSK 猜题：卡面、曲绘、谱面、听歌、倒放和歌词。
// 会话按群互斥；答案匹配成功后每位用户最多消耗三次猜测。
type GuessModule struct {
	md      *masterdata.Loader
	draw    GuessRenderer
	store   *store.Store
	chara   *cards.CharaAliasResolver
	sender  onebot.ActionSender
	dataDir string
	now     func() time.Time

	manager *guessSessionManager
}

// NewGuessModule 创建 guess 模块。sender 用于 90 秒超时后的后台主动发送。
func NewGuessModule(md *masterdata.Loader, renderer GuessRenderer, s *store.Store, resolver *cards.CharaAliasResolver, sender onebot.ActionSender, dataDir string) *GuessModule {
	m := &GuessModule{
		md:      md,
		draw:    renderer,
		store:   s,
		chara:   resolver,
		sender:  sender,
		dataDir: dataDir,
		now:     time.Now,
	}
	m.manager = newGuessSessionManager(guessTTL, m.now, m.handleExpired)
	return m
}

// Register 注册完整的猜题启动命令与发起者结束命令。
func (m *GuessModule) Register(r *router.Router) {
	r.RegisterRegex("guess", guessCardPattern, m.handleStart)
	r.RegisterRegex("guess", guessMusicPattern, m.handleStart)
	r.RegisterRegex("guess", guessSpecialPattern, m.handleStart)
	r.RegisterRegex("guess", guessSpecialNumberPattern, m.handleStart)
	r.RegisterRegex("guess", guessChartPattern, m.handleStart)
	r.RegisterRegex("guess", guessRankPattern, m.handleRank)
	r.Register("结束猜曲", []string{"结束猜卡面", "结束猜谱面"}, m.handleEnd)
	r.Register("来点提示", []string{"来丶提示", "给点提示", "给丶提示"}, m.handleTip)
}

// Close 停止当前群的猜题超时定时器。
func (m *GuessModule) Close() {
	if m != nil && m.manager != nil {
		m.manager.close()
	}
}

func (m *GuessModule) handleStart(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if !req.Event.IsGroup() {
		return nil
	}
	spec, ok := parseGuessCommand(req.Arg)
	if !ok {
		return nil
	}
	if m.manager == nil {
		return onebot.ReplyText(req.Event, "猜题功能暂不可用", false)
	}
	if _, active := m.manager.current(req.Event.GroupID); active {
		return onebot.ReplyText(req.Event, "本群已经有进行中的猜题，请等待本轮结束！", true)
	}

	session, err := m.buildSession(ctx, req.Event, spec)
	if err != nil {
		return onebot.ReplyText(req.Event, "猜题启动失败：题目资源暂不可用", true)
	}
	if !m.manager.start(session) {
		return onebot.ReplyText(req.Event, "本群已经有进行中的猜题，请等待本轮结束！", true)
	}

	message := onebot.Message{onebot.Text(guessStartText(spec))}
	if session.questionText != "" {
		message = append(message, onebot.Text("\n"+session.questionText))
	}
	if len(session.questionImage) > 0 {
		message = append(message, onebot.ImageBytes(base64Encode(session.questionImage)))
	}
	if session.mediaFile != "" {
		message = append(message, onebot.RecordFile(session.mediaFile))
	}
	return onebot.SendMessageAction(req.Event, message)
}

func (m *GuessModule) handleEnd(_ context.Context, req router.Request) *onebot.ActionRequest {
	if !req.Event.IsGroup() || m.manager == nil {
		return nil
	}
	result := m.manager.end(req.Event.GroupID, req.Event.UserID)
	switch result.outcome {
	case guessInactive:
		return onebot.ReplyText(req.Event, "本群当前没有进行中的猜题", true)
	case guessNotInitiator:
		return onebot.ReplyText(req.Event, "只有猜题发起者可以提前结束本轮游戏", true)
	case guessExpired:
		return m.timeoutAction(req.Event, result.session)
	case guessEnded:
		return m.settlementAction(req.Event, result.session, "已提前结束")
	default:
		return nil
	}
}

func (m *GuessModule) handleTip(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if !req.Event.IsGroup() || m.manager == nil {
		return nil
	}
	session, text, image, ok := m.manager.takeTip(req.Event.GroupID, req.Event.UserID)
	if session == nil {
		return onebot.ReplyText(req.Event, "本群当前没有进行中的猜题", true)
	}
	if !ok {
		if session.initiatorID != req.Event.UserID {
			return onebot.ReplyText(req.Event, "只有猜题发起者可以使用提示功能", true)
		}
		return onebot.ReplyText(req.Event, "已经没有可用提示了", true)
	}
	if image == nil && text == "" {
		text = "提示资源暂不可用"
	}
	message := onebot.Message{onebot.Text("提示：" + text + fmt.Sprintf("（剩余提示次数：%d）", len(session.tips)+len(session.tipImages)))}
	if len(image) > 0 {
		message = append(message, onebot.ImageBytes(base64Encode(image)))
	}
	_ = ctx
	return onebot.SendMessageAction(req.Event, message)
}

func (m *GuessModule) handleRank(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if !req.Event.IsGroup() || m.store == nil {
		return onebot.ReplyText(req.Event, "排行榜数据暂不可用", true)
	}
	groups := guessRankRE.FindStringSubmatch(strings.TrimSpace(req.Arg))
	if len(groups) == 0 {
		groups = guessRankRE.FindStringSubmatch(strings.TrimSpace(req.Event.Message.PlainText()))
	}
	if len(groups) == 0 {
		return onebot.ReplyText(req.Event, "排行榜指令格式错误", true)
	}
	server := guessServer(groups[1])
	kind := groups[2]
	gameTypeName := groups[3]
	gameType := "guessmusic"
	if gameTypeName == "猜卡面" {
		gameType = "guesscard"
	}
	var difficulty *int
	if kind != "" {
		d := map[string]int{"正常": 1, "阴间": 2, "非人类": 3, "听歌": 4, "倒放": 5, "谱面": 6, "歌词": 7}[kind]
		if d == 0 || (gameType == "guesscard" && d > 3) {
			return onebot.ReplyText(req.Event, "没有这种类型的排行榜哦！", true)
		}
		difficulty = &d
	}
	rows, err := m.store.QueryGuessRank(ctx, req.Event.GroupID, gameType, difficulty, server, parseRankLimit(groups[4]))
	if err != nil || len(rows) == 0 {
		return onebot.ReplyText(req.Event, "当前类型排行榜尚无人上榜哦", true)
	}
	var b strings.Builder
	fmt.Fprintf(&b, "%s排行榜：", groups[2]+groups[3])
	for i, row := range rows {
		fmt.Fprintf(&b, "\n%d. %d：%d次", i+1, row.UserID, row.Count)
	}
	return onebot.ReplyText(req.Event, b.String(), false)
}

func parseRankLimit(raw string) int {
	n, err := strconv.Atoi(raw)
	if err != nil || n < 10 || n > 50 {
		return 10
	}
	return n
}

// HandleAnswer 处理未被 Router 命中的群消息。未知答案不消费次数，也不回复。
func (m *GuessModule) HandleAnswer(ctx context.Context, event onebot.MessageEvent) *onebot.ActionRequest {
	if m == nil || m.manager == nil || !event.IsGroup() {
		return nil
	}
	session, active := m.manager.current(event.GroupID)
	if !active {
		return nil
	}
	answer := strings.TrimSpace(event.Message.PlainText())
	if !canGuessAnswer(answer) {
		return nil
	}
	answerID, _, ok := m.matchAnswer(ctx, session, answer)
	if !ok {
		return nil
	}

	result := m.manager.guess(event.GroupID, event.UserID, int64(answerID))
	switch result.outcome {
	case guessWrong:
		return onebot.ReplyText(event, fmt.Sprintf("猜错了，还剩 %d 次猜测。", result.remain), true)
	case guessAttemptsExhausted:
		return nil
	case guessCorrect:
		m.settleCorrect(ctx, event, result.session)
		return m.settlementAction(event, result.session, "猜对了")
	case guessExpired:
		return m.timeoutAction(event, result.session)
	default:
		return nil
	}
}

func (m *GuessModule) buildSession(ctx context.Context, event onebot.MessageEvent, spec guessSpec) (*guessSession, error) {
	if m.md == nil || (m.draw == nil && spec.mode != guessModeListen && spec.mode != guessModeReverse) {
		return nil, fmt.Errorf("guess dependencies unavailable")
	}
	var (
		answerID      int
		answerName    string
		questionImage []byte
		answerImage   []byte
		questionText  string
		mediaFile     string
		answerFile    string
		asset         string
	)

	switch spec.mode {
	case guessModeCard:
		card, err := m.pickCard(spec.server)
		if err != nil {
			return nil, err
		}
		images, err := m.draw.RenderMulti(ctx, "guess_card", map[string]any{
			"asset":       card.asset,
			"rarity_type": card.rarity,
			"size":        guessCardSize(spec.difficulty),
			"is_bw":       spec.difficulty == 2,
			"pjsk_type":   spec.server,
		})
		if err != nil || len(images) == 0 {
			if err != nil {
				return nil, err
			}
			return nil, fmt.Errorf("guess_card returned no image")
		}
		answerID = card.characterID
		answerName = card.displayName
		asset = card.asset
		questionImage, answerImage = splitGuessImages(images)
	case guessModeMusic, guessModeChart, guessModeListen, guessModeReverse, guessModeLyrics:
		music, err := m.pickMusic(spec.server)
		if err != nil {
			return nil, err
		}
		asset = music.asset
		switch spec.mode {
		case guessModeChart:
			question, err := m.draw.RenderMulti(ctx, "guess_chart", map[string]any{
				"music_id":  music.id,
				"pjsk_type": spec.server,
			})
			if err != nil || len(question) == 0 {
				if err != nil {
					return nil, err
				}
				return nil, fmt.Errorf("guess_chart returned no image")
			}
			questionImage, answerImage = splitGuessImages(question)
		case guessModeLyrics:
			lines, lineNum, err := m.pickLyrics(music.id, spec.server)
			if err != nil {
				return nil, err
			}
			questionText = strings.Join(lines[lineNum:lineNum+2], "\n")
			image, err := m.draw.RenderMulti(ctx, "guess_lyrics", map[string]any{
				"lines": lines, "line_num": lineNum, "asset": music.asset, "pjsk_type": spec.server,
			})
			if err != nil || len(image) == 0 {
				if err != nil {
					return nil, err
				}
				return nil, fmt.Errorf("guess_lyrics returned no image")
			}
			answerImage = image[0]
		default:
			if spec.mode == guessModeListen || spec.mode == guessModeReverse {
				audioAsset := m.defaultVocalAsset(spec.server, music.id, music.asset)
				source := m.musicPath(spec.server, audioAsset)
				if _, err := os.Stat(source); err != nil {
					return nil, fmt.Errorf("music resource unavailable: %w", err)
				}
				mediaFile = m.prepareAudio(ctx, source, spec.mode == guessModeReverse, event.GroupID)
				if mediaFile == "" {
					mediaFile = source
				}
				answerFile = source
			} else {
				images, err := m.draw.RenderMulti(ctx, "guess_jacket", map[string]any{
					"asset": music.asset, "size": guessMusicSize(spec.difficulty),
					"is_bw": spec.difficulty == 2, "pjsk_type": spec.server,
				})
				if err != nil || len(images) == 0 {
					if err != nil {
						return nil, err
					}
					return nil, fmt.Errorf("guess_jacket returned no image")
				}
				questionImage, answerImage = splitGuessImages(images)
			}
		}
		answerID = music.id
		answerName = music.title
	default:
		return nil, fmt.Errorf("unsupported guess mode %q", spec.mode)
	}

	session := &guessSession{
		groupID:       event.GroupID,
		selfID:        event.SelfID,
		server:        spec.server,
		mode:          spec.mode,
		difficulty:    spec.difficulty,
		initiatorID:   event.UserID,
		answerID:      answerID,
		answerName:    answerName,
		asset:         asset,
		questionImage: questionImage,
		answerImage:   answerImage,
		questionText:  questionText,
		mediaFile:     mediaFile,
		answerFile:    answerFile,
	}
	session.tips = m.buildTips(spec, answerID, spec.server)
	return session, nil
}

type guessCardCandidate struct {
	characterID int
	asset       string
	rarity      string
	displayName string
}

func (m *GuessModule) pickCard(server int) (guessCardCandidate, error) {
	cardsData, err := m.md.Load("cards.json", server)
	if err != nil {
		return guessCardCandidate{}, err
	}
	nowMS := m.now().UnixMilli()
	candidates := make([]guessCardCandidate, 0, len(cardsData))
	for _, card := range cardsData {
		releaseAt := int64(intField(card, "releaseAt"))
		if releaseAt > 0 && releaseAt > nowMS {
			continue
		}
		rarity := strField(card, "cardRarityType")
		if rarity == "rarity_1" || rarity == "rarity_2" {
			continue
		}
		characterID := intField(card, "characterId")
		asset := strings.TrimSpace(strField(card, "assetbundleName"))
		if characterID == 0 || asset == "" {
			continue
		}
		prefix := strings.TrimSpace(strField(card, "prefix"))
		if prefix == "" {
			prefix = asset
		}
		charaName := m.characterName(server, characterID)
		display := prefix
		if charaName != "" {
			display += " - " + charaName
		}
		candidates = append(candidates, guessCardCandidate{
			characterID: characterID,
			asset:       asset,
			rarity:      rarity,
			displayName: display,
		})
	}
	if len(candidates) == 0 {
		return guessCardCandidate{}, fmt.Errorf("no released cards")
	}
	return candidates[rand.Intn(len(candidates))], nil
}

type guessMusicCandidate struct {
	id    int
	title string
	asset string
}

func (m *GuessModule) pickMusic(server int) (guessMusicCandidate, error) {
	musics, err := m.md.Load("musics.json", server)
	if err != nil {
		return guessMusicCandidate{}, err
	}
	nowMS := m.now().UnixMilli()
	candidates := make([]guessMusicCandidate, 0, len(musics))
	for _, music := range musics {
		publishedAt := int64(intField(music, "publishedAt"))
		if publishedAt > 0 && publishedAt > nowMS {
			continue
		}
		id := intField(music, "id")
		title := strings.TrimSpace(strField(music, "title"))
		asset := strings.TrimSpace(strField(music, "assetbundleName"))
		if id == 0 || title == "" {
			continue
		}
		candidates = append(candidates, guessMusicCandidate{id: id, title: title, asset: asset})
	}
	if len(candidates) == 0 {
		return guessMusicCandidate{}, fmt.Errorf("no released music")
	}
	return candidates[rand.Intn(len(candidates))], nil
}

func (m *GuessModule) pickLyrics(musicID, server int) ([]string, int, error) {
	path := filepath.Join(m.dataDir, "ondemand", serverDirName(server), "lyrics", fmt.Sprintf("%d.txt", musicID))
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, 0, err
	}
	lines := make([]string, 0)
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if line != "" {
			lines = append(lines, line)
		}
	}
	if len(lines) < 2 {
		return nil, 0, fmt.Errorf("lyrics has fewer than two lines")
	}
	return lines, rand.Intn(len(lines) - 1), nil
}

func (m *GuessModule) musicPath(server int, asset string) string {
	return filepath.Join(m.dataDir, "ondemand", serverDirName(server), "ondemand", "music", "long", asset, asset+".mp3")
}

func (m *GuessModule) prepareAudio(ctx context.Context, source string, reverse bool, groupID int64) string {
	if _, err := exec.LookPath("ffmpeg"); err != nil {
		return ""
	}
	dir := filepath.Join(m.dataDir, ".guess")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return ""
	}
	out := filepath.Join(dir, fmt.Sprintf("guess_%d_%d.mp3", groupID, time.Now().UnixNano()))
	args := []string{"-y", "-ss", "10", "-t", "1.7", "-i", source}
	if reverse {
		args = append(args, "-af", "areverse")
	}
	args = append(args, "-vn", "-acodec", "libmp3lame", out)
	if err := exec.CommandContext(ctx, "ffmpeg", args...).Run(); err != nil {
		_ = os.Remove(out)
		return ""
	}
	return out
}

func (m *GuessModule) defaultVocalAsset(server, musicID int, fallback string) string {
	vocals, err := m.md.Load("musicVocals.json", server)
	if err != nil {
		return fallback
	}
	candidate := ""
	for _, vocal := range vocals {
		if intField(vocal, "musicId") != musicID {
			continue
		}
		asset := strings.TrimSpace(strField(vocal, "assetbundleName"))
		if asset == "" {
			continue
		}
		typ := strField(vocal, "musicVocalType")
		if typ == "sekai" || typ == "instrumental" {
			return asset
		}
		candidate = asset
	}
	if candidate != "" {
		return candidate
	}
	return fallback
}

func (m *GuessModule) buildTips(spec guessSpec, answerID, server int) []string {
	if spec.mode == guessModeCard {
		name := m.characterName(server, answerID)
		if name == "" {
			return []string{"这是一位 PJSK 角色"}
		}
		return []string{"答案角色是“" + name + "”", "可以从角色所在组合方向思考", "可以结合角色生日和个人特征判断"}
	}
	if spec.mode == guessModeLyrics {
		return []string{"歌词题答案是一首 PJSK 歌曲", "可以尝试查看歌词对应的演唱者"}
	}
	musics, err := m.md.Load("musics.json", server)
	if err != nil {
		return []string{"答案是一首 PJSK 歌曲"}
	}
	for _, music := range musics {
		if intField(music, "id") != answerID {
			continue
		}
		out := []string{"这首歌的作词：" + strField(music, "lyricist"), "这首歌的作曲：" + strField(music, "composer")}
		if spec.mode == guessModeChart {
			out = append(out, "这是 Master 谱面题")
		}
		return out
	}
	return []string{"答案是一首 PJSK 歌曲"}
}

func (m *GuessModule) characterName(server, characterID int) string {
	if chars, err := m.md.Load("gameCharacters.json", server); err == nil {
		for _, chara := range chars {
			if intField(chara, "id") != characterID {
				continue
			}
			name := strings.TrimSpace(strField(chara, "firstName") + " " + strField(chara, "givenName"))
			if name != "" {
				return name
			}
		}
	}
	return map[int]string{
		1: "星乃一歌", 2: "天马咲希", 3: "望月穗波", 4: "日野森志步",
		5: "花里实乃理", 6: "桐谷遥", 7: "桃井爱莉", 8: "日野森雫",
		9: "小豆泽心羽", 10: "白石杏", 11: "东云彰人", 12: "青柳冬弥",
		13: "天马司", 14: "凤绘梦", 15: "草薙宁宁", 16: "神代类",
		17: "宵崎奏", 18: "朝比奈真冬", 19: "东云绘名", 20: "晓山瑞希",
		21: "初音未来", 22: "镜音铃", 23: "镜音连", 24: "巡音流歌", 25: "MEIKO", 26: "KAITO",
	}[characterID]
}

func (m *GuessModule) matchAnswer(ctx context.Context, session *guessSession, answer string) (int, string, bool) {
	if session == nil {
		return 0, "", false
	}
	if session.mode == guessModeCard {
		if m.chara == nil {
			return 0, "", false
		}
		id := m.chara.Resolve(strings.ToLower(strings.TrimSpace(answer)))
		if id == 0 {
			return 0, "", false
		}
		return id, m.characterName(session.server, id), true
	}
	return m.matchMusicAnswer(ctx, session.server, answer)
}

func (m *GuessModule) matchMusicAnswer(ctx context.Context, server int, answer string) (int, string, bool) {
	musics, err := m.md.Load("musics.json", server)
	if err != nil {
		return 0, "", false
	}
	byID := make(map[int]map[string]any, len(musics))
	for _, music := range musics {
		byID[intField(music, "id")] = music
	}
	if m.store != nil {
		if id, ok, err := m.store.QuerySongID(ctx, strings.TrimSpace(answer)); err == nil && ok {
			if music, exists := byID[id]; exists {
				return id, strField(music, "title"), true
			}
		}
	}

	want := normalizeSongQuery(answer)
	if want == "" {
		return 0, "", false
	}
	translations := m.loadTranslations(server)
	for id, music := range byID {
		if normalizeSongQuery(strField(music, "title")) == want {
			return id, strField(music, "title"), true
		}
		for _, translated := range strings.Split(translations[id], "/") {
			if normalizeSongQuery(strings.TrimSpace(translated)) == want {
				return id, strField(music, "title"), true
			}
		}
	}
	return 0, "", false
}

func (m *GuessModule) loadTranslations(server int) map[int]string {
	out := make(map[int]string)
	if m.dataDir == "" {
		return out
	}
	path := filepath.Join(m.dataDir, "ondemand", serverDirName(server), "translate.yaml")
	raw, err := os.ReadFile(path)
	if err != nil {
		return out
	}
	_ = yaml.Unmarshal(raw, &out)
	return out
}

func (m *GuessModule) settleCorrect(ctx context.Context, event onebot.MessageEvent, session *guessSession) {
	if m.store == nil || session == nil {
		return
	}
	daily, err := m.store.GuessDailyCount(ctx, event.UserID, event.GroupID)
	if err != nil || daily >= 10 {
		session.rewardCapped = true
		return
	}
	base := []int{10, 30, 50, 30, 45, 60, 60}
	extra := []int{3, 5, 10, 5, 10, 15, 15}
	idx := session.difficulty - 1
	if idx < 0 || idx >= len(base) {
		return
	}
	amount := base[idx] + rand.Intn(extra[idx]+1)
	if session.tipsUsed > 0 {
		amount -= session.tipsUsed * amount / 4
	}
	if event.UserID != session.initiatorID {
		amount = (amount*9 + 9) / 10
	}
	session.reward = amount
	if err := m.store.AddGold(ctx, event.UserID, event.GroupID, amount); err != nil {
		return
	}
	_ = m.store.AddGuessResult(ctx, event.UserID, event.GroupID, guessGameType(session.mode), session.difficulty, session.server, !session.rankEligible)
}

func guessGameType(mode guessMode) string {
	if mode == guessModeCard {
		return "guesscard"
	}
	return "guessmusic"
}

func (m *GuessModule) settlementAction(event onebot.MessageEvent, session *guessSession, lead string) *onebot.ActionRequest {
	if session == nil {
		return nil
	}
	text := fmt.Sprintf("%s！正确答案：%s", lead, session.answerName)
	if lead == "猜对了" {
		if session.rewardCapped {
			text += "；但是已达今日游戏获取金币上限，并没有奖励"
		} else if session.reward > 0 {
			text += fmt.Sprintf("；奖励%d金币", session.reward)
		}
	}
	message := onebot.Message{onebot.Text(text)}
	if lead == "猜对了" && event.IsGroup() {
		message = onebot.Message{onebot.At(event.UserID), onebot.Text(" " + text)}
	}
	if len(session.answerImage) > 0 {
		message = append(message, onebot.ImageBytes(base64Encode(session.answerImage)))
	}
	if session.answerFile != "" {
		message = append(message, onebot.RecordFile(session.answerFile))
	}
	return onebot.SendMessageAction(event, message)
}

func (m *GuessModule) timeoutAction(event onebot.MessageEvent, session *guessSession) *onebot.ActionRequest {
	if session == nil {
		return nil
	}
	text := fmt.Sprintf("时间到，正确答案：%s", session.answerName)
	message := onebot.Message{onebot.Text(text)}
	if len(session.answerImage) > 0 {
		message = append(message, onebot.ImageBytes(base64Encode(session.answerImage)))
	}
	if session.answerFile != "" {
		message = append(message, onebot.RecordFile(session.answerFile))
	}
	return onebot.SendMessageAction(event, message)
}

func (m *GuessModule) handleExpired(session *guessSession) {
	if m.sender == nil || session == nil {
		return
	}
	event := onebot.MessageEvent{SelfID: session.selfID, MessageType: "group", GroupID: session.groupID}
	if action := m.timeoutAction(event, session); action != nil {
		_ = m.sender.Send(action)
	}
}

func splitGuessImages(images [][]byte) ([]byte, []byte) {
	question := append([]byte(nil), images[0]...)
	answer := append([]byte(nil), images[0]...)
	if len(images) > 1 {
		answer = append([]byte(nil), images[len(images)-1]...)
	}
	return question, answer
}

func guessCardSize(difficulty int) int {
	switch difficulty {
	case 2:
		return 250
	case 3:
		return 60
	default:
		return 250
	}
}

func guessMusicSize(difficulty int) int {
	switch difficulty {
	case 2:
		return 140
	case 3:
		return 30
	default:
		return 140
	}
}

func guessStartText(spec guessSpec) string {
	kind := "曲绘"
	end := "结束猜曲"
	switch spec.mode {
	case guessModeCard:
		kind = "卡面"
		end = "结束猜卡面"
	case guessModeChart:
		kind = "谱面"
		end = "结束猜谱面"
	case guessModeListen:
		kind = "听歌识曲"
	case guessModeReverse:
		kind = "倒放识曲"
	case guessModeLyrics:
		kind = "歌词"
	}
	difficulty := map[int]string{1: "普通", 2: "阴间", 3: "非人类"}[spec.difficulty]
	if spec.mode == guessModeChart || spec.mode == guessModeListen || spec.mode == guessModeReverse || spec.mode == guessModeLyrics {
		difficulty = ""
	}
	if difficulty != "" {
		kind = difficulty + kind
	}
	return fmt.Sprintf("PJSK%s竞猜开始！请直接发送答案（每人最多猜%d次）。本轮限时%d秒，发起者可发送“%s”提前结束。", kind, guessMaxAttempts, int(guessTTL/time.Second), end)
}

func canGuessAnswer(answer string) bool {
	answer = strings.TrimSpace(strings.ToLower(answer))
	if answer == "" || len([]rune(answer)) > 32 {
		return false
	}
	allPunctuation := true
	for _, r := range answer {
		if !unicode.IsPunct(r) && r != '_' && !unicode.IsSpace(r) {
			allPunctuation = false
			break
		}
	}
	if allPunctuation {
		return false
	}
	for _, separator := range []string{"，", ",", "。", "！", "？", "；", ";", "：", ":", "、", "\n", "\r"} {
		if strings.Contains(answer, separator) {
			return false
		}
	}
	return len(strings.Fields(answer)) <= 3
}
