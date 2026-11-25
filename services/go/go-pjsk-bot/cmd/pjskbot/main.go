package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
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

type deps struct {
	db            *store.Store
	draw          *draw.Client
	resolver      *pjsk.UserResolver
	fetcher       *profile.Fetcher
	serverCfg     *serverconfig.Config
	md            *masterdata.Loader
	api           *gameapi.Client
	settings      *settings.Settings
	deckSettings  settings.Deck
	dataDir       string
	staticDir     string
	msFetcher     *mysekaidata.Fetcher
	chara         *cards.CharaAliasResolver
	skStore       *skstore.Store
	remoteLive    *remotelive.Store
	skSub         *sksub.Store
	msrSub        *msrsub.Store
	notify        *notifysub.Store
	supers        []int64
	helperURL     string
	remoteAccount string
	remoteRegion  string
	queryRefresh  *pjsk.QueryRefresher
	newCardSource *pjsk.NewCardSubscriptionSource
}

func main() {
	if err := run(); err != nil {
		log.Printf("[pjskbot] 退出: %v", err)
		os.Exit(1)
	}
}

func run() error {
	cfg, err := config.Load()
	if err != nil {
		return fmt.Errorf("配置错误: %w", err)
	}
	logf := func(format string, args ...any) { log.Printf(format, args...) }

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	drawClient := draw.New(cfg.DrawServiceURL)

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

	md := masterdata.New(cfg.DataDir)
	api := gameapi.New(cfg.GameApiToken)

	set, err := settings.Load(cfg.ConfigDir)
	if err != nil {
		log.Printf("[pjskbot] 警告：加载 settings.yaml 失败: %v", err)
	}

	sc, err := serverconfig.Load(cfg.ConfigDir)
	if err != nil {
		log.Printf("[pjskbot] 警告：加载 servers.yaml 失败，档案/MySekai 型指令不可用: %v", err)
	}

	var fetcher *profile.Fetcher
	var msFetcher *mysekaidata.Fetcher
	if sc != nil {
		fetcher = profile.NewFetcher(api, md, sc)
		msFetcher = mysekaidata.NewFetcher(api, sc, cfg.DataDir)
		log.Printf("[pjskbot] 档案/MySekai 拉取器已就绪")
	}

	charaResolver := cards.NewCharaAliasResolver(cfg.StaticDir)

	skStore := skstore.New(cfg.DataDir)
	defer skStore.Close()
	remoteLiveStore := remotelive.New(cfg.DataDir)

	skSubStore := sksub.New(cfg.DataDir)
	defer skSubStore.Close()

	msrSubStore := msrsub.New(cfg.DataDir)
	defer msrSubStore.Close()

	notifyStore := notifysub.New(cfg.DataDir)
	defer notifyStore.Close()
	queryRefresh := pjsk.NewQueryRefresher(md, drawClient, cfg.HelperServiceURL)
	newCardSnapshotPath := strings.TrimSpace(os.Getenv("PJSKBOT_NEW_CARD_SNAPSHOT"))
	if newCardSnapshotPath == "" {
		newCardSnapshotPath = filepath.Join(cfg.DataDir, "ondemand", "jp", "local_new_card_snapshot.json")
	}
	newCardAssetBaseURL := strings.TrimSpace(os.Getenv("PJSKBOT_NEW_CARD_ASSET_BASE_URL"))
	if newCardAssetBaseURL == "" {
		newCardAssetBaseURL = "http://host.docker.internal:8082/jp-assets"
	}
	newCardSource := pjsk.NewNewCardSubscriptionSource(md, drawClient, sc, cfg.DataDir)
	if os.Getenv("PJSKBOT_LOCAL_NEW_CARD_ENABLED") == "1" {
		newCardSource = pjsk.NewNewCardSubscriptionSourceWithOptions(md, drawClient, sc, cfg.DataDir, pjsk.NewCardSourceOptions{
			SnapshotPath: newCardSnapshotPath,
			AssetBaseURL: newCardAssetBaseURL,
			State:        notifyStore,
			Logf:         logf,
		})
		log.Printf("[pjskbot] 新卡通知使用本地快照=%s 资产源=%s", newCardSnapshotPath, newCardAssetBaseURL)
	}

	runtimeRoot := strings.TrimSpace(os.Getenv("PJSKBOT_HARUKI_3D_RUNTIME_ROOT"))
	if runtimeRoot == "" {
		runtimeRoot = "/data/haruki-3d/jp"
	}
	costumeSource := newCardSource.SetCostumes(pjsk.NewCostumeRenderer(runtimeRoot, strings.TrimSpace(os.Getenv("PJSKBOT_HARUKI_3D_ENGINE_URL"))))

	d := deps{
		db: db, draw: drawClient, resolver: pjsk.NewUserResolver(db),
		fetcher: fetcher, serverCfg: sc, md: md, api: api, settings: set, dataDir: cfg.DataDir, staticDir: cfg.StaticDir,
		deckSettings:  set.Deck(cfg),
		msFetcher:     msFetcher,
		chara:         charaResolver,
		skStore:       skStore,
		remoteLive:    remoteLiveStore,
		skSub:         skSubStore,
		msrSub:        msrSubStore,
		notify:        notifyStore,
		supers:        cfg.Superusers,
		helperURL:     cfg.HelperServiceURL,
		remoteAccount: cfg.RemoteAccount,
		remoteRegion:  cfg.RemoteRegion,
		queryRefresh:  queryRefresh,
		newCardSource: newCardSource,
	}

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

	rateLimiter := pjsk.NewRateLimiter(cfg.Superusers)

	groups := store.NewGroupManager(cfg.GroupManagerPath, logf)
	supers := make(map[int64]bool, len(cfg.Superusers))
	for _, id := range cfg.Superusers {
		supers[id] = true
	}
	blocked := func(userID, groupID int64) bool {
		if ctx.Err() != nil {
			return true
		}
		if supers[userID] {
			return false
		}
		if groupID > 0 && groups.Blocked(groupID) {
			return true
		}
		if db == nil {
			return cfg.DatabaseURL != ""
		}
		queryCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
		defer cancel()
		banned, err := db.IsBanned(queryCtx, userID, groupID)
		if err != nil {
			if ctx.Err() == nil {
				logf("[pjskbot] 查询共享黑名单失败: %v", err)
			}
			return true
		}
		return banned
	}

	var guess *pjsk.GuessModule
	handler := func(event onebot.MessageEvent) *onebot.ActionRequest {
		if blocked(event.UserID, event.GroupID) {
			return nil
		}
		started := time.Now()
		text := onebot.TruncateText(event.Message.PlainText(), 160)
		location := fmt.Sprintf("type=%s user=%d group=%d", event.MessageType, event.UserID, event.GroupID)
		if action := upload.HandleMessage(ctx, event); action != nil {
			logf("[pjskbot] suite upload message handled action=%s", onebot.ActionSummary(action))
			return action
		}
		if action := maintenance.HandleMessage(ctx, event); action != nil {
			logf("[pjskbot] maintenance handled %s text=%q action=%s elapsed=%s", location, text, onebot.ActionSummary(action), time.Since(started).Round(time.Millisecond))
			return action
		}
		req, h, ok := r.Match(event)
		if ok {
			if cfg.UnibotCheck {
				if blocked, action := botcheck.CheckGroup(ctx, event); blocked {
					logf("[pjskbot] botcheck blocked %s text=%q action=%s elapsed=%s", location, text, onebot.ActionSummary(action), time.Since(started).Round(time.Millisecond))
					return action
				}
			}
			logf("[pjskbot] command start command=%q raw=%q server=%s %s arg=%q", req.Command, req.RawCmd, req.Server.Name(), location, onebot.TruncateText(req.Arg, 120))
			action := rateLimiter.Wrap(ctx, req, h)
			logf("[pjskbot] command done command=%q raw=%q server=%s action=%s elapsed=%s", req.Command, req.RawCmd, req.Server.Name(), onebot.ActionSummary(action), time.Since(started).Round(time.Millisecond))
			return action
		}
		// 猜题进行中时，没命中命令的群消息拿去当答案
		if guess != nil {
			action := guess.HandleAnswer(ctx, event)
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
		if notice.NoticeType != "offline_file" || notice.GroupID != 0 || blocked(notice.UserID, notice.GroupID) {
			return nil
		}
		return upload.HandleNotice(ctx, notice)
	}
	client := onebot.NewClientWithNotice(cfg.OneBotWSURL, cfg.OneBotToken, handler, noticeHandler, logf)
	upload.SetFileClient(client)
	client.SetLogMessages(cfg.LogMessages)
	client.SetAllowUnauthenticatedReverse(cfg.AllowUnauthenticatedOneBot)
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
		Costume: costumeSource,
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

	err = runOneBot(ctx, cfg, client)
	stop()
	if errors.Is(err, context.Canceled) {
		return nil
	}
	return err
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
		jobs = append(jobs, subscription.JobNewCard, subscription.JobCardCostume)
	}
	if ownership.Owns("pjsk开启新服装通知") {
		jobs = append(jobs, subscription.JobCostume)
	}
	if ownership.Owns("msr订阅") {
		jobs = append(jobs, subscription.JobMSR)
	}
	if ownership.Owns("订阅sk") {
		jobs = append(jobs, subscription.JobSK)
	}
	return jobs
}

