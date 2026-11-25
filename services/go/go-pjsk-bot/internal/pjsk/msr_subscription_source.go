package pjsk

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/gameapi"
	"github.com/kazuhira/go-pjsk-bot/internal/msrsub"
	"github.com/kazuhira/go-pjsk-bot/internal/mysekaidata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/serverconfig"
	"github.com/kazuhira/go-pjsk-bot/internal/store"
	"github.com/kazuhira/go-pjsk-bot/internal/subscription"
)

const msrRecentWindow = 10 * time.Minute

type UploadTimeBatcher interface {
	FetchUploadTimes(context.Context, string, []msrsub.Subscription) ([]int64, error)
}

type UploadTimeBatchFunc func(context.Context, string, []msrsub.Subscription) ([]int64, error)

func (f UploadTimeBatchFunc) FetchUploadTimes(ctx context.Context, server string, subs []msrsub.Subscription) ([]int64, error) {
	return f(ctx, server, subs)
}

type msrImageCache struct {
	mu    sync.Mutex
	items map[string][][]byte
	order []string
	limit int
}

func newMSRImageCache(limit int) *msrImageCache {
	if limit < 1 {
		limit = 16
	}
	return &msrImageCache{items: make(map[string][][]byte), limit: limit}
}

func (c *msrImageCache) get(key string) ([][]byte, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	images, ok := c.items[key]
	if !ok {
		return nil, false
	}
	out := cloneImages(images)
	for i, k := range c.order {
		if k == key {
			c.order = append(c.order[:i], c.order[i+1:]...)
			break
		}
	}
	c.order = append(c.order, key)
	return out, true
}

func (c *msrImageCache) put(key string, images [][]byte) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if _, ok := c.items[key]; ok {
		c.items[key] = cloneImages(images)
		return
	}
	c.items[key] = cloneImages(images)
	c.order = append(c.order, key)
	for len(c.order) > c.limit {
		delete(c.items, c.order[0])
		c.order = c.order[1:]
	}
}

func cloneImages(images [][]byte) [][]byte {
	out := make([][]byte, len(images))
	for i, image := range images {
		out[i] = append([]byte(nil), image...)
	}
	return out
}

type MSRSubscriptionSource struct {
	fetcher *mysekaidata.Fetcher
	config  *serverconfig.Config
	api     *gameapi.Client
	draw    *draw.Client
	bind    *store.Store
	now     func() time.Time
	batcher UploadTimeBatcher
	cache   *msrImageCache
}

type MsrSubscriptionSource = MSRSubscriptionSource

func NewMSRSubscriptionSource(fetcher *mysekaidata.Fetcher, config *serverconfig.Config, api *gameapi.Client, d *draw.Client) *MSRSubscriptionSource {
	return &MSRSubscriptionSource{
		fetcher: fetcher,
		config:  config,
		api:     api,
		draw:    d,
		now:     time.Now,
		cache:   newMSRImageCache(16),
	}
}

func NewMsrSubscriptionSource(fetcher *mysekaidata.Fetcher, config *serverconfig.Config, api *gameapi.Client, d *draw.Client) *MSRSubscriptionSource {
	return NewMSRSubscriptionSource(fetcher, config, api, d)
}

func (s *MSRSubscriptionSource) SetUploadTimeBatcher(batcher UploadTimeBatcher) {
	s.batcher = batcher
}

func (s *MSRSubscriptionSource) SetBindStore(bind *store.Store) {
	s.bind = bind
}

func (s *MSRSubscriptionSource) FetchMSR(ctx context.Context, sub msrsub.Subscription) (subscription.FeedItem, bool, error) {
	items, err := s.FetchBatch(ctx, []msrsub.Subscription{sub})
	if err != nil {
		return subscription.FeedItem{}, false, err
	}
	if len(items) == 0 {
		return subscription.FeedItem{}, false, nil
	}
	return items[0], true, nil
}

