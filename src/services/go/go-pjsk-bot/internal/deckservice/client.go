// Package deckservice 是 Rust deck-service 组卡引擎的 HTTP 客户端，
// 对齐 old-python deck._recommender.do_recommend 的 /recommend JSON 契约。
//
// 组卡算法不在 Go 侧实现：本包只负责把组卡 options + 用户数据发给外部
// deck-service，收回卡组结果。多地址故障转移。
package deckservice

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"
)

// Client 调用 deck-service。
type Client struct {
	urls []string
	http *http.Client
}

// New 创建客户端。urls 为 deck-service 地址列表（多地址做故障转移）。
func New(urls []string, timeout time.Duration) *Client {
	if timeout <= 0 {
		timeout = 120 * time.Second
	}
	return &Client{urls: urls, http: &http.Client{Timeout: timeout}}
}

// recommendResponse 是 deck-service /recommend 的响应信封。
type recommendResponse struct {
	Decks []map[string]any `json:"decks"`
}

// RecommendParams 是一次组卡请求的参数。
type RecommendParams struct {
	Region      string         // jp/tw/cn
	UserDataStr string         // 用户 suite 数据（字符串）
	Options     map[string]any // 组卡 options（live_type/limit/固定卡等）
	Algorithm   string         // dfs/ga
	TimeoutMS   int            // 组卡超时毫秒
}

// Recommend 向 deck-service 发送组卡请求，返回原始卡组列表（[]map）。
// 多地址依次尝试，全部失败返回错误。
func (c *Client) Recommend(ctx context.Context, p RecommendParams) ([]map[string]any, error) {
	if len(c.urls) == 0 {
		return nil, fmt.Errorf("未配置可用的组卡服务")
	}
	payload := map[string]any{}
	for k, v := range p.Options {
		payload[k] = v
	}
	payload["region"] = p.Region
	payload["user_data_str"] = p.UserDataStr
	if p.Algorithm != "" {
		payload["algorithm"] = p.Algorithm
	} else if _, ok := payload["algorithm"]; !ok {
		payload["algorithm"] = "dfs"
	}
	if p.TimeoutMS > 0 {
		payload["timeout_ms"] = p.TimeoutMS
	} else if _, ok := payload["timeout_ms"]; !ok {
		payload["timeout_ms"] = 15000
	}

	body, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("marshal deck payload: %w", err)
	}

	var errs []string
	for _, base := range c.urls {
		url := strings.TrimRight(base, "/") + "/recommend"
		decks, err := c.postOne(ctx, url, body)
		if err != nil {
			errs = append(errs, fmt.Sprintf("%s: %v", base, err))
			continue
		}
		return decks, nil
	}
	return nil, fmt.Errorf("请求所有可用的组卡服务失败:\n%s", strings.Join(errs, "\n"))
}

func (c *Client) postOne(ctx context.Context, url string, body []byte) ([]map[string]any, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		var errData struct {
			Error  string `json:"error"`
			Detail string `json:"detail"`
		}
		_ = json.NewDecoder(resp.Body).Decode(&errData)
		detail := errData.Error
		if detail == "" {
			detail = errData.Detail
		}
		return nil, fmt.Errorf("%d: %s", resp.StatusCode, detail)
	}
	var out recommendResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, fmt.Errorf("decode deck response: %w", err)
	}
	return out.Decks, nil
}
