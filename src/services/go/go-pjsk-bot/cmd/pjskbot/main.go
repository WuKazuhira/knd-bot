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
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/config"
	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/gameapi"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/pjsk"
	"github.com/kazuhira/go-pjsk-bot/internal/profile"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/serverconfig"
	"github.com/kazuhira/go-pjsk-bot/internal/settings"
	"github.com/kazuhira/go-pjsk-bot/internal/store"
)

// deps 汇总各业务模块的依赖，注册时按需取用。
type deps struct {
	db       *store.Store
	draw     *draw.Client
	resolver *pjsk.UserResolver
	fetcher  *profile.Fetcher   // 可能为 nil（servers.yaml 缺失时）
	md       *masterdata.Loader // 主数据读取器
	api      *gameapi.Client    // 游戏 API 客户端
	settings *settings.Settings // settings.yaml（可能为 nil）
	dataDir  string             // pjsk 数据目录
}

func main() {
	cfg := config.Load()
	logf := func(format string, args ...any) { log.Printf(format, args...) }

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	drawClient := draw.New(cfg.DrawServiceURL)

	// 连接共享 PostgreSQL（失败不致命：DB 型指令不注册）。
	var db *store.Store
	if cfg.DatabaseURL != "" {
		if s, err := store.Open(ctx, cfg.DatabaseURL); err != nil {
			log.Printf("[pjskbot] 警告：连接数据库失败，DB 型指令不可用: %v", err)
		} else {
			db = s
			defer db.Close()
			log.Printf("[pjskbot] 数据库已连接")
		}
	} else {
		log.Printf("[pjskbot] 警告：DATABASE_URL 未配置，DB 型指令不可用")
	}

	// 主数据读取器（本地共享目录，无外部依赖，总是可用）。
	md := masterdata.New(cfg.DataDir)
	api := gameapi.New(cfg.GameApiToken)

	// settings.yaml（失败则依赖它的指令降级）。
	set, err := settings.Load(cfg.ConfigDir)
	if err != nil {
		log.Printf("[pjskbot] 警告：加载 settings.yaml 失败: %v", err)
	}

	// 档案拉取器依赖 servers.yaml（失败则 profile 型指令不可用）。
	var fetcher *profile.Fetcher
	if sc, err := serverconfig.Load(cfg.ConfigDir); err != nil {
		log.Printf("[pjskbot] 警告：加载 servers.yaml 失败，档案型指令不可用: %v", err)
	} else {
		fetcher = profile.NewFetcher(api, md, sc)
		log.Printf("[pjskbot] 档案拉取器已就绪")
	}

	d := deps{
		db: db, draw: drawClient, resolver: pjsk.NewUserResolver(db),
		fetcher: fetcher, md: md, api: api, settings: set, dataDir: cfg.DataDir,
	}

	// 命令所有权：只接管 KND_GO_OWNED_COMMANDS 中列出的 pjsk 指令。
	ownership := router.ParseOwnership(os.Getenv("KND_GO_OWNED_COMMANDS"))
	if ownership.Empty() {
		log.Printf("[pjskbot] 警告：KND_GO_OWNED_COMMANDS 为空，本服务不会接管任何指令（全部由 Python 处理）")
	}

	r := router.New([]string{"/", ""}, ownership)
	registerCommands(r, d)

	handler := func(event onebot.MessageEvent) *onebot.ActionRequest {
		req, h, ok := r.Match(event)
		if !ok {
			return nil
		}
		return h(context.Background(), req)
	}

	client := onebot.NewClient(cfg.OneBotWSURL, cfg.OneBotToken, handler, logf)

	log.Printf("[pjskbot] 启动：onebot=%s draw=%s helper=%s", cfg.OneBotWSURL, cfg.DrawServiceURL, cfg.HelperURL)
	if err := client.Run(ctx); err != nil && err != context.Canceled {
		log.Printf("[pjskbot] 退出: %v", err)
		os.Exit(1)
	}
}

// registerCommands 注册所有 pjsk 指令。随业务模块迁移逐步扩充。
func registerCommands(r *router.Router, d deps) {
	nowMS := func() int64 { return time.Now().UnixMilli() }

	// 出图型模块：只依赖 pjsk-draw（+ 本地主数据）。
	pjsk.NewYcmModule(d.draw).Register(r)
	pjsk.NewGachaModule(d.md, d.draw).Register(r)
	pjsk.NewSongModule(d.md, d.db, d.draw, d.dataDir).Register(r)
	// 难度排行：主体只需主数据+出图；玩家成绩段在 fetcher/db 可用时增强。
	pjsk.NewDiffRankModule(d.md, d.fetcher, d.db, d.draw).Register(r)

	// DB 型模块：数据库不可用时跳过注册。
	if d.db != nil {
		pjsk.NewBindModule(d.db).Register(r)
	}

	// 档案型模块：需要 servers.yaml。
	if d.fetcher != nil {
		pjsk.NewRopModule(d.fetcher, d.resolver, d.draw).Register(r)
		pjsk.NewB30Module(d.fetcher, d.md, d.resolver, d.draw).Register(r)
		pjsk.NewProfileModule(d.fetcher, d.resolver, d.draw).Register(r)
		// 逮捕：收歌统计 + 排位（排位段可缺 settings 时降级）。
		pjsk.NewArrestModule(d.fetcher, d.api, d.md, d.resolver, d.settings, nowMS).Register(r)
	}

	// 排位查询：需要 settings.yaml（rank_match_api_base_url）。
	if d.settings != nil {
		pjsk.NewRkModule(d.api, d.md, d.db, d.settings, d.draw, nowMS).Register(r)
	}
}
