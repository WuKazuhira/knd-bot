// Package helper 是 go-pjsk-helper sidecar 的 HTTP 客户端，
// 复用其已提供的 suite / b30 / ranking 能力（不在本服务重复实现）。
package helper

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// Client 调用 go-pjsk-helper。
type Client struct {
	baseURL string
	http    *http.Client
}

// New 创建客户端。baseURL 形如 http://pjsk-helper:8000。
func New(baseURL string) *Client {
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		http:    &http.Client{Timeout: 30 * time.Second},
	}
}

func region(serverType int) string {
	switch serverType {
	case 1:
		return "tw"
	case 2:
		return "cn"
	default:
		return "jp"
	}
}

// Suite 拉取用户 suite 数据（原始 JSON 字节）。
func (c *Client) Suite(ctx context.Context, serverType int, uid string) ([]byte, error) {
	return c.get(ctx, fmt.Sprintf("/suite/%s/%s", region(serverType), uid))
}

// B30 拉取 b30 数据（原始 JSON 字节）。
func (c *Client) B30(ctx context.Context, serverType int, uid string) ([]byte, error) {
	return c.get(ctx, fmt.Sprintf("/b30/%s/%s", region(serverType), uid))
}

// RankingLatest 拉取最新榜线快照（原始 JSON 字节）。
func (c *Client) RankingLatest(ctx context.Context, serverType int) ([]byte, error) {
	return c.get(ctx, fmt.Sprintf("/ranking/%s/latest", region(serverType)))
}

func (c *Client) get(ctx context.Context, path string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+path, nil)
	if err != nil {
		return nil, err
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("call helper %s: %w", path, err)
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("helper %s 返回 %d", path, resp.StatusCode)
	}
	return data, nil
}
