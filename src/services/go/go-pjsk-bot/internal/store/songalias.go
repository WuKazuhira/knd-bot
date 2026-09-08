package store

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
)

// QuerySongID 按别名查歌曲 id，对齐 PjskSongsAlias.query_sid。未找到返回 (0, false)。
func (s *Store) QuerySongID(ctx context.Context, alias string) (int, bool, error) {
	var songID int
	err := s.pool.QueryRow(ctx,
		`SELECT song_id FROM pjsk_songs_alias WHERE song_alias = $1 LIMIT 1`, alias).Scan(&songID)
	if errors.Is(err, pgx.ErrNoRows) {
		return 0, false, nil
	}
	if err != nil {
		return 0, false, fmt.Errorf("query song alias: %w", err)
	}
	return songID, true, nil
}

// AliasPair 是 (歌曲id, 别名) 对，供本地模糊匹配候选。
type AliasPair struct {
	SongID int
	Alias  string
}

// QueryAliasPairs 读取全部别名对，对齐 query_alias_pairs。
func (s *Store) QueryAliasPairs(ctx context.Context) ([]AliasPair, error) {
	rows, err := s.pool.Query(ctx, `SELECT song_id, song_alias FROM pjsk_songs_alias`)
	if err != nil {
		return nil, fmt.Errorf("query alias pairs: %w", err)
	}
	defer rows.Close()
	var out []AliasPair
	for rows.Next() {
		var p AliasPair
		if err := rows.Scan(&p.SongID, &p.Alias); err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, rows.Err()
}

// AddAlias 新增别名，别名已存在（唯一冲突）返回 false。对齐 add_alias 的去重语义。
func (s *Store) AddAlias(ctx context.Context, songID int, alias string, userQQ, groupID int64, isPass bool) (bool, error) {
	// 别名已存在则不重复添加
	if _, exists, err := s.QuerySongID(ctx, alias); err != nil {
		return false, err
	} else if exists {
		return false, nil
	}
	_, err := s.pool.Exec(ctx,
		`INSERT INTO pjsk_songs_alias (song_id, song_alias, user_qq, group_id, join_time, is_pass)
		 VALUES ($1, $2, $3, $4, $5, $6)`,
		songID, alias, userQQ, groupID, time.Now(), isPass)
	if err != nil {
		return false, fmt.Errorf("add alias: %w", err)
	}
	return true, nil
}

// DeleteAlias 删除别名，返回是否确有删除。对齐 delete_alias。
func (s *Store) DeleteAlias(ctx context.Context, alias string) (bool, error) {
	tag, err := s.pool.Exec(ctx, `DELETE FROM pjsk_songs_alias WHERE song_alias = $1`, alias)
	if err != nil {
		return false, fmt.Errorf("delete alias: %w", err)
	}
	return tag.RowsAffected() > 0, nil
}
