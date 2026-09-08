// go-pjsk-bot：PJSK 业务的独立 Go 微服务入口。
//
// 只负责 pjsk 指令的业务逻辑：通过 OneBot v11 正向 WS 收发消息，
// 出图委托 pjsk-draw 微服务，数据复用 go-pjsk-helper / sekai-api 等 sidecar。
// 与 Python 主进程通过命令所有权（KND_GO_OWNED_COMMANDS）互斥。
package main

import (
	"context"
	"log"
	"os"
	"os/signal"
	"syscall"

	"github.com/kazuhira/go-pjsk-bot/internal/config"
	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

func main() {
	cfg := config.Load()
	logf := func(format string, args ...any) { log.Printf(format, args...) }

	drawClient := draw.New(cfg.DrawServiceURL)
	_ = drawClient // 业务模块接入后使用

	// 命令起始符：兼容带 / 与不带（对齐项目 COMMAND_START 常见配置）。
	r := router.New([]string{"/", ""})
	registerCommands(r)

	handler := func(event onebot.MessageEvent) *onebot.ActionRequest {
		req, h, ok := r.Match(event)
		if !ok {
			return nil
		}
		return h(context.Background(), req)
	}

	client := onebot.NewClient(cfg.OneBotWSURL, cfg.OneBotToken, handler, logf)

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	log.Printf("[pjskbot] 启动：onebot=%s draw=%s helper=%s", cfg.OneBotWSURL, cfg.DrawServiceURL, cfg.HelperURL)
	if err := client.Run(ctx); err != nil && err != context.Canceled {
		log.Printf("[pjskbot] 退出: %v", err)
		os.Exit(1)
	}
}

// registerCommands 注册所有 pjsk 指令。随业务模块迁移逐步扩充。
func registerCommands(r *router.Router) {
	// TODO: 各业务模块在此注册。骨架阶段先留空。
	_ = r
}
