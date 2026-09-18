// go-pjsk-bot：PJSK 业务的独立 Go 微服务入口。
//
// 只负责 pjsk 指令的业务逻辑：通过 OneBot v11 正向或反向 WS 收发消息，
// 出图委托 pjsk-draw 微服务，数据复用 go-pjsk-helper / sekai-api 等 sidecar。
// 与 Python 主进程通过命令所有权（KND_GO_OWNED_COMMANDS）互斥。
package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/cards"
	"github.com/kazuhira/go-pjsk-bot/internal/config"
	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/gameapi"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/msrsub"
	"github.com/kazuhira/go-pjsk-bot/internal/mysekaidata"
	"github.com/kazuhira/go-pjsk-bot/internal/notifysub"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/pjsk"
	"github.com/kazuhira/go-pjsk-bot/internal/profile"
	"github.com/kazuhira/go-pjsk-bot/internal/remotelive"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/serverconfig"
	"github.com/kazuhira/go-pjsk-bot/internal/settings"
	"github.com/kazuhira/go-pjsk-bot/internal/skforecast"
	"github.com/kazuhira/go-pjsk-bot/internal/skstore"
	"github.com/kazuhira/go-pjsk-bot/internal/sksub"
	"github.com/kazuhira/go-pjsk-bot/internal/store"
	"github.com/kazuhira/go-pjsk-bot/internal/subscription"
)

// deps 汇总各业务模块的依赖，注册时按需取用。
type deps struct {
	db              *store.Store
	draw            *draw.Client
	resolver        *pjsk.UserResolver
	fetcher         *profile.Fetcher // 可能为 nil（servers.yaml 缺失时）
	serverCfg       *serverconfig.Config
	md              *masterdata.Loader // 主数据读取器
	api             *gameapi.Client    // 游戏 API 客户端
	settings        *settings.Settings // settings.yaml（可能为 nil）
	dataDir         string             // pjsk 数据目录
	staticDir       string             // pjsk 静态资源目录
	msFetcher       *mysekaidata.Fetcher
	chara           *cards.CharaAliasResolver
	skStore         *skstore.Store
	remoteLive      *remotelive.Store
	skSub           *sksub.Store
	msrSub          *msrsub.Store
	notify          *notifysub.Store
	supers          []int64 // 超级用户 QQ 列表
	sekaiURL        string  // sekai-api 基址
	sekaiTok        string  // sekai-api token
	sekaiControlURL string  // 旧部署宿主控制服务（可选）
	sekaiControlTok string  // 旧部署宿主控制 token（可选）
	helperURL       string  // go-pjsk-helper 基址
	remoteAccount   string  // remote 自动打歌账号
	remoteRegion    string  // remote 默认区服
	liveInterval    int     // live 循环间隔秒数
	liveAutoStop    string  // live 每日自动停止时间
	queryRefresh    *pjsk.QueryRefresher
	newCardSource   *pjsk.NewCardSubscriptionSource
}

