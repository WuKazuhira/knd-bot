package subscription

import (
	"context"
	"fmt"
	"github.com/kazuhira/go-pjsk-bot/internal/notifysub"
	"strconv"
	"strings"
	"time"
)

type CostumeSource interface {
	FetchCostumes(context.Context) ([]FeedItem, error)
	FetchCardCostumes(context.Context) ([]FeedItem, error)
	AckCostume(context.Context, string) error
	AckCardCostume(context.Context, string) error
}

func (w *NotifyWorker) PollCostume(ctx context.Context) error {
	if w.costume == nil {
		return nil
	}
	items, err := w.costume.FetchCostumes(ctx)
	if err != nil {
		return err
	}
	return w.deliverNews(ctx, notifysub.KindCostume, notifysub.KindCostume, items, w.costume.AckCostume)
}
func (w *NotifyWorker) PollCardCostume(ctx context.Context) error {
	if w.costume == nil {
		return nil
	}
	items, err := w.costume.FetchCardCostumes(ctx)
	if err != nil {
		return err
	}
	return w.deliverNews(ctx, notifysub.KindNewCard, "card_costume", items, w.costume.AckCardCostume)
}
func (w *NotifyWorker) deliverNews(ctx context.Context, subscriptionKind, deliveryKind string, items []FeedItem, ack func(context.Context, string) error) error {
	if w.notify == nil || w.sender == nil {
		return fmt.Errorf("news store/sender unavailable")
	}
	groups, err := w.notify.ListGroups(ctx, subscriptionKind, "jp")
	if err != nil {
		return err
	}
	for _, item := range items {
		if err = ctx.Err(); err != nil {
			return err
		}
		if item.ID == "" {
			continue
		}
		complete := true
		for _, group := range groups {
			if !item.CreatedAt.IsZero() && group.CreatedAt.After(item.CreatedAt) {
				continue
			}
			if _, err = strconv.ParseInt(group.GroupID, 10, 64); err != nil {
				continue
			}
			if deliveryKind == "card_costume" {
				parentID := strings.SplitN(item.ID, "/card-", 2)[0]
				sent, lookupErr := w.notify.WasSent(ctx, notifysub.KindNewCard, "jp", parentID+"/"+group.GroupID)
				if lookupErr != nil || !sent {
					complete = false
					continue
				}
			}
			stateID := item.ID + "/" + group.GroupID
			memoryKey := deliveryKind + "/jp/" + stateID
			if w.notifyWasSent(ctx, deliveryKind, "jp", stateID, memoryKey) {
				continue
			}
			if err = w.sendFeed(ctx, group.GroupID, item, nil); err != nil {
				complete = false
				w.logf("[subscription] %s group=%s: %v", deliveryKind, group.GroupID, err)
				continue
			}
			if err = w.notify.MarkSent(ctx, deliveryKind, "jp", stateID, time.Now()); err != nil {
				complete = false
				continue
			}
			w.markSeen(memoryKey)
		}
		if complete {
			if err = ack(ctx, item.ID); err != nil {
				return err
			}
		}
	}
	return nil
}
