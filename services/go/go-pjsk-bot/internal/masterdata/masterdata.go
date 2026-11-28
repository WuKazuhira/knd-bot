package masterdata

import (
	"container/list"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
)

const defaultMaxCacheBytes int64 = 40 << 20

// 超大表（50MB 级 JSON）解析成 []map[string]any 后堆放大明显，且多用于构建轻量 Derived 索引，不常驻原始 map
var uncachedFiles = map[string]bool{
	"costume3ds.json":         true,
	"gachas.json":             true,
	"virtualLives.json":       true,
	"resourceBoxes.json":      true,
	"costume3dModels.json":    true,
	"costume3dShopItems.json": true,
}

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
	cache          map[string]*list.Element
	lru            *list.List
	cacheBytes     int64
	maxCacheBytes  int64
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
	key   string
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
		root:          filepath.Join(dataDir, "ondemand"),
		cache:         make(map[string]*list.Element),
		lru:           list.New(),
		maxCacheBytes: defaultMaxCacheBytes,
		derived:       make(map[string]derivedEntry),
	}
}

func (l *Loader) Load(filename string, serverType int) ([]map[string]any, error) {
	return l.load(filename, serverType, !uncachedFiles[filename])
}

func (l *Loader) LoadUncached(filename string, serverType int) ([]map[string]any, error) {
	return l.load(filename, serverType, false)
}

func (l *Loader) load(filename string, serverType int, cacheable bool) ([]map[string]any, error) {
	server := serverName(serverType)
	path := filepath.Join(l.root, server, filename)
	key := server + "/" + filename

	info, err := os.Stat(path)
	if err != nil {
		return nil, fmt.Errorf("masterdata %s (%s) 不存在: %w", filename, server, err)
	}
	mtime, size := info.ModTime().UnixNano(), info.Size()

	if cacheable {
		l.mu.Lock()
		if el, ok := l.cache[key]; ok {
			ent := el.Value.(*cacheEntry)
			if ent.mtime == mtime && ent.size == size {
				l.lru.MoveToFront(el)
				data := ent.data
				l.mu.Unlock()
				return data, nil
			}
			l.removeElementLocked(el)
		}
		l.mu.Unlock()
	}

	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("读取 masterdata %s: %w", filename, err)
	}
	var parsed any
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return nil, fmt.Errorf("解析 masterdata %s: %w", filename, err)
	}
	raw = nil
	list := unwrap(parsed)
	parsed = nil

	if cacheable && (l.maxCacheBytes <= 0 || size <= l.maxCacheBytes) {
		l.mu.Lock()
		l.putLocked(key, mtime, size, list)
		l.mu.Unlock()
	}
	return list, nil
}

func (l *Loader) putLocked(key string, mtime, size int64, data []map[string]any) {
	if l.lru == nil {
		l.lru = list.New()
	}
	if l.cache == nil {
		l.cache = make(map[string]*list.Element)
	}
	if old, ok := l.cache[key]; ok {
		l.removeElementLocked(old)
	}
	el := l.lru.PushFront(&cacheEntry{key: key, mtime: mtime, size: size, data: data})
	l.cache[key] = el
	l.cacheBytes += size
	for l.maxCacheBytes > 0 && l.cacheBytes > l.maxCacheBytes && l.lru.Len() > 1 {
		tail := l.lru.Back()
		if tail == nil {
			break
		}
		l.removeElementLocked(tail)
	}
}

func (l *Loader) removeElementLocked(el *list.Element) {
	ent := l.lru.Remove(el).(*cacheEntry)
	delete(l.cache, ent.key)
	l.cacheBytes -= ent.size
	if l.cacheBytes < 0 {
		l.cacheBytes = 0
	}
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
		for key, el := range l.cache {
			if strings.HasPrefix(key, prefix) {
				l.removeElementLocked(el)
			}
		}
		return
	}
	for _, filename := range filenames {
		if el, ok := l.cache[prefix+filename]; ok {
			l.removeElementLocked(el)
		}
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
