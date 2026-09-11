package pjsk

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/store"
)

// AliasSyncer 从 Haruki 别名 API 增量写入共享 PostgreSQL 别名表。
type AliasSyncer struct {
	md       *masterdata.Loader
	db       *store.Store
	endpoint string
	client   *http.Client
}

func NewAliasSyncer(md *masterdata.Loader, db *store.Store, endpoint string) *AliasSyncer {
	if endpoint == "" {
		endpoint = os.Getenv("PJSK_MUSIC_ALIAS_API_URL")
	}
	if endpoint == "" {
		endpoint = "https://neo-api.haruki.seiunx.com/api/bot/v2/pjsk/alias/music/{music_id}"
	}
	return &AliasSyncer{md: md, db: db, endpoint: endpoint, client: &http.Client{Timeout: 15 * time.Second}}
}

func (s *AliasSyncer) Run(ctx context.Context, interval time.Duration) {
	if interval <= 0 {
		interval = 24 * time.Hour
	}
	s.Sync(ctx)
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			s.Sync(ctx)
		}
	}
}

func (s *AliasSyncer) Sync(ctx context.Context) (int, error) {
	if s.md == nil || s.db == nil {
		return 0, nil
	}
	musics, err := s.md.Load("musics.json", 0)
	if err != nil {
		return 0, err
	}
	updated := 0
	for _, music := range musics {
		id, ok := masterdata.IntField(music, "id")
		if !ok {
			continue
		}
		aliases, err := s.fetch(ctx, id)
		if err != nil {
			continue
		}
		for _, alias := range aliases {
			added, err := s.db.AddAlias(ctx, int(id), alias, 114514, 114514, true)
			if err != nil {
				continue
			}
			if added {
				updated++
			}
		}
	}
	return updated, nil
}

func (s *AliasSyncer) fetch(ctx context.Context, musicID int64) ([]string, error) {
	url := strings.ReplaceAll(s.endpoint, "{music_id}", fmt.Sprint(musicID))
	var last error
	for attempt := 0; attempt < 3; attempt++ {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		if err != nil {
			return nil, err
		}
		resp, err := s.client.Do(req)
		if err == nil {
			body, readErr := io.ReadAll(io.LimitReader(resp.Body, 2<<20))
			resp.Body.Close()
			if readErr == nil && resp.StatusCode == http.StatusOK {
				return parseAliases(body)
			}
			if readErr != nil {
				last = readErr
			} else {
				last = fmt.Errorf("HTTP %d", resp.StatusCode)
			}
		} else {
			last = err
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
	return nil, last
}

func parseAliases(raw []byte) ([]string, error) {
	var list []struct {
		Alias string `json:"alias"`
	}
	if err := json.Unmarshal(raw, &list); err == nil {
		out := make([]string, 0, len(list))
		for _, item := range list {
			if strings.TrimSpace(item.Alias) != "" {
				out = append(out, strings.TrimSpace(item.Alias))
			}
		}
		return out, nil
	}
	var wrapped struct {
		Aliases []string `json:"aliases"`
	}
	if err := json.Unmarshal(raw, &wrapped); err != nil {
		return nil, err
	}
	out := make([]string, 0, len(wrapped.Aliases))
	for _, alias := range wrapped.Aliases {
		if strings.TrimSpace(alias) != "" {
			out = append(out, strings.TrimSpace(alias))
		}
	}
	return out, nil
}
