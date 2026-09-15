// go-pjsk-helper: kndbot 的 pjsk 辅助 sidecar。
//
// 模块：
//   - masterdata: 定时差分下载各服主数据，原子写入共享 volume，并回调 kndbot 预热缓存
//   - ranking:    定时抓取排行 API，写入 sktop100.json 与共享 SQLite
//   - forecast:   生成 local/33kit/moe/sekarun 预测缓存
//   - suite:      Haruki suite/profile 缓存代理 + b30 预计算
package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/kazuhira/go-pjsk-helper/internal/assets"
	"github.com/kazuhira/go-pjsk-helper/internal/forecast"
	"github.com/kazuhira/go-pjsk-helper/internal/masterdata"
	"github.com/kazuhira/go-pjsk-helper/internal/ranking"
	"github.com/kazuhira/go-pjsk-helper/internal/sheets"
	"github.com/kazuhira/go-pjsk-helper/internal/suite"
	"github.com/kazuhira/go-pjsk-helper/internal/translation"
)

func envOr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func parseDuration(key, def string) time.Duration {
	value := envOr(key, def)
	parsed, err := time.ParseDuration(value)
	if err != nil || parsed <= 0 {
		log.Printf("无效的 %s=%q，使用默认值 %s", key, value, def)
		parsed, _ = time.ParseDuration(def)
	}
	return parsed
}

func main() {
	dataDir := envOr("PJSK_DATA_DIR", "/data/pjsk")
	configPath := envOr("PJSK_SERVERS_YAML", "/config/pjsk/servers.yaml")
	listenAddr := envOr("LISTEN_ADDR", ":8000")
	callbackURL := envOr("KNDBOT_CALLBACK_URL", "") // 例: http://kndbot:8080/pjsk/internal/masterdata-updated

	cfg, err := masterdata.LoadConfig(configPath)
	if err != nil {
		log.Fatalf("load servers.yaml: %v", err)
	}

	onDemandDir := dataDir + "/ondemand"
	mdSyncer := masterdata.NewSyncer(cfg, onDemandDir, callbackURL)
	rkCollector := ranking.NewCollector(cfg, onDemandDir, onDemandDir+"/database", os.Getenv("GAMEAPI_TOKEN"))
	suiteProxy := suite.NewProxy(cfg, onDemandDir+"/suite", onDemandDir)
	assetDL := assets.NewDownloader(cfg, onDemandDir)
	translationSyncer := translation.NewSyncer(cfg, onDemandDir)
	sheetSyncer := sheets.NewSyncer(cfg, dataDir)
	settingsPath := envOr("PJSK_SETTINGS_YAML", filepath.Join(filepath.Dir(configPath), "settings.yaml"))
	forecastGenerator := forecast.NewGenerator(dataDir, settingsPath)

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	// 定时任务。每项均先执行一次，后续失败由下一轮及各自的源重试恢复。
	if envOr("ENABLE_MASTERDATA_SYNC", "1") == "1" {
		go mdSyncer.Run(ctx, parseDuration("MASTERDATA_SYNC_INTERVAL", "1h"))
	}
	if envOr("ENABLE_RANKING_COLLECT", "0") == "1" {
		go rkCollector.Run(ctx, parseDuration("RANKING_COLLECT_INTERVAL", "1s"))
	}
	if envOr("ENABLE_TRANSLATION_SYNC", "1") == "1" {
		go translationSyncer.Run(ctx, parseDuration("TRANSLATION_SYNC_INTERVAL", "1h"))
	}
	if envOr("ENABLE_DIFFICULTY_SHEET_SYNC", "1") == "1" {
		go sheetSyncer.Run(ctx, parseDuration("DIFFICULTY_SHEET_SYNC_INTERVAL", "24h"))
	}
	if envOr("ENABLE_FORECAST_GENERATION", "1") == "1" {
		go forecastGenerator.Run(ctx, parseDuration("FORECAST_SYNC_INTERVAL", "20m"))
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
	mux.HandleFunc("POST /masterdata/refresh", mdSyncer.HandleRefresh)
	mux.HandleFunc("GET /suite/{region}/{uid}", suiteProxy.HandleSuite)
	mux.HandleFunc("GET /b30/{region}/{uid}", suiteProxy.HandleB30)
	mux.HandleFunc("GET /ranking/{region}/latest", rkCollector.HandleLatest)
	mux.HandleFunc("POST /assets/fetch", assetDL.HandleFetch)
	mux.HandleFunc("POST /assets/prefetch", assetDL.HandlePrefetch)
	mux.HandleFunc("POST /translation/refresh", translationSyncer.HandleRefresh)
	mux.HandleFunc("POST /difficulty/refresh", sheetSyncer.HandleRefresh)
	mux.HandleFunc("POST /forecast/refresh", forecastGenerator.HandleRefresh)

	srv := &http.Server{Addr: listenAddr, Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutdownCtx)
	}()

	log.Printf("go-pjsk-helper listening on %s (data=%s)", listenAddr, dataDir)
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}
