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

type Loader struct {
	root string

	mu             sync.RWMutex
	cache          map[string]cacheEntry
	derived        map[string]derivedEntry
	constantsStamp fileStamp
	constants      map[ConstantKey]float64
}

type fileStamp struct {
	exists bool
	mtime  int64
	size   int64
}

type cacheEntry struct {
	mtime int64
	size  int64
	data  []map[string]any
}

type derivedEntry struct {
	stamps []fileStamp
	value  any
}

func New(dataDir string) *Loader {
	return &Loader{
		root:    filepath.Join(dataDir, "ondemand"),
		cache:   make(map[string]cacheEntry),
		derived: make(map[string]derivedEntry),
	}
}

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

func (l *Loader) Invalidate(serverType int, filenames ...string) {
	if l == nil {
		return
	}
	server := serverName(serverType)
	prefix := server + "/"
	l.mu.Lock()
	defer l.mu.Unlock()
	for key := range l.derived {
		if strings.HasPrefix(key, prefix) {
			delete(l.derived, key)
		}
	}
	if len(filenames) == 0 {
		for key := range l.cache {
			if strings.HasPrefix(key, prefix) {
				delete(l.cache, key)
			}
		}
		return
	}
	for _, filename := range filenames {
		delete(l.cache, prefix+filename)
	}
}

func (l *Loader) statStamps(server string, deps []string) []fileStamp {
	stamps := make([]fileStamp, len(deps))
	for i, filename := range deps {
		if info, err := os.Stat(filepath.Join(l.root, server, filename)); err == nil {
			stamps[i] = fileStamp{exists: true, mtime: info.ModTime().UnixNano(), size: info.Size()}
		}
	}
	return stamps
}

func stampsEqual(a, b []fileStamp) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// build 执行时不持锁，内部可直接调 Load
func (l *Loader) Derived(name string, serverType int, deps []string, build func() (any, error)) (any, error) {
	if l == nil {
		return build()
	}
	server := serverName(serverType)
	key := server + "/" + name
	stamps := l.statStamps(server, deps)

	l.mu.RLock()
	if ent, ok := l.derived[key]; ok && stampsEqual(ent.stamps, stamps) {
		l.mu.RUnlock()
		return ent.value, nil
	}
	l.mu.RUnlock()

	val, err := build()
	if err != nil {
		return nil, err
	}
	l.mu.Lock()
	if l.derived == nil {
		l.derived = make(map[string]derivedEntry)
	}
	l.derived[key] = derivedEntry{stamps: stamps, value: val}
	l.mu.Unlock()
	return val, nil
}

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

func ByID(list []map[string]any) map[int64]map[string]any {
	idx := make(map[int64]map[string]any, len(list))
	for _, item := range list {
		if id, ok := IntField(item, "id"); ok {
			idx[id] = item
		}
	}
	return idx
}

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
