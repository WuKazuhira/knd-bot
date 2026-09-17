package pjsk

import (
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/skforecast"
)

type numericSuffixRouteCase struct {
	name       string
	ownership  string
	text       string
	wantCmd    string
	wantArg    string
	wantServer router.ServerType
	register   func(*router.Router)
}

func TestNumericSuffixRegistrations(t *testing.T) {
	cases := []numericSuffixRouteCase{
		{"bind alias", "bind", "绑定1234567890123", "bind", "1234567890123", router.ServerJP, func(r *router.Router) { (&BindModule{}).Register(r) }},
		{"查时间", "查时间", "查时间123", "查时间", "123", router.ServerJP, func(r *router.Router) { (&BindModule{}).Register(r) }},
		{"逮捕", "逮捕", "逮捕1234567890123", "逮捕", "1234567890123", router.ServerJP, func(r *router.Router) { (&ArrestModule{}).Register(r) }},
		{"b30 alias", "pjsk b30", "b301234567890123", "pjsk b30", "1234567890123", router.ServerJP, func(r *router.Router) { (&B30Module{}).Register(r) }},
		{"profile alias", "烧烤档案", "个人信息1234567890123", "烧烤档案", "1234567890123", router.ServerJP, func(r *router.Router) { (&ProfileModule{}).Register(r) }},
		{"rop alias", "pjsk进度", "烧烤进度1234567890123", "pjsk进度", "1234567890123", router.ServerJP, func(r *router.Router) { (&RopModule{}).Register(r) }},
		{"rk", "rk", "rk123", "rk", "123", router.ServerJP, func(r *router.Router) { (&RkModule{}).Register(r) }},
		{"event", "event", "event123", "event", "123", router.ServerJP, func(r *router.Router) { (&EventModule{}).Register(r) }},
		{"event cn suffix", "event", "cnevent180", "event", "180", router.ServerCN, func(r *router.Router) { (&EventModule{}).Register(r) }},
		{"findevent alias", "findevent", "查活动123", "findevent", "123", router.ServerJP, func(r *router.Router) { (&EventModule{}).Register(r) }},
		{"findevent cn alias", "findevent", "cn查活动123", "findevent", "123", router.ServerCN, func(r *router.Router) { (&EventModule{}).Register(r) }},
		{"card", "card", "card1254", "card", "1254", router.ServerJP, func(r *router.Router) { (&CardAssetModule{}).Register(r) }},
		{"cardinfo", "cardinfo", "cardinfo1254", "cardinfo", "1254", router.ServerJP, func(r *router.Router) { (&CardInfoModule{}).Register(r) }},
		{"findcard alias", "findcard", "查卡1254", "findcard", "1254", router.ServerJP, func(r *router.Router) { (&FindCardModule{}).Register(r) }},
		{"song alias", "pjskinfo", "查曲811", "pjskinfo", "811", router.ServerJP, func(r *router.Router) { (&SongModule{}).Register(r) }},
		{"note count", "查物量", "查物量100", "查物量", "100", router.ServerJP, func(r *router.Router) { (&SongModule{}).Register(r) }},
		{"bpm alias", "pjskbpm", "bpm811", "pjskbpm", "811", router.ServerJP, func(r *router.Router) { (&SongModule{}).Register(r) }},
		{"bpm find", "查bpm", "查bpm180", "查bpm", "180", router.ServerJP, func(r *router.Router) { (&SongModule{}).Register(r) }},
		{"song alias query", "pjskalias", "查别称811", "pjskalias", "811", router.ServerJP, func(r *router.Router) { (&SongModule{}).Register(r) }},
		{"map preview", "谱面预览", "谱面预览811", "谱面预览", "811", router.ServerJP, func(r *router.Router) { (&PreviewModule{}).Register(r) }},
		{"skill preview", "技能预览", "技能预览811", "技能预览", "811", router.ServerJP, func(r *router.Router) { (&PreviewModule{}).Register(r) }},
		{"card box", "卡牌一览", "卡牌一览2023", "卡牌一览", "2023", router.ServerJP, func(r *router.Router) { (&CardBoxModule{}).Register(r) }},
		{"difficulty rank", "难度排行", "难度排行31", "难度排行", "31", router.ServerJP, func(r *router.Router) { (&DiffRankModule{}).Register(r) }},
		{"forecast", "sk预测", "sk预测123", "sk预测", "123", router.ServerJP, func(r *router.Router) { (&SkModule{forecast: &skforecast.Reader{}}).Register(r) }},
		{"forecast curve", "ycx曲线", "ycx曲线123", "ycx曲线", "123", router.ServerJP, func(r *router.Router) { (&SkModule{forecast: &skforecast.Reader{}}).Register(r) }},
		{"skme", "skme", "skme123", "skme", "123", router.ServerJP, func(r *router.Router) { (&SkMeModule{}).Register(r) }},
		{"mysekai blueprint", "msb", "msb17", "msb", "17", router.ServerJP, func(r *router.Router) { (&MysekaiModule{}).Register(r) }},
		{"mysekai furniture", "msf", "msf123", "msf", "123", router.ServerJP, func(r *router.Router) { (&MysekaiModule{}).Register(r) }},
		{"mysekai photo", "msp", "msp1", "msp", "1", router.ServerJP, func(r *router.Router) { (&MysekaiModule{}).Register(r) }},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			r := router.New([]string{"/", ""}, router.ParseOwnership(`[
				"`+tc.ownership+`"
			]`))
			tc.register(r)
			event := onebot.MessageEvent{
				SelfID: 1, UserID: 100, MessageID: 1,
				MessageType: "group", GroupID: 200,
				Message: onebot.Message{onebot.Text(tc.text)},
			}
			req, _, ok := r.Match(event)
			if !ok || req.Command != tc.wantCmd || req.Arg != tc.wantArg || req.Server != tc.wantServer {
				t.Fatalf("%q => ok=%v command=%q arg=%q server=%v, want command=%q arg=%q server=%v",
					tc.text, ok, req.Command, req.Arg, req.Server, tc.wantCmd, tc.wantArg, tc.wantServer)
			}
		})
	}
}
