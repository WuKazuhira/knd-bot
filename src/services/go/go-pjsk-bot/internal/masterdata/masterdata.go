// Package masterdata 读取 PJSK 游戏主数据（本地 JSON），对齐 old-python
// _utils.load_master_data 的解包与缓存语义。
//
// 只读本地文件（与主进程/其它 sidecar 共享 data/pjsk/ondemand 目录），
// 缺失不自动下载——下载由 go-pjsk-helper / Python 负责。
package masterdata

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
)

// serverName 把 pjsk_type 映射到服务器目录名（0=jp 1=tw 2=cn）。
func serverName(serverType int) string {
	switch serverType {
	case 1:
		return "tw"
	case 2:
		return "cn"
	default:
		return "jp"
	}
}

// Loader 从 ondemand/{server}/ 下读取主数据 JSON，带 mtime 缓存。
type Loader struct {
	root string // data/pjsk/ondemand

	mu    sync.RWMutex
	cache map[string]cacheEntry // key: server/filename
}

type cacheEntry struct {
	mtime int64
	size  int64
	data  []map[string]any
}

// New 创建 Loader。dataDir 为 data/pjsk（内部拼接 ondemand）。
func New(dataDir string) *Loader {
	return &Loader{
		root:  filepath.Join(dataDir, "ondemand"),
		cache: make(map[string]cacheEntry),
	}
}

// Load 读取指定主数据文件并解包成对象列表。文件不存在或损坏返回 error。
func (l *Loader) Load(filename string, serverType int) ([]map[string]any, error) {
	server := serverName(serverType)
	path := filepath.Join(l.root, server, filename)
	key := server + "/" + filename

	info, err := os.Stat(path)
	if err != nil {
		return nil, fmt.Errorf("masterdata %s (%s) 不存在: %w", filename, server, err)
	}
	mtime, size := info.ModTime().UnixNano(), info.Size()

	l.mu.RLock()
	if ent, ok := l.cache[key]; ok && ent.mtime == mtime && ent.size == size {
		l.mu.RUnlock()
		return ent.data, nil
	}
	l.mu.RUnlock()

	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("读取 masterdata %s: %w", filename, err)
	}
	var parsed any
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return nil, fmt.Errorf("解析 masterdata %s: %w", filename, err)
	}
	list := unwrap(parsed)

	l.mu.Lock()
	l.cache[key] = cacheEntry{mtime: mtime, size: size, data: list}
	l.mu.Unlock()
	return list, nil
}

// Invalidate 使指定服务器的主数据缓存失效。
// filenames 为空时清空该服务器的全部缓存；传入文件名时只清理对应条目。
func (l *Loader) Invalidate(serverType int, filenames ...string) {
	if l == nil {
		return
	}
	server := serverName(serverType)
	l.mu.Lock()
	defer l.mu.Unlock()
	if len(filenames) == 0 {
		prefix := server + "/"
		for key := range l.cache {
			if strings.HasPrefix(key, prefix) {
				delete(l.cache, key)
			}
		}
		return
	}
	for _, filename := range filenames {
		delete(l.cache, server+"/"+filename)
	}
}

// unwrap 把主数据还原成对象列表，对齐 Python _unwrap：
// 优先取常见包裹键；数字键字典取 values；否则过滤出容器型字段。
func unwrap(data any) []map[string]any {
	switch v := data.(type) {
	case []any:
		return toMapList(v)
	case map[string]any:
		wrapperKeys := []string{"data", "items", "list", "entries", "rankings", "masterData", "cards", "musics", "events", "skills"}
		for _, k := range wrapperKeys {
			if inner, ok := v[k]; ok {
				switch inner.(type) {
				case []any, map[string]any:
					return unwrap(inner)
				}
			}
		}
		// 数字键字典：按键排序取 values
		if isNumericMap(v) {
			keys := make([]string, 0, len(v))
			for k := range v {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			out := make([]any, 0, len(keys))
			for _, k := range keys {
				out = append(out, v[k])
			}
			return toMapList(out)
		}
		// 否则过滤出容器型字段的值
		var filtered []any
		for _, val := range v {
			switch val.(type) {
			case []any, map[string]any:
				filtered = append(filtered, val)
			}
		}
		return toMapList(filtered)
	}
	return nil
}

func isNumericMap(m map[string]any) bool {
	if len(m) == 0 {
		return false
	}
	for k := range m {
		for _, c := range k {
			if c < '0' || c > '9' {
				return false
			}
		}
	}
	return true
}

func toMapList(items []any) []map[string]any {
	out := make([]map[string]any, 0, len(items))
	for _, it := range items {
		if m, ok := it.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

// ByID 按 "id" 字段建索引，便于按 id 查主数据对象。
func ByID(list []map[string]any) map[int64]map[string]any {
	idx := make(map[int64]map[string]any, len(list))
	for _, item := range list {
		if id, ok := IntField(item, "id"); ok {
			idx[id] = item
		}
	}
	return idx
}

// IntField 从对象取整型字段（兼容 JSON number 为 float64）。
func IntField(m map[string]any, key string) (int64, bool) {
	v, ok := m[key]
	if !ok {
		return 0, false
	}
	switch n := v.(type) {
	case float64:
		return int64(n), true
	case json.Number:
		i, err := n.Int64()
		return i, err == nil
	case int64:
		return n, true
	case int:
		return int64(n), true
	}
	return 0, false
}
