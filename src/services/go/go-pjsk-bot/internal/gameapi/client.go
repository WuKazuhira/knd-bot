// Package gameapi 是游戏数据 API（Haruki 工具箱后端）的 HTTP 客户端，
// 对齐 old-python _gameapi.request_gameapi 的 token 注入与业务错误映射。
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

// APIError 是面向用户的业务错误（对齐 apiCallError/maintenanceIn/userIdBan）。
type APIError struct {
	Message string
}

func (e *APIError) Error() string { return e.Message }

// Client 调用游戏 API。
type Client struct {
	token string
	http  *http.Client
}

// New 创建客户端。token 对应 GAMEAPI_TOKEN，可空（仅无需鉴权的接口可用）。
func New(token string) *Client {
	return &Client{
		token: token,
		http:  &http.Client{Timeout: 30 * time.Second},
	}
}

// requiresAuth 对齐 Python _requires_auth：public / mysekai 接口无需 token。
func requiresAuth(url string) bool {
	if strings.Contains(url, "/api/public/") {
		return false
	}
	if strings.Contains(url, "/mysekai/") || strings.Contains(url, "get_upload_time") {
		return false
	}
	return true
}

// Get 请求游戏 API 并返回原始响应字节。非 200 映射为业务错误。
func (c *Client) Get(ctx context.Context, url string) ([]byte, error) {
	return c.Request(ctx, http.MethodGet, url, nil)
}

// Request 通用请求。body 可为 nil。
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

// GetJSON 请求并解码 JSON 到 out。
func (c *Client) GetJSON(ctx context.Context, url string, out any) error {
	data, err := c.Get(ctx, url)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, out)
}

// PostJSON 以 application/json 提交 payload（POST），返回原始响应字节。
// 用于 MySekai 照片下载等需要提交 JSON 并取回二进制的接口。
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

// mapBusinessError 对齐 old-python _raise_business_error。
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
