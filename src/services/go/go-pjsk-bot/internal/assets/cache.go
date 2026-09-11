// Package assets 提供可复用的本地资源缓存与受限下载能力。
package assets

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

var ErrTooLarge = errors.New("asset exceeds size limit")

// Client 负责将远程资源安全地缓存到共享 data 目录。
type Client struct {
	Root      string
	HTTP      *http.Client
	MaxBytes  int64
	UserAgent string
}

func New(root string, httpClient *http.Client, maxBytes int64) *Client {
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 90 * time.Second}
	}
	if maxBytes <= 0 {
		maxBytes = 64 << 20
	}
	return &Client{Root: root, HTTP: httpClient, MaxBytes: maxBytes, UserAgent: "go-pjsk-bot/assets"}
}

// Path 返回 rel 在缓存根目录下的安全路径，拒绝绝对路径和目录穿越。
func (c *Client) Path(rel string) (string, error) {
	rel = filepath.Clean(rel)
	if rel == "." || filepath.IsAbs(rel) || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("invalid asset path %q", rel)
	}
	return filepath.Join(c.Root, rel), nil
}

func (c *Client) Read(rel string) ([]byte, error) {
	path, err := c.Path(rel)
	if err != nil {
		return nil, err
	}
	return os.ReadFile(path)
}

// Fetch 返回缓存内容；缓存不存在时下载并原子写入。
func (c *Client) Fetch(ctx context.Context, url, rel string) ([]byte, error) {
	path, err := c.Path(rel)
	if err != nil {
		return nil, err
	}
	if data, readErr := os.ReadFile(path); readErr == nil {
		return data, nil
	}
	if strings.TrimSpace(url) == "" {
		return nil, fmt.Errorf("asset %s not cached and URL is empty", rel)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, fmt.Errorf("build asset request: %w", err)
	}
	if c.UserAgent != "" {
		req.Header.Set("User-Agent", c.UserAgent)
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, fmt.Errorf("download asset: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("download asset: HTTP %d", resp.StatusCode)
	}
	if resp.ContentLength > c.MaxBytes {
		return nil, ErrTooLarge
	}

	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, fmt.Errorf("create asset directory: %w", err)
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".asset-*")
	if err != nil {
		return nil, fmt.Errorf("create asset temp file: %w", err)
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	limited := io.LimitReader(resp.Body, c.MaxBytes+1)
	data, err := io.ReadAll(limited)
	if err != nil {
		_ = tmp.Close()
		return nil, fmt.Errorf("read asset: %w", err)
	}
	if int64(len(data)) > c.MaxBytes {
		_ = tmp.Close()
		return nil, ErrTooLarge
	}
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return nil, fmt.Errorf("write asset cache: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return nil, fmt.Errorf("close asset cache: %w", err)
	}
	if err := os.Rename(tmpName, path); err != nil {
		return nil, fmt.Errorf("commit asset cache: %w", err)
	}
	return data, nil
}

// CacheKey 为外部资源生成稳定的缓存键，供调用方构造相对路径。
func CacheKey(url string) string {
	sum := sha256.Sum256([]byte(url))
	return hex.EncodeToString(sum[:])
}
