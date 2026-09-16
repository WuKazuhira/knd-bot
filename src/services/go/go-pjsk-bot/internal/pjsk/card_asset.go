package pjsk

import (
	"bytes"
	"context"
	"fmt"
	"image"
	"image/jpeg"
	_ "image/png"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/assets"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/serverconfig"
)

const cardAssetMaxBytes = 64 << 20

// CardAssetModule 实现 card/cncard/twcard：下载并缓存卡面原图，再转成 JPG 发送。
type CardAssetModule struct {
	md      *masterdata.Loader
	servers *serverconfig.Config
	dataDir string
	http    *http.Client
}

func NewCardAssetModule(md *masterdata.Loader, servers *serverconfig.Config, dataDir string) *CardAssetModule {
	return &CardAssetModule{
		md:      md,
		servers: servers,
		dataDir: dataDir,
		http:    &http.Client{Timeout: 90 * time.Second},
	}
}

func (m *CardAssetModule) Register(r *router.Router) {
	r.RegisterNumericSuffix("card", nil, m.handle)
}

func (m *CardAssetModule) handle(ctx context.Context, req router.Request) *onebot.ActionRequest {
	opts := parseQueryOptions(req.Arg)
	cardID, err := strconv.Atoi(strings.TrimSpace(opts.Arg))
	if err != nil || cardID <= 0 {
		return onebot.ReplyText(req.Event, "请提供有效的卡面 ID", false)
	}
	images, err := m.loadCardImages(ctx, cardID, int(req.Server), opts.Refresh)
	if err != nil {
		return onebot.ReplyText(req.Event, "获取卡面资源失败，请稍后再试", false)
	}
	message := make(onebot.Message, 0, len(images))
	for _, imageBytes := range images {
		message = append(message, onebot.ImageBytes(base64Encode(imageBytes)))
	}
	return onebot.SendMessageAction(req.Event, message)
}

func (m *CardAssetModule) loadCardImages(ctx context.Context, cardID, server int, refresh bool) ([][]byte, error) {
	cards, err := m.md.Load("cards.json", server)
	if err != nil {
		return nil, err
	}
	var card map[string]any
	for _, item := range cards {
		if intField(item, "id") == cardID {
			card = item
			break
		}
	}
	if card == nil {
		return nil, os.ErrNotExist
	}
	bundle := strings.TrimSpace(strField(card, "assetbundleName"))
	if bundle == "" {
		return nil, os.ErrNotExist
	}
	files := []string{"card_normal.png"}
	if rarity := strField(card, "cardRarityType"); rarity == "rarity_3" || rarity == "rarity_4" {
		files = append(files, "card_after_training.png")
	}

	cache := assets.New(filepath.Join(m.dataDir, serverCode(server)), m.http, cardAssetMaxBytes)
	images := make([][]byte, 0, len(files))
	for _, name := range files {
		imageBytes, err := m.loadJPEG(ctx, cache, server, bundle, name, refresh)
		if err != nil {
			return nil, err
		}
		images = append(images, imageBytes)
	}
	return images, nil
}

func (m *CardAssetModule) loadJPEG(ctx context.Context, cache *assets.Client, server int, bundle, name string, refresh bool) ([]byte, error) {
	dir := filepath.Join("startapp", "character", "member", bundle)
	pngRel := filepath.ToSlash(filepath.Join(dir, name))
	jpgRel := strings.TrimSuffix(pngRel, filepath.Ext(pngRel)) + ".jpg"
	if !refresh {
		if cached, err := cache.Read(jpgRel); err == nil {
			return cached, nil
		}
	}

	var raw []byte
	var err error
	urls := m.servers.AssetURLs(server, dir, name)
	if len(urls) == 0 {
		return nil, fmt.Errorf("no asset source configured for %s", pngRel)
	}
	for _, url := range urls {
		if refresh {
			raw, err = cache.FetchFresh(ctx, url, pngRel)
		} else {
			raw, err = cache.Fetch(ctx, url, pngRel)
		}
		if err == nil {
			break
		}
	}
	if err != nil {
		return nil, err
	}
	decoded, _, err := image.Decode(bytes.NewReader(raw))
	if err != nil {
		return nil, err
	}
	var out bytes.Buffer
	if err := jpeg.Encode(&out, decoded, &jpeg.Options{Quality: 95}); err != nil {
		return nil, err
	}
	jpgPath, err := cache.Path(jpgRel)
	if err != nil {
		return nil, err
	}
	if err := writeAtomic(jpgPath, out.Bytes()); err != nil {
		return nil, err
	}
	return out.Bytes(), nil
}

func writeAtomic(path string, data []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".tmp-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, path)
}
