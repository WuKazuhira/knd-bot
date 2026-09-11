package pjsk

import (
	"testing"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

func TestDeckRegisterAllKindsAndServerPrefixes(t *testing.T) {
	r := router.New([]string{"/", ""}, router.All())
	NewDeckModule(nil, nil, nil, nil, nil, nil, nil).Register(r)
	cases := []struct {
		text    string
		command string
		server  router.ServerType
	}{
		{"活动组卡", "活动组卡", router.ServerJP}, {"cn活动组卡", "活动组卡", router.ServerCN}, {"tw活动组卡", "活动组卡", router.ServerTW},
		{"挑战组卡 miku", "挑战组卡", router.ServerJP}, {"cn挑战组卡", "挑战组卡", router.ServerCN},
		{"长草组卡", "长草组卡", router.ServerJP}, {"tw长草组卡", "长草组卡", router.ServerTW},
		{"加成组卡 120", "加成组卡", router.ServerJP}, {"cn加成组卡 120", "加成组卡", router.ServerCN},
	}
	for _, tc := range cases {
		req, _, ok := r.Match(onebot.MessageEvent{UserID: 1, Message: onebot.Message{onebot.Text(tc.text)}})
		if !ok || req.Command != tc.command || req.Server != tc.server {
			t.Errorf("%q => ok=%v command=%q server=%v", tc.text, ok, req.Command, req.Server)
		}
	}
}
