from typing import Any, Dict

import nonebot
from nonebot import logger
from nonebot.adapters.onebot.v11 import ActionFailed, Bot

from utils.utils import CountLimiter, FreqLimiter

# 风控提醒cd
_fk_fqlmt = FreqLimiter(3600, 3)  # 1h内3次消息发送失败则视为可能风控
_fk_warn_clmt = CountLimiter(1)
_notifying: set[str] = set()
_RISK_CONTROL_APIS = frozenset({
    "send_msg",
    "send_private_msg",
    "send_group_msg",
    "send_group_forward_msg",
    "send_private_forward_msg",
})


# 风控提醒
@Bot.on_called_api
async def handle_api_call(bot: Bot, exception, api: str, data: Dict[str, Any], result):
    if exception is None or not isinstance(exception, ActionFailed):
        return
    if api not in _RISK_CONTROL_APIS:
        return
    bot_qq = bot.self_id
    if bot_qq in _notifying or (api == "send_private_msg" and bool(_notifying)):
        return
    # 未满足风控检测条件时
    if _fk_fqlmt.count_check(bot_qq):
        # 准备提醒
        _fk_warn_clmt.clear(bot_qq)
        _fk_fqlmt.start_cd(bot_qq)
    # 满足风控检测条件时(1小时3次)
    elif _fk_warn_clmt.get_count(bot_qq) == 0:
        superusers = list(bot.config.superusers)
        if not superusers:
            logger.warning(f"风控提醒跳过：未配置 superusers (bot={bot_qq})")
            return
        super_qq = superusers[0]
        msg = f"奏宝bot({bot_qq})疑似被风控，请及时解除"
        _fk_warn_clmt.add(bot_qq)
        _notifying.add(bot_qq)
        try:
            await bot.send_private_msg(user_id=int(super_qq), message=msg)
        # 风控到私聊都发不出去, 尝试用另一只bot提醒
        except ActionFailed:
            other_bot = next(
                (each for each in nonebot.get_bots().values() if each.self_id != bot_qq),
                None,
            )
            if other_bot is not None:
                try:
                    await other_bot.send_private_msg(user_id=int(super_qq), message=msg)
                except Exception as e:
                    logger.warning(
                        f"风控提醒备用 bot({other_bot.self_id}) 发送失败 (原 bot={bot_qq}): {e}"
                    )
            else:
                logger.warning(f"风控提醒发送失败且无其他在线 bot 可代发 (bot={bot_qq})")
        except Exception as e:
            logger.warning(f"风控提醒发送异常 (bot={bot_qq}): {e}")
        finally:
            _notifying.discard(bot_qq)


# bot掉线通知
driver = nonebot.get_driver()


@driver.on_bot_disconnect
async def bot_disconnect(bot: Bot):
    bot_qq = bot.self_id
    superusers = list(bot.config.superusers)
    if not superusers:
        logger.warning(f"bot掉线通知跳过：未配置 superusers (bot={bot_qq})")
        return
    super_qq = superusers[0]
    bots = list(nonebot.get_bots().values())
    msg = f"检测到奏宝bot({bot_qq})掉线，请及时处理"
    if 1 <= len(bots) <= 4:
        other_bot = next((each for each in bots if each.self_id != bot_qq), None)
        if other_bot is not None:
            try:
                await other_bot.send_private_msg(user_id=int(super_qq), message=msg)
            except Exception as e:
                logger.warning(
                    f"bot掉线通知发送失败 (掉线 bot={bot_qq}, 代发 bot={other_bot.self_id}): {e}"
                )
