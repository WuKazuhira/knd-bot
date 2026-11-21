package masterdata

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"hash"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const (
	localJPRegion              = "jp"
	LocalNewCardSnapshotFile   = "local_new_card_snapshot.json"
	DefaultLocalJPPollInterval = 30 * time.Second
)

var localNewCardFiles = []string{
	"cards.json", "events.json", "eventCards.json",
	"costume3ds.json", "cardCostume3ds.json", "character3ds.json", "gameCharacters.json",
}

// LocalJPConfig 描述只读本地 JP 主数据源。源目录不会被 helper 修改。
type LocalJPConfig struct {
	MasterDir   string
	VersionFile string
}

// LocalNewCardSnapshot 是 bot 新卡差分的单文件权威快照。
type LocalNewCardSnapshot struct {
	Revision       string           `json:"revision"`
	Cards          []map[string]any `json:"cards"`
	Events         []map[string]any `json:"events"`
	EventCards     []map[string]any `json:"eventCards"`
	Costume3ds     []map[string]any `json:"costume3ds"`
	CardCostume3ds []map[string]any `json:"cardCostume3ds"`
	Character3ds   []map[string]any `json:"character3ds"`
	GameCharacters []map[string]any `json:"gameCharacters"`
}

type localJPCapture struct {
	revision       string
	files          []string
	cards          []map[string]any
	events         []map[string]any
	eventCards     []map[string]any
	costume3ds     []map[string]any
	cardCostume3ds []map[string]any
	character3ds   []map[string]any
	gameCharacters []map[string]any
}

// ConfigureLocalJP 启用 JP 本地主数据模式。启用后 JP 不再接受远端 SyncFile 覆盖。
func (s *Syncer) ConfigureLocalJP(masterDir, versionFile string) error {
	masterDir = strings.TrimSpace(masterDir)
	versionFile = strings.TrimSpace(versionFile)
	if masterDir == "" || versionFile == "" {
		return fmt.Errorf("local JP master directory and version file must both be configured")
	}
	s.localJP = &LocalJPConfig{
		MasterDir:   filepath.Clean(masterDir),
		VersionFile: filepath.Clean(versionFile),
	}
	return nil
}

// LocalJPEnabled 报告是否启用了本地 JP 模式。
func (s *Syncer) LocalJPEnabled() bool {
	return s.localJP != nil
}

// RunLocalJP 独立轮询本地 JP 源，不会阻塞其他区服的远端同步。
func (s *Syncer) RunLocalJP(ctx context.Context, interval time.Duration) {
	if interval <= 0 {
		interval = DefaultLocalJPPollInterval
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	s.syncLocalJPAndLog(ctx)
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			s.syncLocalJPAndLog(ctx)
		}
	}
}

func (s *Syncer) syncLocalJPAndLog(ctx context.Context) {
	changed, err := s.SyncLocalJP(ctx)
	if err != nil {
		if ctx.Err() == nil {
			log.Printf("[masterdata] local JP snapshot rejected, keeping last successful snapshot: %v", err)
		}
		return
	}
	if changed {
		log.Printf("[masterdata] local JP snapshot published")
	}
}

// SyncLocalJP 读取两次本地源并核对版本与内容指纹；仅在完整发布后替换新卡快照。
func (s *Syncer) SyncLocalJP(ctx context.Context) (bool, error) {
	if !s.LocalJPEnabled() {
		return false, fmt.Errorf("local JP mode is not configured")
	}
	probe, err := probeLocalJP(ctx, *s.localJP)
	if err != nil {
		return false, err
	}
	if probe == s.lastLocalJPProbe {
		return false, nil
	}
	if err := os.MkdirAll(s.outDir, 0o755); err != nil {
		return false, fmt.Errorf("prepare output directory: %w", err)
	}
	stageDir, err := os.MkdirTemp(s.outDir, ".jp-local-stage-*")
	if err != nil {
		return false, fmt.Errorf("create local JP staging directory: %w", err)
	}
	defer os.RemoveAll(stageDir)

	first, err := inspectLocalJPSource(ctx, *s.localJP, stageDir, true)
	if err != nil {
		return false, err
	}
	if s.afterLocalJPCapture != nil {
		s.afterLocalJPCapture()
	}
	second, err := inspectLocalJPSource(ctx, *s.localJP, "", false)
	if err != nil {
		return false, fmt.Errorf("verify local JP source: %w", err)
	}
	if first.revision != second.revision || !sameStrings(first.files, second.files) {
		return false, fmt.Errorf("local JP source changed while snapshot was being collected")
	}

	changed := false
	for _, name := range first.files {
		data, err := os.ReadFile(filepath.Join(stageDir, name))
		if err != nil {
			return false, fmt.Errorf("read staged %s: %w", name, err)
		}
		fileChanged, err := s.writeIfChanged(localJPRegion, name, data)
		if err != nil {
			return false, fmt.Errorf("publish %s: %w", name, err)
		}
		changed = changed || fileChanged
	}

	snapshotData, err := json.Marshal(LocalNewCardSnapshot{
		Revision:       first.revision,
		Cards:          first.cards,
		Events:         first.events,
		EventCards:     first.eventCards,
		Costume3ds:     first.costume3ds,
		CardCostume3ds: first.cardCostume3ds,
		Character3ds:   first.character3ds,
		GameCharacters: first.gameCharacters,
	})
	if err != nil {
		return false, fmt.Errorf("encode local new-card snapshot: %w", err)
	}
	snapshotChanged, err := s.writeIfChanged(localJPRegion, LocalNewCardSnapshotFile, snapshotData)
	if err != nil {
		return false, fmt.Errorf("publish local new-card snapshot: %w", err)
	}

	// 只有全体主数据和单文件快照均成功写入后，才记住本轮源指纹。
	s.lastLocalJPProbe = probe
	return changed || snapshotChanged, nil
}

