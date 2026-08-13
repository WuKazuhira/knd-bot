import re
from typing import Any, Tuple
from nonebot import on_regex
from nonebot.params import RegexGroup
from ._data_source import fakegacha, getcurrentgacha
from .._config import BUG_ERROR
from services.log import logger

import json

__plugin_name__ = "pjsk抽卡"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.1
__plugin_usage__ = f"""
usage：
    pjsk假抽卡
    若群内已有unibot请勿开启此bot该功能
    由于功能容易刷屏，仅在特定群开放
    私聊可用，限制每人1分钟只能抽2次
    指令：
        sekai抽卡/pjsk抽卡         ?[卡池id]    ：进行一次假十连
        sekai十连/pjsk十连         ?[卡池id]    ：同上
        sekai反十连/pjsk反十连      ?[卡池id]    ：四星概率翻转
        sekai[XX]连/pjsk[XX]连    ?[卡池id]    ：[XX]为数字，进行指定次数的抽卡
    注意：
        以上指令均可以携带卡池id，不携带卡池id时默认抽取当前日服最新的卡池
""".strip()
__plugin_settings__ = {
    "level": 5,
    "default_status": False,
    "cmd": ["pjsk抽卡", "sekai抽卡", "烧烤相关", "uni移植"],
}
__plugin_cd_limit__ = {"cd": 60, "count_limit": 2, "rst": "别急，等[cd]秒后再用！", "limit_type": "user"}
__plugin_block_limit__ = {"rst": "别急，抽卡正在进行中！", "limit_type": "user"}
__plugin_count_limit__ = {
    "max_count": 10,
    "limit_type": "user",
    "rst": "今天已经抽了[count]次了，还请明天再继续呢[at]",
}

# pjsk抽卡
pjsk_gacha = on_regex(r'^(cn|tw|jp)? *(?:pjsk|sekai) *(反向?)? *(抽卡|十连抽?|\d+连抽?) *(\d+)?$', priority=5, block=True)


@pjsk_gacha.handle()
async def _(reg_group: Tuple[Any, ...] = RegexGroup()):
    from nonebot.exception import FinishedException
    
    try:
        prefix = reg_group[0].strip() if reg_group[0] else 'jp'
        pjsk_type = 0
        if prefix == 'cn':
            pjsk_type = 2
        elif prefix == 'tw':
            pjsk_type = 1
        
        logger.debug(f"[gacha] 服务器类型: {pjsk_type}")
        
        isreverse = True if reg_group[1] else False
        if _ := re.sub(r'\D', '', reg_group[2]):
            cardnum = int(_)
        else:
            cardnum = 10
        if cardnum > 300:
            await pjsk_gacha.finish("一次至多指定一井300抽哦", at_sender=True)
        if _ := re.sub(r'\D', '', reg_group[3] if reg_group[3] else ''):
            gachaid = int(_)
        else:
            logger.debug(f"[gacha] 获取当前卡池...")
            current_gacha = getcurrentgacha(pjsk_type=pjsk_type)
            if current_gacha is None:
                logger.warning(f"[gacha] 服务器{pjsk_type}没有进行中的卡池")
                await pjsk_gacha.finish("当前没有进行中的卡池", at_sender=True)
            gachaid = int(current_gacha['id'])
            logger.debug(f"[gacha] 当前卡池ID: {gachaid}")
        
        logger.debug(f"[gacha] 开始抽卡: 卡池{gachaid}, 次数{cardnum}, 反向{isreverse}")
        result = await fakegacha(gachaid, cardnum, isreverse, pjsk_type=pjsk_type)
        logger.debug(f"[gacha] 抽卡成功")
        await pjsk_gacha.finish(result)
    except FinishedException:
        # 这是正常的结束，不是错误
        raise
    except Exception as e:
        import traceback
        logger.error(f"[gacha] 抽卡失败: {e}")
        logger.error(f"[gacha] 错误堆栈: {traceback.format_exc()}")
        await pjsk_gacha.finish(BUG_ERROR)