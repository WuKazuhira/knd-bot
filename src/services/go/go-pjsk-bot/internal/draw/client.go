// Package draw 是 pjsk-draw 出图微服务的 HTTP 客户端。
//
// Go 业务侧只负责取数并组织 payload，出图统一委托 Python 的 pjsk-draw 服务
// （POST /render/{name}，JSON 载荷进、图片字节出）。绝不在 Go 侧自建绘图。
package draw

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// Client 调用 pjsk-draw 服务。
type Client struct {
	baseURL string
	http    *http.Client
}

// New 创建客户端。baseURL 形如 http://pjsk-draw:45560。
func New(baseURL string) *Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.MaxIdleConns = 32
	transport.MaxIdleConnsPerHost = 16
	transport.IdleConnTimeout = 90 * time.Second
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		http: &http.Client{
			Timeout:   60 * time.Second,
			Transport: transport,
		},
	}
}

// jsonEnvelope 是多图/带 meta 任务的返回信封。
type jsonEnvelope struct {
	Images []string       `json:"images"`
	Meta   map[string]any `json:"meta"`
}

// Render 渲染单图任务，返回图片字节。
func (c *Client) Render(ctx context.Context, name string, payload map[string]any) ([]byte, error) {
	images, _, err := c.RenderWithMeta(ctx, name, payload)
	if err != nil {
		return nil, err
	}
	if len(images) == 0 {
		return nil, fmt.Errorf("draw 任务 %s 未返回图片", name)
	}
	return images[0], nil
}

// RenderMulti 渲染多图任务，返回图片字节列表。
func (c *Client) RenderMulti(ctx context.Context, name string, payload map[string]any) ([][]byte, error) {
	images, _, err := c.RenderWithMeta(ctx, name, payload)
	return images, err
}

// RenderWithMeta 渲染任务，返回图片字节列表与元信息。
func (c *Client) RenderWithMeta(ctx context.Context, name string, payload map[string]any) ([][]byte, map[string]any, error) {
	if payload == nil {
		payload = map[string]any{}
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, nil, fmt.Errorf("marshal payload: %w", err)
	}
	url := fmt.Sprintf("%s/render/%s", c.baseURL, name)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, nil, fmt.Errorf("call draw %s: %w", name, err)
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, nil, fmt.Errorf("draw %s 返回 %d: %s", name, resp.StatusCode, truncate(data, 300))
	}
	contentType := strings.ToLower(strings.SplitN(resp.Header.Get("Content-Type"), ";", 2)[0])
	if strings.TrimSpace(contentType) == "application/json" {
		var env jsonEnvelope
		if err := json.Unmarshal(data, &env); err != nil {
			return nil, nil, fmt.Errorf("decode draw envelope: %w", err)
		}
		images := make([][]byte, 0, len(env.Images))
		for _, b64 := range env.Images {
			raw, err := base64.StdEncoding.DecodeString(b64)
			if err != nil {
				return nil, nil, fmt.Errorf("decode image b64: %w", err)
			}
			images = append(images, raw)
		}
		return images, env.Meta, nil
	}
	if len(data) == 0 {
		return nil, nil, fmt.Errorf("draw %s 返回空响应", name)
	}
	return [][]byte{data}, nil, nil
}

func truncate(b []byte, n int) string {
	if len(b) <= n {
		return string(b)
	}
	return string(b[:n])
}
