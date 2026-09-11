package pjsk

import (
	"crypto/sha256"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

var dedupPrefixes = []string{
	"ondemand/music/long/",
	"music/long/",
	"startapp/music/music_score/",
	"startapp/music/jacket/",
	"startapp/character/member/",
	"startapp/thumbnail/chara/",
	"charts/",
}

type DedupStats struct {
	Scanned    int
	Candidates int
	Duplicates int
	Linked     int
	Symlinked  int
	Skipped    int
	Errors     int
	SavedBytes int64
}

func deduplicate(root string, regions []string, apply bool) (map[string]DedupStats, error) {
	result := make(map[string]DedupStats, len(regions))
	for _, region := range regions {
		stats, err := deduplicateRegion(filepath.Join(root, "jp"), filepath.Join(root, region), apply)
		if err != nil {
			return result, err
		}
		result[region] = stats
	}
	return result, nil
}

func deduplicateRegion(jpRoot, regionRoot string, apply bool) (DedupStats, error) {
	var stats DedupStats
	if _, err := os.Stat(regionRoot); os.IsNotExist(err) {
		return stats, nil
	} else if err != nil {
		return stats, err
	}
	err := filepath.WalkDir(regionRoot, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			stats.Errors++
			return nil
		}
		if entry.IsDir() {
			return nil
		}
		if entry.Type()&os.ModeSymlink != 0 || !entry.Type().IsRegular() {
			return nil
		}
		stats.Scanned++
		rel, err := filepath.Rel(regionRoot, path)
		if err != nil {
			stats.Errors++
			return nil
		}
		rel = filepath.ToSlash(rel)
		if !isDedupCandidate(rel) {
			return nil
		}
		stats.Candidates++
		source := filepath.Join(jpRoot, filepath.FromSlash(rel))
		if !isRegular(source) {
			stats.Skipped++
			return nil
		}
		targetInfo, err := os.Stat(path)
		if err != nil {
			stats.Errors++
			return nil
		}
		if !sameFileContent(source, path) {
			return nil
		}
		stats.Duplicates++
		stats.SavedBytes += targetInfo.Size()
		if !apply {
			return nil
		}
		method, err := replaceWithLink(source, path)
		if err != nil {
			stats.Errors++
			return nil
		}
		switch method {
		case "hardlink":
			stats.Linked++
		case "symlink":
			stats.Symlinked++
		default:
			stats.Skipped++
		}
		return nil
	})
	return stats, err
}

func isDedupCandidate(relative string) bool {
	relative = strings.TrimLeft(filepath.ToSlash(relative), "/")
	for _, prefix := range dedupPrefixes {
		if strings.HasPrefix(relative, prefix) {
			return true
		}
	}
	return false
}

func isRegular(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}

func sameFileContent(source, target string) bool {
	sourceInfo, err := os.Stat(source)
	if err != nil {
		return false
	}
	targetInfo, err := os.Stat(target)
	if err != nil || sourceInfo.Size() != targetInfo.Size() {
		return false
	}
	sourceHash, err := sha256File(source)
	if err != nil {
		return false
	}
	targetHash, err := sha256File(target)
	return err == nil && sourceHash == targetHash
}

func sha256File(path string) ([32]byte, error) {
	file, err := os.Open(path)
	if err != nil {
		return [32]byte{}, err
	}
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return [32]byte{}, err
	}
	var out [32]byte
	copy(out[:], hash.Sum(nil))
	return out, nil
}

func replaceWithLink(source, target string) (string, error) {
	sourceInfo, err := os.Stat(source)
	if err != nil {
		return "", err
	}
	targetInfo, err := os.Stat(target)
	if err != nil {
		return "", err
	}
	if os.SameFile(sourceInfo, targetInfo) {
		return "existing", nil
	}
	tmp, err := os.CreateTemp(filepath.Dir(target), ".dedup-*")
	if err != nil {
		return "", err
	}
	tmpName := tmp.Name()
	if err := tmp.Close(); err != nil {
		_ = os.Remove(tmpName)
		return "", err
	}
	_ = os.Remove(tmpName)
	method := "hardlink"
	if err := os.Link(source, tmpName); err != nil {
		method = "symlink"
		rel, err := filepath.Rel(filepath.Dir(target), source)
		if err != nil {
			return "", err
		}
		if err := os.Symlink(rel, tmpName); err != nil {
			return "", err
		}
	}
	if err := os.Rename(tmpName, target); err != nil {
		_ = os.Remove(tmpName)
		return "", fmt.Errorf("replace dedup target: %w", err)
	}
	return method, nil
}
