// go-pjsk-helper: kndbot 的 pjsk 辅助 sidecar。
//
// 模块：
//   - masterdata: 定时差分下载各服主数据，原子写入共享 volume，并回调 kndbot 预热缓存
//   - ranking:    定时抓取排行 API，写入 sktop100.json 供 Python 端直接读取
//   - suite:      Haruki suite/profile 缓存代理 + b30 预计算
package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/kazuhira/go-pjsk-helper/internal/assets"
	"github.com/kazuhira/go-pjsk-helper/internal/masterdata"
	"github.com/kazuhira/go-pjsk-helper/internal/ranking"
	"github.com/kazuhira/go-pjsk-helper/internal/suite"
)

func envOr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
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

	mdSyncer := masterdata.NewSyncer(cfg, dataDir+"/masterdata", callbackURL)
	rkCollector := ranking.NewCollector(cfg, dataDir+"/masterdata", dataDir+"/database", os.Getenv("GAMEAPI_TOKEN"))
	suiteProxy := suite.NewProxy(cfg, dataDir+"/profile", dataDir+"/masterdata")
	assetDL := assets.NewDownloader(cfg, dataDir+"/masterdata")

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	// 定时任务
	if envOr("ENABLE_MASTERDATA_SYNC", "1") == "1" {
		interval, _ := time.ParseDuration(envOr("MASTERDATA_SYNC_INTERVAL", "1h"))
		go mdSyncer.Run(ctx, interval)
	}
	if envOr("ENABLE_RANKING_COLLECT", "0") == "1" {
		interval, _ := time.ParseDuration(envOr("RANKING_COLLECT_INTERVAL", "30s"))
		go rkCollector.Run(ctx, interval)
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
