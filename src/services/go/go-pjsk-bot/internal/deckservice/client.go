// Package deckservice 是 Rust deck-service 组卡引擎的 HTTP 客户端。
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

type Client struct {
	urls []string
	http *http.Client
}

func New(urls []string, timeout time.Duration) *Client {
	if timeout <= 0 {
		timeout = 120 * time.Second
	}
	clean := make([]string, 0, len(urls))
	for _, u := range urls {
		if u = strings.TrimSpace(u); u != "" {
			clean = append(clean, strings.TrimRight(u, "/"))
		}
	}
	return &Client{urls: clean, http: &http.Client{Timeout: timeout}}
}

type RecommendParams struct {
	Region      string
	UserDataStr string
	Options     map[string]any
	Algorithm   string
	TimeoutMS   int
}

func (c *Client) Recommend(ctx context.Context, p RecommendParams) ([]map[string]any, error) {
	if c == nil || len(c.urls) == 0 {
		return nil, fmt.Errorf("未配置可用的组卡服务")
	}
	payload := make(map[string]any, len(p.Options)+4)
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
		decks, err := c.post(ctx, base+"/recommend", body)
		if err == nil {
			return decks, nil
		}
		errs = append(errs, fmt.Sprintf("%s: %v", base, err))
	}
	return nil, fmt.Errorf("请求所有可用的组卡服务失败：\n%s", strings.Join(errs, "\n"))
}

func (c *Client) post(ctx context.Context, url string, body []byte) ([]map[string]any, error) {
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
		var detail struct{ Error, Detail string }
		_ = json.NewDecoder(resp.Body).Decode(&detail)
		msg := detail.Error
		if msg == "" {
			msg = detail.Detail
		}
		return nil, fmt.Errorf("%d: %s", resp.StatusCode, msg)
	}
	var out struct {
		Decks []map[string]any `json:"decks"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, fmt.Errorf("decode deck response: %w", err)
	}
	return out.Decks, nil
}
