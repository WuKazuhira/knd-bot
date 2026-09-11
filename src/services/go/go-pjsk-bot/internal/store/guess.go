package store

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
)

// GuessRankRow 是猜题排行榜的一条聚合记录。
type GuessRankRow struct {
	UserID int64
	Count  int
}

// AddGuessResult 记录一次猜题结果；使用提示时只更新每日次数。
func (s *Store) AddGuessResult(ctx context.Context, userID, groupID int64, gameType string, difficulty, server int, tipsUsed bool) error {
	if s == nil || s.pool == nil {
		return errors.New("store unavailable")
	}
	now := time.Now()
	var id, daily int
	var raw []byte
	var last time.Time
	err := s.pool.QueryRow(ctx, `
		SELECT id, total_count, daily_count, last_guess_time FROM pjsk_guess_rank
		WHERE user_qq=$1 AND group_id=$2 AND game_type=$3 AND pjsk_type=$4 LIMIT 1
	`, userID, groupID, gameType, server).Scan(&id, &raw, &daily, &last)
	if errors.Is(err, pgx.ErrNoRows) {
		counts := map[string]int{}
		if !tipsUsed {
			counts[fmt.Sprint(difficulty)] = 1
		}
		_, err = s.pool.Exec(ctx, `INSERT INTO pjsk_guess_rank (user_qq, group_id, game_type, total_count, daily_count, pjsk_type, last_guess_time) VALUES ($1,$2,$3,$4,$5,$6,$7)`, userID, groupID, gameType, counts, 1, server, now)
		if err != nil {
			return fmt.Errorf("insert guess result: %w", err)
		}
		return nil
	}
	if err != nil {
		return fmt.Errorf("query guess result: %w", err)
	}
	counts := map[string]int{}
	if len(raw) > 0 {
		_ = json.Unmarshal(raw, &counts)
	}
	if !tipsUsed {
		key := fmt.Sprint(difficulty)
		counts[key]++
	}
	if last.IsZero() || last.Before(time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, now.Location())) {
		daily = 1
	} else {
		daily++
	}
	_, err = s.pool.Exec(ctx, `UPDATE pjsk_guess_rank SET total_count=$1, daily_count=$2, last_guess_time=$3 WHERE id=$4`, counts, daily, now, id)
	if err != nil {
		return fmt.Errorf("update guess result: %w", err)
	}
	return nil
}

// GuessDailyCount 返回用户当天所有猜题类型的获奖次数。
func (s *Store) GuessDailyCount(ctx context.Context, userID, groupID int64) (int, error) {
	if s == nil || s.pool == nil {
		return 0, errors.New("store unavailable")
	}
	var count int
	err := s.pool.QueryRow(ctx, `SELECT COALESCE(SUM(daily_count),0) FROM pjsk_guess_rank WHERE user_qq=$1 AND group_id=$2 AND last_guess_time::date >= CURRENT_DATE`, userID, groupID).Scan(&count)
	if err != nil {
		return 0, fmt.Errorf("query guess daily count: %w", err)
	}
	return count, nil
}

// AddGold 对齐 BagUser.add_gold，保留首次创建用户的初始 100 金币。
func (s *Store) AddGold(ctx context.Context, userID, groupID int64, amount int) error {
	if s == nil || s.pool == nil {
		return errors.New("store unavailable")
	}
	var id int
	err := s.pool.QueryRow(ctx, `SELECT id FROM bag_users WHERE user_qq=$1 AND group_id=$2 LIMIT 1`, userID, groupID).Scan(&id)
	if errors.Is(err, pgx.ErrNoRows) {
		_, err = s.pool.Exec(ctx, `INSERT INTO bag_users (user_qq, group_id, gold, get_total_gold, get_today_gold, spend_total_gold, spend_today_gold, property) VALUES ($1,$2,$3,$4,$4,0,0,'{}'::json)`, userID, groupID, 100+amount, amount)
	} else if err == nil {
		_, err = s.pool.Exec(ctx, `UPDATE bag_users SET gold=gold+$1, get_total_gold=get_total_gold+$1, get_today_gold=get_today_gold+$1 WHERE id=$2`, amount, id)
	}
	if err != nil {
		return fmt.Errorf("add guess gold: %w", err)
	}
	return nil
}

// QueryGuessRank 返回按次数降序排列的排行榜。
func (s *Store) QueryGuessRank(ctx context.Context, groupID int64, gameType string, difficulty *int, server, limit int) ([]GuessRankRow, error) {
	if s == nil || s.pool == nil {
		return nil, errors.New("store unavailable")
	}
	if limit < 1 || limit > 50 {
		limit = 10
	}
	var rows pgx.Rows
	var err error
	if difficulty == nil {
		rows, err = s.pool.Query(ctx, `SELECT user_qq, COALESCE(SUM(value::int),0) FROM pjsk_guess_rank, jsonb_each_text(total_count) WHERE group_id=$1 AND game_type=$2 AND pjsk_type=$3 GROUP BY user_qq ORDER BY 2 DESC, user_qq LIMIT $4`, groupID, gameType, server, limit)
	} else {
		rows, err = s.pool.Query(ctx, `SELECT user_qq, COALESCE(SUM((total_count ->> $4)::int),0) FROM pjsk_guess_rank WHERE group_id=$1 AND game_type=$2 AND pjsk_type=$3 GROUP BY user_qq ORDER BY 2 DESC, user_qq LIMIT $5`, groupID, gameType, server, fmt.Sprint(*difficulty), limit)
	}
	if err != nil {
		return nil, fmt.Errorf("query guess rank: %w", err)
	}
	defer rows.Close()
	out := make([]GuessRankRow, 0)
	for rows.Next() {
		var row GuessRankRow
		if err := rows.Scan(&row.UserID, &row.Count); err != nil {
			return nil, err
		}
		out = append(out, row)
	}
	return out, rows.Err()
}