func registerCommands(r *router.Router, d deps) {
	nowMS := func() int64 { return time.Now().UnixMilli() }

	pjsk.NewYcmModule(d.draw).Register(r)
	pjsk.NewGachaModule(d.md, d.draw).Register(r)
	pjsk.NewWorldBloomFrameModule(d.md, d.draw, d.db, d.fetcher, d.chara).Register(r)
	songs := pjsk.NewSongModule(d.md, d.db, d.draw, d.dataDir, d.chara)
	songs.Register(r)
	pjsk.NewDeckModule(d.md, d.draw, d.fetcher, d.resolver, songs, d.chara, d.dataDir, d.supers, d.deckSettings).Register(r)
	pjsk.NewPreviewModule(d.md, d.db, d.draw, d.dataDir, d.chara).Register(r)
	pjsk.NewDiffRankModule(d.md, d.fetcher, d.db, d.draw).Register(r)
	pjsk.NewCardBoxModule(d.md, d.draw, d.msFetcher, d.db, d.chara).Register(r)
	if d.serverCfg != nil {
		pjsk.NewCardAssetModule(d.md, d.serverCfg, d.dataDir).Register(r)
	}
	pjsk.NewCardInfoModule(d.md, d.draw, d.queryRefresh).Register(r)
	pjsk.NewFindCardModule(d.md, d.draw, d.staticDir, d.queryRefresh).Register(r)
	pjsk.NewEventModule(d.md, d.draw, d.chara, d.queryRefresh).Register(r)
	pjsk.NewSubscribeModule(d.md, d.draw, d.notify, d.supers, d.newCardSource).Register(r)
	pjsk.NewSkModule(d.md, d.skStore, d.draw, skforecast.NewWithHelper(d.dataDir, d.helperURL), d.db, d.chara, d.skSub, d.supers).Register(r)
	pjsk.NewSKAPIModule(d.dataDir, d.supers).Register(r)
	pjsk.NewSkMeModule(d.md, d.remoteLive, d.draw, d.remoteAccount, d.remoteRegion, d.supers).Register(r)

	if d.db != nil {
		pjsk.NewBindModule(d.db).Register(r)
	}

	if d.fetcher != nil {
		pjsk.NewRopModule(d.fetcher, d.resolver, d.draw).Register(r)
		pjsk.NewB30Module(d.fetcher, d.md, d.resolver, d.draw).Register(r)
		pjsk.NewProfileModule(d.fetcher, d.resolver, d.draw, d.staticDir).Register(r)
		pjsk.NewArrestModule(d.fetcher, d.api, d.md, d.resolver, d.settings, nowMS).Register(r)
	}

	if d.msFetcher != nil && d.db != nil {
		pjsk.NewMysekaiModule(d.msFetcher, d.db, d.draw, d.md, d.chara, d.dataDir, d.staticDir, d.msrSub).Register(r)
	}

	pjsk.NewCnMsrModule(d.dataDir, d.staticDir, d.supers).Register(r)

	if d.settings != nil {
		pjsk.NewRkModule(d.api, d.md, d.db, d.settings, d.draw, nowMS).Register(r)
	}
}