func probeLocalJP(ctx context.Context, cfg LocalJPConfig) (string, error) {
	version, _, err := readLocalJPVersion(cfg.VersionFile)
	if err != nil {
		return "", err
	}
	files, err := listLocalJPFiles(cfg.MasterDir)
	if err != nil {
		return "", err
	}
	required := make(map[string]bool, len(localNewCardFiles))
	for _, name := range localNewCardFiles {
		required[name] = false
	}
	h := sha256.New()
	writeDigestPart(h, "version", version)
	for _, name := range files {
		if err := ctx.Err(); err != nil {
			return "", err
		}
		path := filepath.Join(cfg.MasterDir, name)
		info, err := os.Stat(path)
		if err != nil {
			return "", fmt.Errorf("stat local JP %s: %w", name, err)
		}
		_, _ = fmt.Fprintf(h, "file\x00%s\x00%d\x00%d\n", name, info.Size(), info.ModTime().UnixNano())
		if _, ok := required[name]; ok {
			data, err := os.ReadFile(path)
			if err != nil {
				return "", fmt.Errorf("read local JP %s: %w", name, err)
			}
			if !json.Valid(data) {
				return "", fmt.Errorf("local JP %s is invalid JSON", name)
			}
			writeDigestPart(h, name, data)
			required[name] = true
		}
	}
	for name, found := range required {
		if !found {
			return "", fmt.Errorf("local JP source is missing %s", name)
		}
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func inspectLocalJPSource(ctx context.Context, cfg LocalJPConfig, stageDir string, decodeCore bool) (localJPCapture, error) {
	version, versionLabel, err := readLocalJPVersion(cfg.VersionFile)
	if err != nil {
		return localJPCapture{}, err
	}
	files, err := listLocalJPFiles(cfg.MasterDir)
	if err != nil {
		return localJPCapture{}, err
	}
	capture := localJPCapture{files: files}
	h := sha256.New()
	writeDigestPart(h, "version", version)
	seenCore := make(map[string]bool, len(localNewCardFiles))

	for _, name := range files {
		if err := ctx.Err(); err != nil {
			return localJPCapture{}, err
		}
		if name == LocalNewCardSnapshotFile {
			return localJPCapture{}, fmt.Errorf("local JP source uses reserved file name %s", name)
		}
		data, err := os.ReadFile(filepath.Join(cfg.MasterDir, name))
		if err != nil {
			return localJPCapture{}, fmt.Errorf("read local JP %s: %w", name, err)
		}
		if !json.Valid(data) {
			return localJPCapture{}, fmt.Errorf("local JP %s is invalid JSON", name)
		}
		writeDigestPart(h, name, data)
		if stageDir != "" {
			if err := os.WriteFile(filepath.Join(stageDir, name), data, 0o644); err != nil {
				return localJPCapture{}, fmt.Errorf("stage local JP %s: %w", name, err)
			}
		}
		if decodeCore {
			switch name {
			case "cards.json":
				capture.cards, err = decodeMapArray(data, name)
			case "events.json":
				capture.events, err = decodeMapArray(data, name)
			case "eventCards.json":
				capture.eventCards, err = decodeMapArray(data, name)
			case "costume3ds.json":
				capture.costume3ds, err = decodeMapArray(data, name)
			case "cardCostume3ds.json":
				capture.cardCostume3ds, err = decodeMapArray(data, name)
			case "character3ds.json":
				capture.character3ds, err = decodeMapArray(data, name)
			case "gameCharacters.json":
				capture.gameCharacters, err = decodeMapArray(data, name)
			}
			if err != nil {
				return localJPCapture{}, err
			}
		}
		for _, coreName := range localNewCardFiles {
			if name == coreName {
				seenCore[name] = true
			}
		}
	}
	for _, name := range localNewCardFiles {
		if !seenCore[name] {
			return localJPCapture{}, fmt.Errorf("local JP source is missing %s", name)
		}
	}
	if decodeCore {
		if err := validateCardAssociations(capture.cards, capture.events, capture.eventCards); err != nil {
			return localJPCapture{}, err
		}
	}
	capture.revision = versionLabel + ":" + hex.EncodeToString(h.Sum(nil))
	return capture, nil
}

func readLocalJPVersion(path string) ([]byte, string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, "", fmt.Errorf("read local JP version: %w", err)
	}
	if !json.Valid(data) {
		return nil, "", fmt.Errorf("local JP version is invalid JSON")
	}
	var version map[string]any
	if err := json.Unmarshal(data, &version); err != nil {
		return nil, "", fmt.Errorf("decode local JP version: %w", err)
	}
	label := strings.TrimSpace(fmt.Sprint(version["dataVersion"]))
	if label == "" || label == "<nil>" {
		return nil, "", fmt.Errorf("local JP version is missing dataVersion")
	}
	return data, label, nil
}

func listLocalJPFiles(dir string) ([]string, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, fmt.Errorf("read local JP master directory: %w", err)
	}
	files := make([]string, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			return nil, fmt.Errorf("stat local JP %s: %w", entry.Name(), err)
		}
		if info.Mode().IsRegular() {
			files = append(files, entry.Name())
		}
	}
	sort.Strings(files)
	if len(files) == 0 {
		return nil, fmt.Errorf("local JP master directory contains no JSON files")
	}
	return files, nil
}

