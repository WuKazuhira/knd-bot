package store

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"time"

	"github.com/jackc/pgx/v5"
)

// RankEntry 是排行榜一项。
type RankEntry struct {
	UserQQ int64
	Count  int
}

// AddGuessCount 记录一次猜对，total_count[难度]++，对齐 PjskGuessRank.add_count。
// 与 Python 共享 pjsk_guess_rank 表（total_count 为 JSON）。
func (s *Store) AddGuessCount(ctx context.Context, userQQ, groupID int64, gameType string, guessDiff, pjskType int) error {
	// 读现有记录
	var raw []byte
	err := s.pool.QueryRow(ctx, `
		SELECT total_count FROM pjsk_guess_rank
		WHERE user_qq=$1 AND group_id=$2 AND game_type=$3 AND pjsk_type=$4`,
		userQQ, groupID, gameType, pjskType).Scan(&raw)

	totalCount := map[string]int{}
	exists := true
	if err != nil {
		if err == pgx.ErrNoRows {
			exists = false
		} else {
			return fmt.Errorf("query guess rank: %w", err)
		}
	}
	if exists && len(raw) > 0 {
		_ = json.Unmarshal(raw, &totalCount)
	}
	diffKey := fmt.Sprintf("%d", guessDiff)
	totalCount[diffKey]++
	encoded, _ := json.Marshal(totalCount)

	if exists {
		_, err = s.pool.Exec(ctx, `
			UPDATE pjsk_guess_rank SET total_count=$1, last_guess_time=$2
			WHERE user_qq=$3 AND group_id=$4 AND game_type=$5 AND pjsk_type=$6`,
			encoded, time.Now(), userQQ, groupID, gameType, pjskType)
	} else {
		_, err = s.pool.Exec(ctx, `
			INSERT INTO pjsk_guess_rank (user_qq, group_id, game_type, total_count, daily_count, pjsk_type, last_guess_time)
			VALUES ($1,$2,$3,$4,0,$5,$6)`,
			userQQ, groupID, gameType, encoded, pjskType, time.Now())
	}
	if err != nil {
		return fmt.Errorf("upsert guess rank: %w", err)
	}
	return nil
}

// GetGuessRank 返回某群某类型的排行榜（按次数降序）。
// guessDiff>0 时只统计该难度，否则汇总所有难度。对齐 PjskGuessRank.get_rank。
func (s *Store) GetGuessRank(ctx context.Context, groupID int64, gameType string, guessDiff, pjskType int) ([]RankEntry, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT user_qq, total_count FROM pjsk_guess_rank
		WHERE group_id=$1 AND game_type=$2 AND pjsk_type=$3`,
		groupID, gameType, pjskType)
	if err != nil {
		return nil, fmt.Errorf("query guess rank list: %w", err)
	}
	defer rows.Close()

	var entries []RankEntry
	for rows.Next() {
		var userQQ int64
		var raw []byte
		if err := rows.Scan(&userQQ, &raw); err != nil {
			return nil, err
		}
		totalCount := map[string]int{}
		if len(raw) > 0 {
			_ = json.Unmarshal(raw, &totalCount)
		}
		count := 0
		if guessDiff > 0 {
			count = totalCount[fmt.Sprintf("%d", guessDiff)]
		} else {
			for _, c := range totalCount {
				count += c
			}
		}
		if count > 0 {
			entries = append(entries, RankEntry{UserQQ: userQQ, Count: count})
		}
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	sort.SliceStable(entries, func(i, j int) bool { return entries[i].Count > entries[j].Count })
	return entries, nil
}
