"""B站动态轮询与逐群投递。"""

import asyncio
import time


class DynamicPushService:
    def __init__(self, store, client, send, enabled, logger, max_per_round=3):
        self.store = store
        self.client = client
        self.send = send
        self.enabled = enabled
        self.logger = logger
        self.max_per_round = max_per_round
        self._poll_lock = asyncio.Lock()
        self._subscription_lock = asyncio.Lock()
        self._retry = {}
        self._stopping = False

    async def subscribe(self, bot_id, group_id, uid, limit=20):
        """接口成功后才保存订阅；同 UID 在不同群分别建立基线。"""
        async with self._subscription_lock:
            if self._stopping:
                raise RuntimeError('动态订阅服务正在关闭')
            existing = await self.store.list_subscriptions(bot_id, group_id)
            if any(sub.uid == uid for sub in existing):
                return 'exists'
            if len(existing) >= limit:
                return 'limit'
            created_at = int(time.time())
            dynamics = await self.client.fetch(uid)
            if self._stopping:
                raise RuntimeError('动态订阅服务正在关闭')
            baseline = getattr(dynamics, 'high_watermark', max((item.id for item in dynamics), default=0))
            name = dynamics[-1].name if dynamics else f'UID {uid}'
            return await self.store.add(bot_id, group_id, uid, name, baseline, created_at, limit)

    async def unsubscribe(self, bot_id, group_id, uid):
        # 与发送前的最后一次检查共用锁，取消成功之后不再产生新的投递。
        async with self._subscription_lock:
            if self._stopping:
                raise RuntimeError('动态订阅服务正在关闭')
            return await self.store.remove(bot_id, group_id, uid)

    async def poll(self, online_bot_ids):
        if self._stopping or self._poll_lock.locked():
            return
        async with self._poll_lock:
            subscriptions = await self.store.list_subscriptions()
            active_uids = {sub.uid for sub in subscriptions}
            self._retry = {uid: retry for uid, retry in self._retry.items() if uid in active_uids}
            by_uid = {}
            for sub in subscriptions:
                if sub.bot_id in online_bot_ids and self.enabled(sub):
                    by_uid.setdefault(sub.uid, []).append(sub)
            semaphore = asyncio.Semaphore(2)

            async def run(uid, subscribers):
                async with semaphore:
                    try:
                        await self._process_uid(uid, subscribers)
                    except Exception as error:
                        # 不记录请求对象，防止 Cookie、代理凭据随异常泄露。
                        self.logger.warning(f'[B站动态订阅] UID {uid} 处理失败：{type(error).__name__}')

            await asyncio.gather(*(run(uid, subs) for uid, subs in by_uid.items()))
            await self.store.prune()

    async def _process_uid(self, uid, subscriptions):
        failures, retry_at = self._retry.get(uid, (0, 0))
        if time.monotonic() >= retry_at:
            try:
                head = await self.store.head(uid)
                dynamics = await self.client.fetch(uid, after_id=head)
                await self.store.save_dynamics(uid, dynamics)
                self._retry.pop(uid, None)
            except Exception as error:
                failures += 1
                delay = min(60 * 2 ** min(failures, 6), 3600)
                self._retry[uid] = (failures, time.monotonic() + delay)
                code = getattr(error, 'code', 'network')
                self.logger.warning(f'[B站动态订阅] UID {uid} 抓取失败（{code}），{delay}秒后重试')
        # 抓取失败仍可投递此前已缓存的动态，不让其它群的失败阻塞成功群。
        for snapshot in subscriptions:
            if self._stopping:
                return
            subscription = await self.store.get(snapshot.id)
            if subscription is None or subscription.retry_at > int(time.time()):
                continue
            pending = await self.store.pending(subscription, limit=self.max_per_round)
            for item in pending:
                async with self._subscription_lock:
                    current = await self.store.get(snapshot.id)
                    if current is None or not self.enabled(current) or self._stopping:
                        break
                    if item.id <= current.cursor:
                        continue
                    try:
                        await self.send(current.bot_id, current.group_id, item)
                    except Exception as error:
                        await self.store.mark_failed(current.id)
                        self.logger.warning(
                            f'[B站动态订阅] 群 {current.group_id} 动态 {item.id} 发送失败：{type(error).__name__}'
                        )
                        break
                    # 仅成功后记账。发送成功但进程崩溃前未记账时可能重试，不宣称跨系统严格恰好一次。
                    await self.store.mark_sent(current.id, item.id)

    async def close(self):
        self._stopping = True
        async with self._poll_lock:
            async with self._subscription_lock:
                await self.client.close()
                await self.store.close()
