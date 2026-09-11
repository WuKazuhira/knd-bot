package pjsk

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/cards"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

func TestParseGuessCommand(t *testing.T) {
	cases := []struct {
		text       string
		mode       guessMode
		difficulty int
		server     int
		ok         bool
	}{
		{"pjsk猜卡面", guessModeCard, 1, 0, true},
		{"cnpjsk阴间猜卡面", guessModeCard, 2, 2, true},
		{"tw pjsk非人类猜曲3", guessModeMusic, 3, 1, true},
		{"pjsk猜谱面", guessModeChart, 6, 0, true},
		{"cnsekai谱面猜曲", guessModeChart, 6, 2, true},
		{"pjsk猜曲6", guessModeChart, 6, 0, true},
		{"pjsk听歌猜曲", guessModeListen, 4, 0, true},
		{"pjsk倒放识曲", guessModeReverse, 5, 0, true},
		{"pjsk歌词猜曲", guessModeLyrics, 7, 0, true},
		{"pjsk猜曲4", guessModeListen, 4, 0, true},
		{"pjsk猜曲5", guessModeReverse, 5, 0, true},
		{"pjsk猜曲7", guessModeLyrics, 7, 0, true},
		{"pjsk猜谱面1", "", 0, 0, false},
		{"pjsk猜卡面 foo", "", 0, 0, false},
	}
	for _, tc := range cases {
		spec, ok := parseGuessCommand(tc.text)
		if ok != tc.ok {
			t.Errorf("%q ok=%v, want %v", tc.text, ok, tc.ok)
			continue
		}
		if !ok {
			continue
		}
		if spec.mode != tc.mode || spec.difficulty != tc.difficulty || spec.server != tc.server {
			t.Errorf("%q => %+v, want mode=%q difficulty=%d server=%d", tc.text, spec, tc.mode, tc.difficulty, tc.server)
		}
	}
}

func TestGuessRouterOnlyMatchesCompleteOwnedCommands(t *testing.T) {
	m := &GuessModule{}
	r := router.New([]string{"/", ""}, router.ParseOwnership(`[
		"guess", "结束猜曲", "来点提示"
	]`))
	m.Register(r)

	cases := []struct {
		text string
		want bool
	}{
		{"pjsk猜卡面", true},
		{"cnpjsk阴间猜卡面", true},
		{"tw pjsk非人类猜曲3", true},
		{"pjsk猜谱面", true},
		{"pjsk猜曲6", true},
		{"结束猜曲", true},
		{"cn结束猜卡面", true},
		{"pjsk听歌猜曲", true},
		{"pjsk猜曲4", true},
		{"给点提示", true},
		{"pjsk猜曲排行榜", true},
		{"pjsk猜卡面 extra", false},
		{"pjsk猜", false},
	}
	for _, tc := range cases {
		event := onebot.MessageEvent{
			SelfID:      1,
			UserID:      2,
			MessageID:   3,
			MessageType: "group",
			GroupID:     4,
			Message:     onebot.Message{onebot.Text(tc.text)},
		}
		_, _, ok := r.Match(event)
		if ok != tc.want {
			t.Errorf("Router.Match(%q)=%v, want %v", tc.text, ok, tc.want)
		}
	}
}

func TestGuessSpecialStartText(t *testing.T) {
	for _, tc := range []struct {
		spec guessSpec
		want string
	}{
		{guessSpec{mode: guessModeListen, difficulty: 4}, "听歌识曲"},
		{guessSpec{mode: guessModeReverse, difficulty: 5}, "倒放识曲"},
		{guessSpec{mode: guessModeLyrics, difficulty: 7}, "歌词竞猜"},
	} {
		if got := guessStartText(tc.spec); !strings.Contains(got, tc.want) {
			t.Errorf("guessStartText(%+v)=%q, want %q", tc.spec, got, tc.want)
		}
	}
}

func TestCanGuessAnswer(t *testing.T) {
	for _, text := range []string{"星乃一歌", "Tell Me", "ロキ"} {
		if !canGuessAnswer(text) {
			t.Errorf("%q 应为可猜答案", text)
		}
	}
	for _, text := range []string{"", "!!!", "a,b", "a b c d", "这是一段超过三十二个字符的答案文本xxxxxxxxxxxxxxxxxxxxxxxx"} {
		if canGuessAnswer(text) {
			t.Errorf("%q 不应为可猜答案", text)
		}
	}
}

