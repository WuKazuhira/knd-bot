package notifysub

import (
	"context"
	"time"
)

// MarkCardWithCostumes commits parent and inline supplement delivery keys together.
func (s *Store) MarkCardWithCostumes(ctx context.Context, server, cardKey, groupID string, costumeKeys []string, at time.Time) error {
	db, err := s.conn(ctx)
	if err != nil {
		return err
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	mark := func(kind, key string) error {
		_, e := tx.ExecContext(ctx, `INSERT INTO pjsk_notify_delivery_state(kind,server,notification_id,last_sent_at) VALUES(?,?,?,?) ON CONFLICT(kind,server,notification_id) DO UPDATE SET last_sent_at=excluded.last_sent_at`, kind, server, key+"/"+groupID, at.Unix())
		return e
	}
	if err = mark(KindNewCard, cardKey); err != nil {
		return err
	}
	for _, key := range costumeKeys {
		if err = mark("card_costume", key); err != nil {
			return err
		}
	}
	return tx.Commit()
}