func decodeMapArray(data []byte, name string) ([]map[string]any, error) {
	var items []map[string]any
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	if err := decoder.Decode(&items); err != nil {
		return nil, fmt.Errorf("decode local JP %s as object array: %w", name, err)
	}
	if items == nil {
		items = []map[string]any{}
	}
	return items, nil
}

func validateCardAssociations(cards, events, eventCards []map[string]any) error {
	if len(cards) == 0 || len(events) == 0 || len(eventCards) == 0 {
		return fmt.Errorf("local JP cards/events/eventCards must all be non-empty")
	}
	cardIDs, err := collectUniqueIDs(cards, "cards.json")
	if err != nil {
		return err
	}
	eventIDs, err := collectUniqueIDs(events, "events.json")
	if err != nil {
		return err
	}
	for index, link := range eventCards {
		cardID, ok := IntField(link, "cardId")
		if !ok || cardID <= 0 {
			return fmt.Errorf("eventCards.json item %d has invalid cardId", index)
		}
		if _, ok := cardIDs[cardID]; !ok {
			return fmt.Errorf("eventCards.json item %d references missing cardId %d", index, cardID)
		}
		eventID, ok := IntField(link, "eventId")
		if !ok || eventID <= 0 {
			return fmt.Errorf("eventCards.json item %d has invalid eventId", index)
		}
		if _, ok := eventIDs[eventID]; !ok {
			return fmt.Errorf("eventCards.json item %d references missing eventId %d", index, eventID)
		}
	}
	return nil
}

func collectUniqueIDs(items []map[string]any, name string) (map[int64]struct{}, error) {
	ids := make(map[int64]struct{}, len(items))
	for index, item := range items {
		id, ok := IntField(item, "id")
		if !ok || id <= 0 {
			return nil, fmt.Errorf("%s item %d has invalid id", name, index)
		}
		if _, exists := ids[id]; exists {
			return nil, fmt.Errorf("%s contains duplicate id %d", name, id)
		}
		ids[id] = struct{}{}
	}
	return ids, nil
}

func writeDigestPart(h hash.Hash, name string, data []byte) {
	sum := sha256.Sum256(data)
	_, _ = h.Write([]byte(name))
	_, _ = h.Write([]byte{0})
	_, _ = h.Write(sum[:])
}

func sameStrings(a, b []string) bool {
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
