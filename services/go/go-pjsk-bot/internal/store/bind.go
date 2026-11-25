package store

import (
	"context"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Store struct {
	pool *pgxpool.Pool
}

func Open(ctx context.Context, databaseURL string) (*Store, error) {
	if databaseURL == "" {
		return nil, errors.New("DATABASE_URL 未配置")
	}
	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		return nil, fmt.Errorf("连接 postgres: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("ping postgres: %w", err)
	}
	return &Store{pool: pool}, nil
}

func (s *Store) Close() {
	if s.pool != nil {
		s.pool.Close()
	}
}

type Bind struct {
	UserQQ    int64
	PjskUID   int64
	PjskType  int
	IsPrivate bool
}

func (s *Store) AddBind(ctx context.Context, userQQ, pjskUID int64, pjskType int) error {
	_, err := s.pool.Exec(ctx, `
		INSERT INTO pjsk_bind (user_qq, pjsk_uid, pjsk_type, isprivate)
		VALUES ($1, $2, $3, false)
		ON CONFLICT (user_qq, pjsk_type)
		DO UPDATE SET pjsk_uid = EXCLUDED.pjsk_uid
	`, userQQ, pjskUID, pjskType)
	if err != nil {
		return fmt.Errorf("add bind: %w", err)
	}
	return nil
}

func (s *Store) DelBind(ctx context.Context, userQQ int64, pjskType int) (bool, error) {
	tag, err := s.pool.Exec(ctx,
		`DELETE FROM pjsk_bind WHERE user_qq = $1 AND pjsk_type = $2`,
		userQQ, pjskType)
	if err != nil {
		return false, fmt.Errorf("del bind: %w", err)
	}
	return tag.RowsAffected() > 0, nil
}

func (s *Store) SetLook(ctx context.Context, userQQ int64, isPrivate bool, pjskType int) (bool, error) {
	tag, err := s.pool.Exec(ctx,
		`UPDATE pjsk_bind SET isprivate = $1 WHERE user_qq = $2 AND pjsk_type = $3`,
		isPrivate, userQQ, pjskType)
	if err != nil {
		return false, fmt.Errorf("set look: %w", err)
	}
	return tag.RowsAffected() > 0, nil
}

func (s *Store) CheckExists(ctx context.Context, userQQ int64, pjskType int) (bool, error) {
	_, _, ok, err := s.getUserBind(ctx, userQQ, pjskType)
	return ok, err
}

func (s *Store) GetUserBind(ctx context.Context, userQQ int64, pjskType int) (int64, bool, bool, error) {
	return s.getUserBind(ctx, userQQ, pjskType)
}

func (s *Store) getUserBind(ctx context.Context, userQQ int64, pjskType int) (int64, bool, bool, error) {
	var uid int64
	var isPrivate bool
	err := s.pool.QueryRow(ctx,
		`SELECT pjsk_uid, isprivate FROM pjsk_bind WHERE user_qq = $1 AND pjsk_type = $2`,
		userQQ, pjskType).Scan(&uid, &isPrivate)
	if errors.Is(err, pgx.ErrNoRows) {
		return 0, false, false, nil
	}
	if err != nil {
		return 0, false, false, fmt.Errorf("query bind: %w", err)
	}
	return uid, isPrivate, true, nil
}