func TestGuessSettlementActionContainsAnswerImage(t *testing.T) {
	m := &GuessModule{}
	event := onebot.MessageEvent{MessageType: "group", GroupID: 9, UserID: 10}
	action := m.settlementAction(event, &guessSession{
		answerName:  "测试歌曲",
		answerImage: []byte("answer"),
	}, "猜对了")
	if action == nil || action.Action != "send_msg" {
		t.Fatalf("结算 action=%+v", action)
	}
	message, ok := action.Params["message"].(onebot.Message)
	if !ok || len(message) != 3 {
		t.Fatalf("结算消息=%#v", action.Params["message"])
	}
	if message[2].Type != "image" {
		t.Fatalf("结算消息应包含答案图=%#v", message)
	}
}

// 确保 GuessRenderer 的接口签名不会意外偏离 draw.Client.RenderMulti。
type compileGuessRenderer struct{}

func (compileGuessRenderer) RenderMulti(context.Context, string, map[string]any) ([][]byte, error) {
	return nil, nil
}

var _ GuessRenderer = compileGuessRenderer{}

type recordingGuessRenderer struct {
	mu      sync.Mutex
	calls   []string
	payload []map[string]any
}

func (r *recordingGuessRenderer) RenderMulti(_ context.Context, name string, payload map[string]any) ([][]byte, error) {
	r.mu.Lock()
	r.calls = append(r.calls, name)
	r.payload = append(r.payload, payload)
	r.mu.Unlock()
	return [][]byte{[]byte("question"), []byte("answer")}, nil
}

func writeGuessMasterData(t *testing.T, root, server, filename, content string) {
	t.Helper()
	dir := filepath.Join(root, "ondemand", server)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, filename), []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func guessGroupEvent(groupID, userID int64, text string) onebot.MessageEvent {
	return onebot.MessageEvent{
		SelfID:      1,
		UserID:      userID,
		MessageType: "group",
		GroupID:     groupID,
		Message:     onebot.Message{onebot.Text(text)},
	}
}

func TestGuessModuleMatchesMusicAnswerAndUsesDrawMulti(t *testing.T) {
	root := t.TempDir()
	writeGuessMasterData(t, root, "cn", "musics.json", `[{"id":7,"title":"测试曲","assetbundleName":"test_asset","publishedAt":0}]`)

	renderer := &recordingGuessRenderer{}
	module := NewGuessModule(masterdata.New(root), renderer, nil, nil, nil, root)
	defer module.Close()
	event := guessGroupEvent(700, 11, "pjsk")
	action := module.handleStart(context.Background(), router.Request{Event: event, Arg: "cnpjsk非人类猜曲"})
	if action == nil {
		t.Fatal("启动猜曲应返回 action")
	}
	if got, ok := module.manager.current(700); !ok || got.answerID != 7 {
		t.Fatalf("猜曲会话=%+v active=%v", got, ok)
	}
	renderer.mu.Lock()
	if len(renderer.calls) != 1 || renderer.calls[0] != "guess_jacket" {
		t.Fatalf("draw calls=%v", renderer.calls)
	}
	if renderer.payload[0]["pjsk_type"] != 2 || renderer.payload[0]["size"] != 30 {
		t.Fatalf("draw payload=%#v", renderer.payload[0])
	}
	renderer.mu.Unlock()

	answerEvent := guessGroupEvent(700, 12, "测试曲")
	if got := module.HandleAnswer(context.Background(), answerEvent); got == nil {
		t.Fatal("正确曲名应返回结算 action")
	}
	if _, ok := module.manager.current(700); ok {
		t.Fatal("答对后会话应删除")
	}
}

func TestGuessModuleMatchesCardCharacterAlias(t *testing.T) {
	root := t.TempDir()
	writeGuessMasterData(t, root, "jp", "cards.json", `[{"id":8,"characterId":1,"assetbundleName":"card_asset","cardRarityType":"rarity_3","prefix":"测试卡","releaseAt":0}]`)
	writeGuessMasterData(t, root, "jp", "gameCharacters.json", `[]`)

	renderer := &recordingGuessRenderer{}
	resolver := cards.NewCharaAliasResolver(filepath.Join(root, "static"))
	module := NewGuessModule(masterdata.New(root), renderer, nil, resolver, nil, root)
	defer module.Close()
	event := guessGroupEvent(701, 21, "pjsk")
	if got := module.handleStart(context.Background(), router.Request{Event: event, Arg: "pjsk猜卡面"}); got == nil {
		t.Fatal("启动猜卡面应返回 action")
	}
	if got := module.HandleAnswer(context.Background(), guessGroupEvent(701, 22, "ICK")); got == nil {
		t.Fatal("角色别名应匹配")
	}
	if _, ok := module.manager.current(701); ok {
		t.Fatal("卡面答对后会话应删除")
	}
}
