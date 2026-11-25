package shared

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// HTTPStatusError 表示上游返回了非 200 的 HTTP 状态码。
type HTTPStatusError struct {
	StatusCode int
	URL        string
}

func (e *HTTPStatusError) Error() string {
	return fmt.Sprintf("HTTP %d for %s", e.StatusCode, e.URL)
}

// WriteAtomic 以临时文件 + Chmod(0o644) + Rename 原子落盘，避免跨容器权限不一致。
func WriteAtomic(target string, data []byte) error {
	dir := filepath.Dir(target)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, "."+filepath.Base(target)+".tmp*")
	if err != nil {
		return err
	}
	name := tmp.Name()
	cleanup := true
	defer func() {
		if cleanup {
			_ = os.Remove(name)
		}
	}()
	if err := tmp.Chmod(0o644); err != nil {
		_ = tmp.Close()
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Rename(name, target); err != nil {
		return err
	}
	cleanup = false
	return nil
}

// CandidateURLs 按 GH_MIRROR_PREFIX 为 raw.githubusercontent.com 拼装镜像及直连候选地址。
func CandidateURLs(rawURL string) []string {
	mirror := strings.TrimSpace(os.Getenv("GH_MIRROR_PREFIX"))
	if mirror == "" {
		mirror = "https://ghfast.top"
	}
	if strings.HasPrefix(rawURL, "https://raw.githubusercontent.com/") && !strings.EqualFold(mirror, "off") {
		mirror = strings.TrimRight(mirror, "/")
		return []string{mirror + "/" + rawURL, rawURL}
	}
	return []string{rawURL}
}

// FetchRetry 执行带指数退避的 HTTP GET，遇 404/410 或上下文取消立即中止，不在末次失败后空等。
func FetchRetry(ctx context.Context, client *http.Client, endpoint string, maxBytes int64, headers map[string]string) ([]byte, error) {
	if maxBytes <= 0 {
		maxBytes = 32 << 20
	}
	var lastErr error
	for attempt := 0; attempt < 3; attempt++ {
		data, err := fetchOnce(ctx, client, endpoint, maxBytes, headers)
		if err == nil {
			return data, nil
		}
		lastErr = err
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		var statusErr *HTTPStatusError
		if errors.As(err, &statusErr) && (statusErr.StatusCode == http.StatusNotFound || statusErr.StatusCode == http.StatusGone) {
			return nil, err
		}
		if attempt < 2 {
			timer := time.NewTimer(time.Duration(1<<attempt) * time.Second)
			select {
			case <-timer.C:
			case <-ctx.Done():
				timer.Stop()
				return nil, ctx.Err()
			}
		}
	}
	return nil, lastErr
}

func fetchOnce(ctx context.Context, client *http.Client, endpoint string, maxBytes int64, headers map[string]string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, err
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, &HTTPStatusError{StatusCode: resp.StatusCode, URL: endpoint}
	}
	return io.ReadAll(io.LimitReader(resp.Body, maxBytes))
}

// NumberValue 宽松解析 JSON 数字或数值字符串为 float64。
func NumberValue(value any) (float64, bool) {
	switch n := value.(type) {
	case float64:
		return n, true
	case float32:
		return float64(n), true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	case json.Number:
		f, err := n.Float64()
		return f, err == nil
	case string:
		f, err := strconv.ParseFloat(strings.TrimSpace(n), 64)
		return f, err == nil
	default:
		return 0, false
	}
}

// SafeFileName 校验单个文件名不含路径分隔符或父目录跳转。
func SafeFileName(name string) bool {
	return name != "" && !strings.ContainsAny(name, "/\\") && filepath.Base(name) == name && !strings.Contains(name, "..")
}