func main() {
	cfg, err := config.Load()
	if err != nil {
		log.Fatalf("[pjskbot] 配置错误: %v", err)
	}
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

	// 服务器配置（servers.yaml）：档案与 mysekai 数据获取共用。
	sc, err := serverconfig.Load(cfg.ConfigDir)
	if err != nil {
		log.Printf("[pjskbot] 警告：加载 servers.yaml 失败，档案/MySekai 型指令不可用: %v", err)
	}

	// 档案拉取器 / MySekai 数据获取器（依赖 servers.yaml）。
	var fetcher *profile.Fetcher
	var msFetcher *mysekaidata.Fetcher
	if sc != nil {
		fetcher = profile.NewFetcher(api, md, sc)
		msFetcher = mysekaidata.NewFetcher(api, sc, cfg.DataDir)
		log.Printf("[pjskbot] 档案/MySekai 拉取器已就绪")
	}

	// 角色别名解析器（组卡与其它角色参数解析共用）。
	charaResolver := cards.NewCharaAliasResolver(cfg.StaticDir)

	// sk 榜线时序库（只读 sqlite，由 go-pjsk-helper 采集写入）。
	skStore := skstore.New(cfg.DataDir)
	defer skStore.Close()
	// remote 自动打歌记录库（只读 sqlite，由 Python remote live 写入）。
	remoteLiveStore := remotelive.New(cfg.DataDir)

	// sk 分数变动订阅库（读写 sqlite，与 Python 共享文件）。
	skSubStore := sksub.New(cfg.DataDir)

	// MySekai 数据更新推送订阅库（读写 sqlite，与 Python 共享文件）。
	msrSubStore := msrsub.New(cfg.DataDir)

	// 新曲/vlive/新卡 订阅库（读写 sqlite，与 Python 共享文件）。
	notifyStore := notifysub.New(cfg.DataDir)
	queryRefresh := pjsk.NewQueryRefresher(md, drawClient, cfg.HelperServiceURL)
	newCardSource := pjsk.NewNewCardSubscriptionSource(md, drawClient, sc, cfg.DataDir)

	d := deps{
		db: db, draw: drawClient, resolver: pjsk.NewUserResolver(db),
		fetcher: fetcher, serverCfg: sc, md: md, api: api, settings: set, dataDir: cfg.DataDir, staticDir: cfg.StaticDir,
		msFetcher:       msFetcher,
		chara:           charaResolver,
		skStore:         skStore,
		remoteLive:      remoteLiveStore,
		skSub:           skSubStore,
		msrSub:          msrSubStore,
		notify:          notifyStore,
		supers:          cfg.Superusers,
		sekaiURL:        cfg.SekaiAPIURL,
		sekaiTok:        cfg.SekaiApiToken,
		sekaiControlURL: cfg.SekaiControlURL,
		sekaiControlTok: cfg.SekaiControlToken,
		helperURL:       cfg.HelperServiceURL,
		remoteAccount:   cfg.RemoteAccount,
		remoteRegion:    cfg.RemoteRegion,
		liveInterval:    cfg.LiveInterval,
		liveAutoStop:    cfg.LiveAutoStop,
		queryRefresh:    queryRefresh,
		newCardSource:   newCardSource,
	}

	// 生产 standalone 模式直接接管 Go 二进制中注册的全部指令；
	// KND_GO_OWNED_COMMANDS 仅用于保留灰度/回滚路径。
	ownership := router.ParseOwnership(os.Getenv("KND_GO_OWNED_COMMANDS"))
	if cfg.Standalone {
		ownership = router.All()
		log.Printf("[pjskbot] standalone：接管全部 Go 注册指令，忽略 KND_GO_OWNED_COMMANDS")
	} else if ownership.Empty() {
		log.Printf("[pjskbot] 警告：KND_GO_OWNED_COMMANDS 为空，本服务不会接管任何指令（全部由 Python 处理）")
	}

	r := router.New([]string{"/", ""}, ownership)
	registerCommands(r, d)
	logf("[pjskbot] 路由已注册 triggers=%d standalone=%t log_messages=%t", r.TriggerCount(), cfg.Standalone, cfg.LogMessages)
	remote := pjsk.NewRemoteModule(pjsk.RemoteConfig{
		APIURL:       cfg.SekaiAPIURL,
		APIToken:     cfg.SekaiApiToken,
		ControlURL:   cfg.SekaiControlURL,
		ControlToken: cfg.SekaiControlToken,
		Region:       cfg.RemoteRegion,
		Account:      cfg.RemoteAccount,
		LiveInterval: time.Duration(cfg.LiveInterval) * time.Second,
		LiveAutoStop: cfg.LiveAutoStop,
		DataDir:      cfg.DataDir,
		Masterdata:   md,
		Superusers:   cfg.Superusers,
	})
	remote.Register(r)
	defer remote.Close()
	upload := pjsk.NewUploadModule(cfg.DataDir)
	upload.Register(r)
	defer upload.Close()
	maintenance := pjsk.NewUpdateModule(d.md, d.dataDir, d.helperURL, d.supers)
	maintenance.Register(r)
	defer maintenance.Close()
	var botcheck *pjsk.BotcheckModule
	if cfg.UnibotCheck {
		botcheck = pjsk.NewBotcheckModule(d.dataDir, d.supers)
		botcheck.Register(r)
	}

	// 指令级冷却（CD）+ 防重入限流，对齐 Python 的 __plugin_cd_limit__；superuser 豁免。
	rateLimiter := pjsk.NewRateLimiter(cfg.Superusers)

	var guess *pjsk.GuessModule
	handler := func(event onebot.MessageEvent) *onebot.ActionRequest {
		started := time.Now()
		text := onebot.TruncateText(event.Message.PlainText(), 160)
		location := fmt.Sprintf("type=%s user=%d group=%d", event.MessageType, event.UserID, event.GroupID)
		if action := maintenance.HandleMessage(event); action != nil {
			logf("[pjskbot] maintenance handled %s text=%q action=%s elapsed=%s", location, text, onebot.ActionSummary(action), time.Since(started).Round(time.Millisecond))
			return action
		}
		req, h, ok := r.Match(event)
		if ok {
			if cfg.UnibotCheck {
				if blocked, action := botcheck.CheckGroup(context.Background(), event); blocked {
					logf("[pjskbot] botcheck blocked %s text=%q action=%s elapsed=%s", location, text, onebot.ActionSummary(action), time.Since(started).Round(time.Millisecond))
					return action
				}
			}
			logf("[pjskbot] command start command=%q raw=%q server=%s %s arg=%q", req.Command, req.RawCmd, req.Server.Name(), location, onebot.TruncateText(req.Arg, 120))
			action := rateLimiter.Wrap(context.Background(), req, h)
			logf("[pjskbot] command done command=%q raw=%q server=%s action=%s elapsed=%s", req.Command, req.RawCmd, req.Server.Name(), onebot.ActionSummary(action), time.Since(started).Round(time.Millisecond))
			return action
		}
		// guess 的活动会话需要捕获未命中任何命令的普通群消息作为答案。
		if guess != nil {
			action := guess.HandleAnswer(context.Background(), event)
			if action != nil {
				logf("[pjskbot] guess answer handled %s text=%q action=%s elapsed=%s", location, text, onebot.ActionSummary(action), time.Since(started).Round(time.Millisecond))
				return action
			}
		}
		if cfg.LogMessages && onebot.LooksLikeCommand(text) {
			logf("[pjskbot] command-like message missed %s text=%q elapsed=%s", location, text, time.Since(started).Round(time.Millisecond))
		} else {
			logf("[pjskbot] message missed %s elapsed=%s", location, time.Since(started).Round(time.Millisecond))
		}
		return nil
	}

	noticeHandler := func(notice onebot.NoticeEvent) *onebot.ActionRequest {
		if action := upload.HandleNotice(notice); action != nil {
			return action
		}
		return remote.HandleNotice(notice)
	}
	client := onebot.NewClientWithNotice(cfg.OneBotWSURL, cfg.OneBotToken, handler, noticeHandler, logf)
	client.SetLogMessages(cfg.LogMessages)
	if cfg.UnibotCheck {
		botcheck.SetClient(client)
		if ownership.Owns("查询uni分布式") || ownership.Owns("添加uni分布式") || cfg.Standalone {
			go botcheck.Run(ctx, 24*time.Hour)
		}
	}
	if db != nil && os.Getenv("ENABLE_ALIAS_SYNC") != "0" {
		aliasSyncer := pjsk.NewAliasSyncer(d.md, db, os.Getenv("PJSK_MUSIC_ALIAS_API_URL"))
		go aliasSyncer.Run(ctx, 24*time.Hour)
	}
	guess = pjsk.NewGuessModule(d.md, d.draw, d.db, d.chara, client, d.dataDir)
	guess.Register(r)
	defer guess.Close()

	transportDone := make(chan error, 1)
	go func() {
		transportDone <- runOneBot(ctx, cfg, client)
	}()

	// 后台订阅调度：只启动当前由 Go 接管的推送类型，避免灰度期间与 Python 重复推送。
	notifySource := pjsk.NewMasterdataSubscriptionSource(md, drawClient)
	var msrSource subscription.MsrSource
	if msFetcher != nil && (msFetcher.SupportsUploadTime(0) || msFetcher.SupportsUploadTime(1) || msFetcher.SupportsUploadTime(2)) {
		source := pjsk.NewMSRSubscriptionSource(msFetcher, sc, api, drawClient)
		source.SetBindStore(db)
		msrSource = source
	}
	skSource := pjsk.NewSKSubscriptionSource(md, skStore, drawClient)
	notifyWorker := subscription.NewNotifyWorker(subscription.WorkerOptions{
		Notify:  notifyStore,
		MSR:     msrSubStore,
		SK:      skSubStore,
		Sender:  client,
		Music:   notifySource,
		VLive:   notifySource,
		NewCard: newCardSource,
		MSRFeed: msrSource,
		SKFeed:  skSource,
		Logf:    logf,
	})
	jobs := ownedSubscriptionJobs(ownership)
	if len(jobs) == 0 {
		log.Printf("[pjskbot] 未接管任何订阅推送，跳过 Go 订阅调度器")
	} else {
		notifyScheduler := subscription.NewSubscribeScheduler(notifyWorker, subscription.SchedulerOptions{Jobs: jobs})
		notifyScheduler.SetLogger(logf)
		if err := notifyScheduler.Start(ctx); err != nil {
			log.Printf("[pjskbot] 订阅调度器启动失败: %v", err)
		} else {
			defer notifyScheduler.Close()
			log.Printf("[pjskbot] 订阅调度器已启动：%v", jobs)
		}
	}

	if cfg.OneBotMode == "reverse" {
		log.Printf("[pjskbot] 启动：onebot=reverse listen=%s%s draw=%s", cfg.OneBotListenAddr, cfg.OneBotPath, cfg.DrawServiceURL)
	} else {
		log.Printf("[pjskbot] 启动：onebot=%s draw=%s", cfg.OneBotWSURL, cfg.DrawServiceURL)
	}

	if err := <-transportDone; err != nil && !errors.Is(err, context.Canceled) {
		log.Printf("[pjskbot] 退出: %v", err)
		os.Exit(1)
	}
}

