from nonebot import Driver, get_driver
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent

from services import logger
from services.db_context import db

from ._models import WithdrawBase

withdraw_group_dict = {}
driver: Driver = get_driver()


@driver.on_bot_connect
async def _(bot: Bot):
    global withdraw_group_dict
    bot_id = bot.self_id
    withdraw_group_dict[bot_id] = []
    try:
        gl = [g["group_id"] for g in await bot.get_group_list()]
    except Exception as e:
        logger.warning(f"Bot({bot_id}) 获取群列表失败，改用撤回词库已有群列表: {type(e).__name__}: {e}")
        try:
            rows = await db.all(
                db.select([WithdrawBase.group_id]).where(WithdrawBase.group_id.isnot(None)).distinct()
            )
            gl = [row[0] for row in rows if row[0] is not None]
        except Exception as db_err:
            logger.warning(f"Bot({bot_id}) 查询撤回配置群列表失败: {type(db_err).__name__}: {db_err}")
            return
        for g in gl:
            if g not in withdraw_group_dict[bot_id]:
                withdraw_group_dict[bot_id].append(g)
                logger.info(f"Bot({bot_id})载入了群组{g}的撤回配置")
        return
    for g in gl:
        try:
            if g not in withdraw_group_dict[bot_id] and await WithdrawBase.exist_any_entry(g):
                withdraw_group_dict[bot_id].append(g)
                logger.info(f"Bot({bot_id})载入了群组{g}的撤回配置")
        except Exception as db_err:
            logger.warning(f"Bot({bot_id}) 检查群组{g}撤回配置失败: {type(db_err).__name__}: {db_err}")


async def check(event: GroupMessageEvent) -> bool:
    # 检测群内是否有设置词条，没有就不匹配了
    global withdraw_group_dict
    if event.group_id not in withdraw_group_dict.get(str(event.self_id), []):
        return False

    # 将用户发言转化为可匹配的格式化问句
    entry = await WithdrawBase.message2entry(event.message)
    # 检测问句是否能匹配
    if entry and await WithdrawBase.check(event, entry):
        return True
    else:
        return False

