package store

import (
	"context"
	"time"
)

func (s *Store) IsBanned(ctx context.Context, userID, groupID int64) (bool, error) {
	var banned bool
	err := s.pool.QueryRow(ctx, `
		SELECT EXISTS (
			SELECT 1 FROM ban_info
			WHERE (duration = -1 OR ban_time + duration > $3)
			  AND (
				(user_id = $1 AND type = 'all')
				OR ($2::bigint > 0 AND group_id = $2 AND ((user_id = $1 AND type = 'user') OR type = 'group'))
			  )
		)
	`, userID, groupID, time.Now().Unix()).Scan(&banned)
	return banned, err
}