func runOneBot(ctx context.Context, cfg config.Config, client *onebot.Client) error {
	switch cfg.OneBotMode {
	case "reverse":
		return client.Serve(ctx, cfg.OneBotListenAddr, cfg.OneBotPath)
	case "forward":
		return client.Run(ctx)
	default:
		return fmt.Errorf("unsupported PJSKBOT_ONEBOT_MODE %q (want forward or reverse)", cfg.OneBotMode)
	}
}

func ownedSubscriptionJobs(ownership router.Ownership) []string {
	jobs := make([]string, 0, 5)
	if ownership.Owns("pjsk开启新曲通知") {
		jobs = append(jobs, subscription.JobMusic)
	}
	if ownership.Owns("pjsk开启live通知") {
		jobs = append(jobs, subscription.JobVLive)
	}
	if ownership.Owns("pjsk开启新卡通知") {
		jobs = append(jobs, subscription.JobNewCard)
	}
	if ownership.Owns("msr订阅") {
		jobs = append(jobs, subscription.JobMSR)
	}
	if ownership.Owns("订阅sk") {
		jobs = append(jobs, subscription.JobSK)
	}
	return jobs
}

// registerCommands 注册所有 pjsk 指令。随业务模块迁移逐步扩充。
func registerCommands(r *router.Router, d deps) {
	nowMS := func() int64 { return time.Now().UnixMilli() }

	// 出图型模块：只依赖 pjsk-draw（+ 本地主数据）。组卡由 Python allium 插件处理。
	pjsk.NewYcmModule(d.draw).Register(r)
	pjsk.NewGachaModule(d.md, d.draw).Register(r)
	pjsk.NewSongModule(d.md, d.db, d.draw, d.dataDir, d.chara).Register(r)
	pjsk.NewPreviewModule(d.md, d.db, d.draw, d.dataDir, d.chara).Register(r)
	// 难度排行：主体只需主数据+出图；玩家成绩段在 fetcher/db 可用时增强。
	pjsk.NewDiffRankModule(d.md, d.fetcher, d.db, d.draw).Register(r)
	// 卡牌一览：按团体/稀有度/属性/限定筛选出图。
	pjsk.NewCardBoxModule(d.md, d.draw, d.msFetcher, d.db, d.chara).Register(r)
	if d.serverCfg != nil {
		pjsk.NewCardAssetModule(d.md, d.serverCfg, d.dataDir).Register(r)
	}
	// 卡面详情：解析卡面核心信息出图。
	pjsk.NewCardInfoModule(d.md, d.draw, d.queryRefresh).Register(r)
	// 卡面查询概览：按角色/团体+多维筛选出图。
	pjsk.NewFindCardModule(d.md, d.draw, d.staticDir, d.queryRefresh).Register(r)
	// 活动信息：查询当前/指定活动信息出图。
	pjsk.NewEventModule(d.md, d.draw, d.chara, d.queryRefresh).Register(r)
	// 订阅相关：虚拟live/新卡列表出图（订阅开关/推送作为增量）。
	pjsk.NewSubscribeModule(d.md, d.draw, d.notify, d.supers, d.newCardSource).Register(r)
	// sk 时速/排名线：读时序 sqlite + 时速计算 → 出图（WL分榜/查榜/预测作为增量）。
	pjsk.NewSkModule(d.md, d.skStore, d.draw, skforecast.NewWithHelper(d.dataDir, d.helperURL), d.db, d.chara, d.skSub, d.supers).Register(r)
	pjsk.NewSKAPIModule(d.dataDir, d.supers).Register(r)
	// skme：只读 remote_live 记录并复用 pjsk-draw 曲线渲染；不触碰 remote 控制/调度。
	pjsk.NewSkMeModule(d.md, d.remoteLive, d.draw, d.remoteAccount, d.remoteRegion, d.supers).Register(r)

	// DB 型模块：数据库不可用时跳过注册。
	if d.db != nil {
		pjsk.NewBindModule(d.db).Register(r)
	}

	// 档案型模块：需要 servers.yaml。
	if d.fetcher != nil {
		pjsk.NewRopModule(d.fetcher, d.resolver, d.draw).Register(r)
		pjsk.NewB30Module(d.fetcher, d.md, d.resolver, d.draw).Register(r)
		pjsk.NewProfileModule(d.fetcher, d.resolver, d.draw, d.staticDir).Register(r)
		// 逮捕：收歌统计 + 排位（排位段可缺 settings 时降级）。
		pjsk.NewArrestModule(d.fetcher, d.api, d.md, d.resolver, d.settings, nowMS).Register(r)
	}

	// MySekai 资源查询（msr 三图）：需要 servers.yaml + 绑定库 + draw。
	if d.msFetcher != nil && d.db != nil {
		pjsk.NewMysekaiModule(d.msFetcher, d.db, d.draw, d.md, d.chara, d.staticDir, d.msrSub).Register(r)
	}

	// CN 服 MSR 群白名单管理（superuser）：需要静态目录存放白名单文件。
	pjsk.NewCnMsrModule(d.staticDir, d.supers).Register(r)

	// 远程打歌分数配置（superuser）：调用 sekai-api /config/score。
	pjsk.NewRemoteScoreModule(d.sekaiURL, d.sekaiTok, d.supers).Register(r)

	// 排位查询：需要 settings.yaml（rank_match_api_base_url）。
	if d.settings != nil {
		pjsk.NewRkModule(d.api, d.md, d.db, d.settings, d.draw, nowMS).Register(r)
	}
}
