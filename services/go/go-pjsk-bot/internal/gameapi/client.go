package gameapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

type APIError struct {
	Message string
}

func (e *APIError) Error() string { return e.Message }

type Client struct {
	token string
	http  *http.Client
}

func New(token string) *Client {
	return &Client{
		token: token,
		http:  &http.Client{Timeout: 30 * time.Second},
	}
}

// public 和 mysekai 接口不需要 token
func requiresAuth(url string) bool {
	if strings.Contains(url, "/api/public/") {
		return false
	}
	if strings.Contains(url, "/mysekai/") || strings.Contains(url, "get_upload_time") {
		return false
	}
	return true
}

func (c *Client) Get(ctx context.Context, url string) ([]byte, error) {
	return c.Request(ctx, http.MethodGet, url, nil)
}

func (c *Client) Request(ctx context.Context, method, url string, body io.Reader) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, method, url, body)
	if err != nil {
		return nil, err
	}
	if requiresAuth(url) {
		if c.token == "" {
			return nil, &APIError{Message: "游戏 API Token 未配置，请设置 GAMEAPI_TOKEN 环境变量"}
		}
		req.Header.Set("X-Haruki-Sekai-Token", c.token)
		req.Header.Set("Authorization", "Bearer "+c.token)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, &APIError{Message: "请求游戏API失败，请稍后再试"}
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, &APIError{Message: "读取游戏API响应失败，请稍后再试"}
	}
	if resp.StatusCode != http.StatusOK {
		return nil, mapBusinessError(resp.StatusCode, data)
	}
	return data, nil
}

func (c *Client) GetJSON(ctx context.Context, url string, out any) error {
	data, err := c.Get(ctx, url)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, out)
}

func (c *Client) PostJSON(ctx context.Context, url string, payload any) ([]byte, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	if requiresAuth(url) {
		if c.token == "" {
			return nil, &APIError{Message: "游戏 API Token 未配置，请设置 GAMEAPI_TOKEN 环境变量"}
		}
		req.Header.Set("X-Haruki-Sekai-Token", c.token)
		req.Header.Set("Authorization", "Bearer "+c.token)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, &APIError{Message: "请求游戏API失败，请稍后再试"}
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, &APIError{Message: "读取游戏API响应失败，请稍后再试"}
	}
	if resp.StatusCode != http.StatusOK {
		return nil, mapBusinessError(resp.StatusCode, data)
	}
	return data, nil
}

func mapBusinessError(status int, body []byte) error {
	var detail struct {
		Message string `json:"message"`
		Status  string `json:"status"`
	}
	_ = json.Unmarshal(body, &detail)
	switch {
	case status == 404 && detail.Message == "account binding not found":
		return &APIError{Message: "未在工具箱绑定QQ和游戏账号"}
	case status == 403 && detail.Message == "you are not allowed to access this player data.":
		return &APIError{Message: "未在 Haruki 工具箱中游戏账号设置里勾选\"允许公开 API 访问\""}
	case detail.Status == "maintenance_in":
		return &APIError{Message: "服务器正在维护中"}
	case detail.Status == "user_id_ban":
		return &APIError{Message: "账号已被封禁"}
	}
	return &APIError{Message: fmt.Sprintf("接口请求失败: HTTP %d", status)}
}