func (s *MSRSubscriptionSource) FetchBatch(ctx context.Context, subs []msrsub.Subscription) ([]subscription.FeedItem, error) {
	results, err := s.FetchMSRBatch(ctx, subs)
	if err != nil {
		return nil, err
	}
	items := make([]subscription.FeedItem, 0, len(results))
	for _, result := range results {
		if result.Ready {
			items = append(items, result.FeedItem)
		}
	}
	return items, nil
}

func (s *MSRSubscriptionSource) FetchMSRBatch(ctx context.Context, subs []msrsub.Subscription) ([]subscription.MsrBatchItem, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if len(subs) == 0 {
		return nil, nil
	}
	byServer := make(map[string][]msrsub.Subscription)
	for _, sub := range subs {
		if sub.UID == "" {
			continue
		}
		byServer[sub.Server] = append(byServer[sub.Server], sub)
	}
	servers := make([]string, 0, len(byServer))
	for server := range byServer {
		servers = append(servers, server)
	}
	sort.Strings(servers)
	results := make([]subscription.MsrBatchItem, 0, len(subs))
	for _, server := range servers {
		serverSubs := byServer[server]
		// 没配 upload_time 接口的区服直接跳过，别卡住其他区服
		if s.batcher == nil && s.config != nil && s.config.MysekaiUploadTimeURL(serverTypeFromShort(server)) == "" {
			continue
		}
		times, err := s.fetchUploadTimes(ctx, server, serverSubs)
		if err != nil {
			return nil, err
		}
		if len(times) != len(serverSubs) {
			return nil, fmt.Errorf("服务器 %s upload_time 数量不匹配：收到 %d，期望 %d", server, len(times), len(serverSubs))
		}
		serverType := serverTypeFromShort(server)
		now := s.clock()
		for i, sub := range serverSubs {
			if !ShouldPushMSR(times[i], sub.LastPushTime, serverType, now) {
				continue
			}
			item, err := s.renderItem(ctx, sub, times[i])
			if err != nil {
				return nil, err
			}
			results = append(results, subscription.MsrBatchItem{
				SubscriptionID: sub.ID,
				FeedItem:       item,
				Ready:          true,
			})
		}
	}
	return results, nil
}

func (s *MSRSubscriptionSource) clock() time.Time {
	if s.now == nil {
		return time.Now()
	}
	return s.now()
}

func (s *MSRSubscriptionSource) fetchUploadTimes(ctx context.Context, server string, subs []msrsub.Subscription) ([]int64, error) {
	if s.batcher != nil {
		return s.batcher.FetchUploadTimes(ctx, server, subs)
	}
	if s.api == nil || s.config == nil {
		return nil, errors.New("MSR upload_time client is nil")
	}
	serverType := serverTypeFromShort(server)
	url := s.config.MysekaiUploadTimeURL(serverType)
	if url == "" {
		return nil, fmt.Errorf("服务器 %s 未配置 MySekai upload_time 接口", server)
	}
	pairs := make([][]string, 0, len(subs))
	for _, sub := range subs {
		mode := sub.Mode
		if mode == "" {
			mode = "latest"
		}
		pairs = append(pairs, []string{sub.UID, mode})
	}
	raw, err := s.api.PostJSON(ctx, url, pairs)
	if err != nil {
		return nil, err
	}
	return ParseMSRUploadTimes(raw)
}

