package store

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
)

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

type AliasPair struct {
	SongID int
	Alias  string
}

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

func (s *Store) AddAlias(ctx context.Context, songID int, alias string, userQQ, groupID int64, isPass bool) (bool, error) {
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

func (s *Store) DeleteAlias(ctx context.Context, alias string) (bool, error) {
	tag, err := s.pool.Exec(ctx, `DELETE FROM pjsk_songs_alias WHERE song_alias = $1`, alias)
	if err != nil {
		return false, fmt.Errorf("delete alias: %w", err)
	}
	return tag.RowsAffected() > 0, nil
}
