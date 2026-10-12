package subscription

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/notifysub"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
)

// 每包最多50节点是工程保护值，不是平台通用限制。
const vliveForwardNodesPerPacket = 50

// 分组发生在逐群过滤之前。重试不缩小原始批次、不改选普通首项。
func (w *NotifyWorker) deliverVLiveBatches(ctx context.Context, server string, targets map[string][]string, items []FeedItem) error {
	batches := make(map[string][]FeedItem)
	keys := make([]string, 0)
	seenIDs := make(map[string]bool)
	for _, item := range items {
		if item.ID == "" || seenIDs[item.ID] {
			w.logf("[subscription] vlive/%s 跳过空或重复通知 ID=%q", server, item.ID)
			continue
		}
		seenIDs[item.ID] = true
		key := "batch/" + item.BatchKey
		if item.BatchKey == "" {
			key = "single/" + item.ID
		}
		if _, ok := batches[key]; !ok {
			keys = append(keys, key)
		}
		batches[key] = append(batches[key], item)
	}
	var failures []error
	for _, key := range keys {
		batch := batches[key]
		sort.SliceStable(batch, func(i, j int) bool {
			if batch[i].BatchOrder != batch[j].BatchOrder {
				return batch[i].BatchOrder < batch[j].BatchOrder
			}
			return batch[i].ID < batch[j].ID
		})
		for groupID, users := range targets {
			if err := ctx.Err(); err != nil {
				return err
			}
			if err := w.deliverVLiveBatch(ctx, server, groupID, users, batch); err != nil {
				w.logf("[subscription] vlive/%s batch=%s group=%s: %v", server, key, groupID, err)
				failures = append(failures, err)
			}
		}
	}
	return errors.Join(failures...)
}

func (w *NotifyWorker) deliverVLiveBatch(ctx context.Context, server, groupID string, users []string, batch []FeedItem) error {
	// SQLite读取失败时停止本群本批，不把未知状态当作未发送。
	pending := make([]bool, len(batch))
	for i, item := range batch {
		sent, err := w.notify.WasSent(ctx, notifysub.KindVLive, server, item.ID+"/"+groupID)
		if err != nil {
			return fmt.Errorf("读取投递状态: %w", err)
		}
		pending[i] = !sent
	}
	var failures []error
	ordinaryCount := len(batch)
	if len(batch) > 3 {
		ordinaryCount = 1
	}
	for i := 0; i < ordinaryCount; i++ {
		if !pending[i] {
			continue
		}
		// VLive普通消息只走Message；@不进入转发节点。
		if err := w.sendGroup(ctx, groupID, withAtUsers(batch[i].Message, users)); err != nil {
			failures = append(failures, fmt.Errorf("普通消息 %s: %w", batch[i].ID, err))
			continue
		}
		if err := w.markVLivePacket(ctx, server, groupID, batch[i:i+1]); err != nil {
			failures = append(failures, err)
		}
	}
	if len(batch) <= 3 {
		return errors.Join(failures...)
	}
	remaining := make([]FeedItem, 0, len(batch)-1)
	for i := 1; i < len(batch); i++ {
		if pending[i] {
			remaining = append(remaining, batch[i])
		}
	}
	if len(remaining) == 0 {
		return errors.Join(failures...)
	}
	forward, ok := w.sender.(ForwardMessenger)
	if !ok {
		return errors.Join(append(failures, errors.New("notification sender does not support group forward"))...)
	}
	id, err := strconv.ParseInt(groupID, 10, 64)
	if err != nil || id <= 0 {
		return errors.Join(append(failures, fmt.Errorf("%w: group_id=%q", ErrInvalidSubscriptionTarget, groupID))...)
	}
	for offset := 0; offset < len(remaining); offset += vliveForwardNodesPerPacket {
		if err := ctx.Err(); err != nil {
			return errors.Join(append(failures, err)...)
		}
		end := offset + vliveForwardNodesPerPacket
		if end > len(remaining) {
			end = len(remaining)
		}
		packet := remaining[offset:end]
		nodes := make([]onebot.ForwardNode, 0, len(packet))
		for _, item := range packet {
			nodes = append(nodes, onebot.NewForwardNode("KNDBOT 虚拟Live通知", 0, item.Message))
		}
		if err := forward.SendGroupForwardMessageContext(ctx, id, nodes); err != nil {
			failures = append(failures, fmt.Errorf("转发包 %d: %w", offset/vliveForwardNodesPerPacket+1, err))
			continue
		}
		if err := w.markVLivePacket(ctx, server, groupID, packet); err != nil {
			failures = append(failures, err)
		}
	}
	return errors.Join(failures...)
}

func (w *NotifyWorker) markVLivePacket(ctx context.Context, server, groupID string, packet []FeedItem) error {
	ids := make([]string, 0, len(packet))
	for _, item := range packet {
		ids = append(ids, item.ID+"/"+groupID)
	}
	// 只有提交成功才算记账；不以内存缓存掩盖数据库失败。
	if err := w.notify.MarkSentBatch(ctx, notifysub.KindVLive, server, ids, time.Now()); err != nil {
		return fmt.Errorf("写入投递状态: %w", err)
	}
	return nil
}