func (s *MSRSubscriptionSource) renderItem(ctx context.Context, sub msrsub.Subscription, uploadHint int64) (subscription.FeedItem, error) {
	if s.fetcher == nil {
		return subscription.FeedItem{}, errors.New("MySekai fetcher is nil")
	}
	serverType := serverTypeFromShort(sub.Server)
	isPrivate := s.privateFor(ctx, sub, serverType)
	cacheKey := fmt.Sprintf("%s/%d/%s/%t/%d", sub.Server, serverType, sub.UID, isPrivate, uploadHint)
	if images, ok := s.cache.get(cacheKey); ok {
		return msrFeedItem(sub.Server, sub.UID, uploadHint, images), nil
	}

	type result struct {
		info map[string]any
		msg  string
		err  error
	}
	infoCh := make(chan result, 1)
	suiteCh := make(chan struct {
		data map[string]any
		msg  string
	}, 1)
	go func() {
		info, msg, err := s.fetcher.GetMysekaiInfo(ctx, sub.UID, serverType, "latest", false)
		infoCh <- result{info: info, msg: msg, err: err}
	}()
	go func() {
		data, msg := s.fetcher.GetSuiteData(ctx, sub.UID, serverType)
		suiteCh <- struct {
			data map[string]any
			msg  string
		}{data: data, msg: msg}
	}()
	infoResult := <-infoCh
	suiteResult := <-suiteCh
	if infoResult.err != nil {
		return subscription.FeedItem{}, infoResult.err
	}
	info := infoResult.info
	actualUpload := uploadHint
	if n, ok := int64Value(info["upload_time"]); ok && n > 0 {
		actualUpload = n
	}
	cacheKey = fmt.Sprintf("%s/%d/%s/%t/%d", sub.Server, serverType, sub.UID, isPrivate, actualUpload)
	if images, ok := s.cache.get(cacheKey); ok {
		return msrFeedItem(sub.Server, sub.UID, actualUpload, images), nil
	}
	profile := mysekaidata.ProfileFromSuiteData(sub.UID, suiteResult.data)
	payloads := BuildMSRDrawPayloadsForPrivate(profile, info, suiteResult.data, serverType, isPrivate, infoResult.msg, suiteResult.msg)
	if s.draw == nil {
		return subscription.FeedItem{}, errors.New("draw client is nil")
	}
	images := make([][]byte, 0, len(payloads))
	assetsComplete := true
	// 整批共享总超时，避免三张图无限拖长
	renderCtx, cancel := context.WithTimeout(ctx, 150*time.Second)
	defer cancel()
	for _, task := range payloads {
		rendered, meta, err := s.draw.RenderWithMeta(renderCtx, task.Name, task.Payload)
		if err != nil {
			return subscription.FeedItem{}, err
		}
		if len(rendered) != 1 {
			return subscription.FeedItem{}, fmt.Errorf("MSR 绘图任务 %s 返回 %d 张图片，应为 1 张", task.Name, len(rendered))
		}
		if complete, ok := meta["assets_complete"].(bool); !ok || !complete {
			assetsComplete = false
		}
		images = append(images, rendered[0])
	}
	// 缺资源或问号图不进整图缓存
	if assetsComplete {
		s.cache.put(cacheKey, images)
	}
	return msrFeedItem(sub.Server, sub.UID, actualUpload, images), nil
}

func (s *MSRSubscriptionSource) privateFor(ctx context.Context, sub msrsub.Subscription, serverType int) bool {
	if s.bind == nil {
		return false
	}
	qq, err := strconv.ParseInt(strings.TrimSpace(sub.QQID), 10, 64)
	if err != nil || qq <= 0 {
		return false
	}
	_, isPrivate, exists, err := s.bind.GetUserBind(ctx, qq, serverType)
	return err == nil && exists && isPrivate
}

type MSRDrawPayload struct {
	Name    string
	Payload map[string]any
}

func BuildMSRDrawPayloads(profile, info, suite map[string]any, serverType int, infoMsg, suiteMsg string) []MSRDrawPayload {
	return BuildMSRDrawPayloadsForPrivate(profile, info, suite, serverType, false, infoMsg, suiteMsg)
}

func BuildMSRDrawPayloadsForPrivate(profile, info, suite map[string]any, serverType int, isPrivate bool, infoMsg, suiteMsg string) []MSRDrawPayload {
	base := map[string]any{
		"profile":                 profile,
		"is_private":              isPrivate,
		"mysekai_info":            info,
		"pjsk_type":               serverType,
		"fast_render":             true,
		"asset_wait_budget":       5.0,
		"asset_fetch_concurrency": 8,
		"quality":                 82,
	}
	return []MSRDrawPayload{
		{Name: "mysekai_summary", Payload: merge(base, map[string]any{
			"suite_data": suite,
			"data_msg":   firstNonEmpty(infoMsg, suiteMsg),
		})},
		{Name: "mysekai_res_list", Payload: merge(base, map[string]any{
			"show_harvested": false,
			"data_msg":       infoMsg,
		})},
		{Name: "mysekai_map", Payload: merge(base, map[string]any{
			"show_harvested": false,
		})},
	}
}

