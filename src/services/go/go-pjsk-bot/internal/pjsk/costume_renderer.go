package pjsk

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"image/png"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/andybalholm/brotli"
	"github.com/vmihailenco/msgpack/v5"
)

const runtimeMetadataLimit = 64 << 20

type CostumeCapture struct {
	ImageID  string           `json:"imageId"`
	RoleID   string           `json:"roleId"`
	Body     int              `json:"bodyCostume3dId"`
	Head     int              `json:"headCostume3dId"`
	Hair     int              `json:"hairCostume3dId"`
	Optional *int             `json:"headOptionalCostume3dId"`
	Version  string           `json:"runtimeVersion,omitempty"`
	Parts    []map[string]any `json:"parts,omitempty"`
}
type CostumeRenderer struct {
	Root, URL   string
	CapturePath string // 默认为多区域 /regions/jp/capture；仅官方单区域 smoke 使用 /capture。
	HTTP        *http.Client
}

func NewCostumeRenderer(root, endpoint string) *CostumeRenderer {
	return &CostumeRenderer{Root: root, URL: strings.TrimRight(endpoint, "/"), HTTP: &http.Client{Timeout: 90 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
}

// runtimeFile rejects traversal and symlinks escaping the read-only runtime root.
func (r *CostumeRenderer) runtimeFile(path string) (string, error) {
	if path == "" || filepath.IsAbs(path) || strings.Contains(path, "\\") {
		return "", fmt.Errorf("unsafe runtime path %q", path)
	}
	for _, s := range strings.Split(path, "/") {
		if s == ".." || s == "." {
			return "", fmt.Errorf("unsafe runtime path %q", path)
		}
	}
	root, err := filepath.EvalSymlinks(r.Root)
	if err != nil {
		return "", err
	}
	full, err := filepath.EvalSymlinks(filepath.Join(root, path))
	if err != nil {
		return "", err
	}
	rel, err := filepath.Rel(root, full)
	if err != nil || strings.HasPrefix(rel, "..") {
		return "", fmt.Errorf("runtime path escapes root")
	}
	return full, nil
}
func (r *CostumeRenderer) metadata(path string) (map[string]any, error) {
	if !strings.HasSuffix(path, ".msgpack.br") {
		return nil, fmt.Errorf("unsupported runtime metadata %q", path)
	}
	full, err := r.runtimeFile(path)
	if err != nil {
		return nil, err
	}
	f, err := os.Open(full)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(brotli.NewReader(io.LimitReader(f, runtimeMetadataLimit+1)), runtimeMetadataLimit+1))
	if err != nil {
		return nil, err
	}
	if len(data) > runtimeMetadataLimit {
		return nil, fmt.Errorf("runtime metadata too large")
	}
	registerOfficialBinaryArrayExtension()
	var result map[string]any
	if err = msgpack.Unmarshal(data, &result); err != nil {
		return nil, fmt.Errorf("decode %s: %w", path, err)
	}
	return result, nil
}
func runtimeInt(m map[string]any, k string) int {
	switch v := m[k].(type) {
	case int:
		return v
	case int8:
		return int(v)
	case int16:
		return int(v)
	case int32:
		return int(v)
	case int64:
		return int(v)
	case uint8:
		return int(v)
	case uint16:
		return int(v)
	case uint32:
		return int(v)
	case uint64:
		return int(v)
	case float64:
		return int(v)
	}
	return 0
}
func runtimeRows(m map[string]any, k string) []map[string]any {
	out := []map[string]any{}
	switch values := m[k].(type) {
	case []any:
		for _, v := range values {
			if row, ok := v.(map[string]any); ok {
				out = append(out, row)
			}
		}
	}
	return out
}

// Selection validates actual core+delta packages and recursively checks referenced runtime assets.
func (r *CostumeRenderer) Selection(characterID, bodyID int, parts []map[string]any) (CostumeCapture, error) {
	paths, err := filepath.Glob(filepath.Join(r.Root, "parts", "by-role", fmt.Sprint(characterID), "*", "runtime-role-catalog.msgpack.br"))
	if err != nil {
		return CostumeCapture{}, err
	}
	sort.Strings(paths)
	var last error = fmt.Errorf("character %d role runtime unavailable", characterID)
	for _, path := range paths {
		rel, err := filepath.Rel(r.Root, path)
		if err != nil {
			last = err
			continue
		}
		capture, err := r.selectionAt(filepath.ToSlash(rel), characterID, bodyID, parts)
		if err == nil {
			return capture, nil
		}
		last = err
	}
	return CostumeCapture{}, last
}
func (r *CostumeRenderer) selectionAt(path string, characterID, bodyID int, parts []map[string]any) (CostumeCapture, error) {
	catalog, err := r.metadata(path)
	if err != nil {
		return CostumeCapture{}, err
	}
	version := runtimeInt(catalog, "version")
	roles := runtimeRows(catalog, "roles")
	master := strField(catalog, "masterVersion")
	if version < 2 || version > 4 || master == "" || len(roles) != 1 {
		return CostumeCapture{}, fmt.Errorf("invalid scoped role catalog %s", path)
	}
	role := roles[0]
	unit := strField(role, "unit")
	if unit == "" {
		unit = "default"
	}
	if runtimeInt(role, "characterId") != characterID {
		return CostumeCapture{}, fmt.Errorf("role character mismatch")
	}
	expected := fmt.Sprintf("roles/%d/%s/role-runtime.msgpack.br", characterID, unit)
	if strField(role, "roleRuntimePath") != expected {
		return CostumeCapture{}, fmt.Errorf("role runtime path mismatch")
	}
	roleRuntime, err := r.metadata(expected)
	if err != nil {
		return CostumeCapture{}, err
	}
	if err = r.validateRuntimeDependencies(expected, "", roleRuntime, map[string]bool{}, 0); err != nil {
		return CostumeCapture{}, err
	}
	selection := CostumeCapture{RoleID: fmt.Sprintf("%d:%s", characterID, unit), Body: bodyID, Head: runtimeInt(role, "headCostume3dId"), Hair: runtimeInt(role, "hairCostume3dId"), Version: master}
	for _, p := range parts {
		switch strField(p, "partType") {
		case "head":
			selection.Head = intField(p, "id")
		case "hair":
			selection.Hair = intField(p, "id")
		case "head_optional":
			id := intField(p, "id")
			selection.Optional = &id
		}
	}
	if selection.Body <= 0 || selection.Head <= 0 || selection.Hair <= 0 {
		return CostumeCapture{}, fmt.Errorf("missing required costume parts")
	}
	registry, err := r.metadata(strings.TrimSuffix(path, "runtime-role-catalog.msgpack.br") + "part-registry.msgpack.br")
	if err != nil {
		return CostumeCapture{}, err
	}
	entries := runtimeRows(registry, "entries")
	required := map[string]int{"body": selection.Body, "head": selection.Head, "hair": selection.Hair}
	if selection.Optional != nil {
		required["head_optional"] = *selection.Optional
	}
	for typ, id := range required {
		found := false
		for _, entry := range entries {
			entrySlot := strField(entry, "partType")
			if runtimeInt(entry, "costume3dId") != id || runtimeInt(entry, "characterId") != characterID || (entrySlot != typ && !(typ == "head" && entrySlot == "head_optional")) {
				continue
			}
			status := strField(entry, "status")
			if typ == "head" && entrySlot == "head_optional" && status == "empty" {
				found = true
				break
			}
			if status == "missing" || status == "empty" || status == "" {
				continue
			}
			packagePath := strField(entry, "packagePath")
			delta, err := r.metadata(packagePath + "/part-runtime.msgpack.br")
			if err != nil {
				return CostumeCapture{}, fmt.Errorf("%s %d %s: %w", typ, id, status, err)
			}
			corePath := strField(delta, "corePath")
			if _, err = r.metadata(corePath); err != nil {
				return CostumeCapture{}, fmt.Errorf("%s %d core unavailable: %w", typ, id, err)
			}
			if err = r.validateRuntimeDependencies(packagePath+"/part-runtime.msgpack.br", packagePath, delta, map[string]bool{}, 0); err != nil {
				return CostumeCapture{}, fmt.Errorf("%s %d dependency unavailable: %w", typ, id, err)
			}
			found = true
			break
		}
		if !found {
			return CostumeCapture{}, fmt.Errorf("registry %s %d unavailable", typ, id)
		}
	}
	// Compatibility is consumed by the official engine; require its real binary registry.
	if _, err = r.metadata("parts/compat/by-unit/" + unit + "/head-hair-compatibility.msgpack.br"); err != nil {
		return CostumeCapture{}, err
	}
	selection.Parts = parts
	return selection, nil
}
func (r *CostumeRenderer) request(ctx context.Context, method, path string, body []byte, limit int64) ([]byte, error) {
	base, err := url.Parse(r.URL)
	if err != nil || base.Host == "" || (base.Scheme != "http" && base.Scheme != "https") {
		return nil, fmt.Errorf("invalid 3D engine URL")
	}
	req, err := http.NewRequestWithContext(ctx, method, r.URL+path, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	client := *r.HTTP
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("engine %s HTTP %d", path, resp.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(data)) > limit {
		return nil, fmt.Errorf("engine response too large")
	}
	return data, nil
}
func (r *CostumeRenderer) Capture(ctx context.Context, c CostumeCapture) ([]byte, error) {
	if _, err := r.request(ctx, "GET", "/healthz", nil, 64<<10); err != nil {
		return nil, err
	}
	if c.ImageID == "" {
		encoded, _ := json.Marshal(c)
		sum := sha256.Sum256(append(encoded, []byte(c.Version)...))
		c.ImageID = "jp-costume-" + hex.EncodeToString(sum[:16])
	}
	for _, ch := range c.ImageID {
		if !(ch >= 'a' && ch <= 'z' || ch >= '0' && ch <= '9' || ch == '-' || ch == '_') {
			return nil, fmt.Errorf("unsafe capture image ID")
		}
	}
	wire := struct {
		ImageID  string `json:"imageId"`
		RoleID   string `json:"roleId"`
		Body     int    `json:"bodyCostume3dId"`
		Head     int    `json:"headCostume3dId"`
		Hair     int    `json:"hairCostume3dId"`
		Optional *int   `json:"headOptionalCostume3dId"`
	}{c.ImageID, c.RoleID, c.Body, c.Head, c.Hair, c.Optional}
	encoded, err := json.Marshal(wire)
	if err != nil {
		return nil, err
	}
	capturePath := r.CapturePath
	if capturePath == "" {
		capturePath = "/regions/jp/capture"
	}
	if capturePath != "/capture" && capturePath != "/regions/jp/capture" {
		return nil, fmt.Errorf("unsupported capture route")
	}
	if _, err = r.request(ctx, "POST", capturePath, encoded, 1<<20); err != nil {
		return nil, err
	}
	data, err := r.request(ctx, "GET", "/captures/"+c.ImageID+".png", nil, 16<<20)
	if err != nil {
		return nil, err
	}
	cfg, err := png.DecodeConfig(bytes.NewReader(data))
	if err != nil || cfg.Width <= 0 || cfg.Height <= 0 || cfg.Width > 8192 || cfg.Height > 8192 {
		return nil, fmt.Errorf("invalid capture PNG dimensions")
	}
	if _, err = png.Decode(bytes.NewReader(data)); err != nil {
		return nil, fmt.Errorf("invalid capture PNG: %w", err)
	}
	return data, nil
}