func msrFeedItem(server, uid string, uploadTime int64, images [][]byte) subscription.FeedItem {
	message := onebot.Message{onebot.Text(fmt.Sprintf(" 的 %s MSR 数据已更新", strings.ToUpper(server)))}
	for _, image := range images {
		message = append(message, onebot.ImageBytes(base64.StdEncoding.EncodeToString(image)))
	}
	return subscription.FeedItem{
		ID:      fmt.Sprintf("msr/%s/%s/%d", server, uid, uploadTime),
		Message: message,
	}
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}

func ParseMSRUploadTimes(raw []byte) ([]int64, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var value any
	if err := dec.Decode(&value); err != nil {
		return nil, fmt.Errorf("解析 upload_time 响应: %w", err)
	}
	var extra any
	if err := dec.Decode(&extra); err != io.EOF {
		if err == nil {
			return nil, errors.New("解析 upload_time 响应：存在多余数据")
		}
		return nil, fmt.Errorf("解析 upload_time 响应：%w", err)
	}
	return parseMSRUploadValue(value)
}

func ParseUploadTimes(raw []byte) ([]int64, error) {
	return ParseMSRUploadTimes(raw)
}

func parseMSRUploadValue(value any) ([]int64, error) {
	switch v := value.(type) {
	case json.Number, string, float64, int64:
		n, ok := int64Value(v)
		if !ok {
			return nil, errors.New("upload_time 响应包含无效时间")
		}
		return []int64{n}, nil
	case []any:
		out := make([]int64, len(v))
		for i, item := range v {
			n, ok := int64Value(item)
			if !ok {
				return nil, fmt.Errorf("upload_time 响应第 %d 项无效", i)
			}
			out[i] = n
		}
		return out, nil
	case map[string]any:
		for _, key := range []string{"upload_times", "timestamps", "data"} {
			if nested, ok := v[key]; ok {
				return parseMSRUploadValue(nested)
			}
		}
	}
	return nil, errors.New("upload_time 响应格式不支持")
}

func int64Value(value any) (int64, bool) {
	var text string
	switch v := value.(type) {
	case json.Number:
		text = v.String()
	case string:
		text = strings.TrimSpace(v)
	case float64:
		if v != float64(int64(v)) {
			return 0, false
		}
		return int64(v), true
	case int:
		return int64(v), true
	case int64:
		return v, true
	default:
		return 0, false
	}
	if text == "" {
		return 0, false
	}
	n, err := strconv.ParseInt(text, 10, 64)
	return n, err == nil
}

var msrCSTZone = time.FixedZone("CST", 8*3600)

func LastMSRRefreshTime(serverType int, now time.Time) time.Time {
	now = now.In(msrCSTZone)
	first, second := 4, 16
	if serverType == 2 {
		first, second = 5, 17
	}
	candidate := time.Date(now.Year(), now.Month(), now.Day(), second, 0, 0, 0, msrCSTZone)
	if now.Hour() < first {
		return candidate.Add(-24 * time.Hour)
	}
	if now.Hour() < second {
		return candidate.Add(-12 * time.Hour)
	}
	return candidate
}

func LastRefreshTime(serverType int, now time.Time) time.Time {
	return LastMSRRefreshTime(serverType, now)
}

func ShouldPushMSR(uploadTime int64, lastPush time.Time, serverType int, now time.Time) bool {
	if uploadTime <= 0 {
		return false
	}
	now = now.In(msrCSTZone)
	refresh := LastMSRRefreshTime(serverType, now)
	updated := time.Unix(uploadTime, 0).In(msrCSTZone)
	if !updated.After(refresh) || now.Sub(updated) >= msrRecentWindow {
		return false
	}
	return lastPush.IsZero() || lastPush.Before(refresh)
}

var _ subscription.MsrSource = (*MSRSubscriptionSource)(nil)
