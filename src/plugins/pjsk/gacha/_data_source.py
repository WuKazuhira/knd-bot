import json
import random
import time
from typing import Union

from nonebot.adapters.onebot.v11 import Message

from services.pjsk_draw import render
from utils.message_builder import image

from .._card_utils import getcharaname
from .._utils import async_load_master_data, load_master_data


def getcurrentgacha(pjsk_type: int = 0):
    """
    获取当前的gacha卡池信息
    返回最新的进行中卡池
    """
    from services.log import logger
    
    data = load_master_data('gachas.json', pjsk_type)
    now = int(round(time.time() * 1000))
    
    logger.debug(f"[getcurrentgacha] 服务器类型: {pjsk_type}, 当前时间戳: {now}")
    logger.debug(f"[getcurrentgacha] 加载了 {len(data)} 个卡池")
    
    # 从后往前遍历，返回最新的进行中卡池
    for gacha in reversed(data):
        startAt = int(gacha['startAt'])
        endAt = int(gacha['endAt'])
        
        logger.debug(f"[getcurrentgacha] 检查卡池 {gacha['id']}: {gacha['name']}, 开始={startAt}, 结束={endAt}")
        
        # 检查卡池是否在进行中
        if startAt < now < endAt:
            logger.info(f"[getcurrentgacha] 找到当前卡池: {gacha['id']} - {gacha['name']}")
            # 返回第一个进行中的卡池
            return {
                'id': gacha['id'],
                'name': gacha['name'],
                'gachaDetails': gacha.get('gachaDetails', []),
                'gachaPickups': gacha.get('gachaPickups', []),
                'gachaCardRarityRates': gacha.get('gachaCardRarityRates', [])
            }
    
    logger.warning(f"[getcurrentgacha] 没有找到进行中的卡池")
    # 如果都没有，返回None
    return None


async def fakegacha(gachaid: int, num: int, isreverse=False, pjsk_type: int = 0) -> Union[str, Message]:
    """
    进行假抽卡
    :param gachaid: 卡池id
    :param num: 抽卡数量
    :param isreverse: 四星、二星概率交换
    """
    from services.log import logger
    
    try:
        logger.debug(f"[fakegacha] 开始加载卡池数据: gachaid={gachaid}, pjsk_type={pjsk_type}")
        data = await async_load_master_data('gachas.json', pjsk_type)
        logger.debug(f"[fakegacha] 加载了{len(data)}个卡池")
        
        gacha = None
        
        # 根据卡池ID找到对应的卡池
        for g in data:
            if g['id'] == gachaid:
                gacha = g
                break
        
        if gacha is None:
            logger.warning(f"[fakegacha] 找不到卡池{gachaid}")
            return f'找不到编号为{gachaid}的卡池，命令:/sekai抽卡 /sekaiXX连 /sekai反抽卡，三个命令后面都可以加卡池id'
        
        logger.debug(f"[fakegacha] 找到卡池: {gacha['name']}")
        
        # 获取稀有度概率
        rate4 = 0
        rate3 = 0
        birthday = False
        for rate_info in gacha['gachaCardRarityRates']:
            if rate_info['cardRarityType'] == 'rarity_4':
                rate4 = rate_info['rate']
                break
            if rate_info['cardRarityType'] == 'rarity_birthday':
                rate4 = rate_info['rate']
                birthday = True
                break
        
        for rate_info in gacha['gachaCardRarityRates']:
            if rate_info['cardRarityType'] == 'rarity_3':
                rate3 = rate_info['rate']
        
        logger.debug(f"[fakegacha] 四星概率: {rate4}, 三星概率: {rate3}")
        
        if isreverse:
            rate4 = 100 - rate4 - rate3
        
        logger.debug(f"[fakegacha] 加载卡牌数据...")
        cards = await async_load_master_data('cards.json', pjsk_type)
        logger.debug(f"[fakegacha] 加载了{len(cards)}张卡牌")
        
        reality2 = []
        reality3 = []
        reality4 = []
        allweight = 0
        
        # 获取卡池中的卡牌
        for detail in gacha['gachaDetails']:
            for card in filter(lambda x: True if x['id'] == detail['cardId'] else False, cards):
                if card['cardRarityType'] == 'rarity_2':
                    reality2.append({'id': card['id'], 'prefix': card['prefix'], 'charaid': card['characterId']})
                elif card['cardRarityType'] == 'rarity_3':
                    reality3.append({'id': card['id'], 'prefix': card['prefix'], 'charaid': card['characterId']})
                else:
                    allweight = allweight + detail['weight']
                    reality4.append({'id': card['id'], 'prefix': card['prefix'], 'charaid': card['characterId'],
                                     'weight': detail['weight']})
        
        alltext = ''
        keytext = ''
        baodi = True
        count4 = 0
        count3 = 0
        count2 = 0
        result = []
        
        for i in range(1, num + 1):
            if i % 10 == 0 and baodi and isreverse is not True:
                baodi = False
                rannum = random.randint(0, int(rate4 + rate3) * 2) / 2
            else:
                rannum = random.randint(0, 100)
            
            if rannum < rate4:  # 四星
                count4 += 1
                baodi = False
                nowweight = 0
                rannum2 = random.randint(0, allweight - 1)
                for j in range(0, len(reality4)):
                    nowweight = nowweight + reality4[j]['weight']
                    if nowweight >= rannum2:
                        if birthday:
                            alltext = alltext + "🎀"
                            keytext = keytext + "🎀"
                        else:
                            alltext = alltext + "★★★★"
                            keytext = keytext + "★★★★"
                            alltext = alltext + "[当期]"
                            keytext = keytext + "[当期]"
                        alltext = alltext + f"{reality4[j]['prefix']} - {getcharaname(reality4[j]['charaid'], pjsk_type=pjsk_type)}\n"
                        keytext = keytext + f"{reality4[j]['prefix']} - {getcharaname(reality4[j]['charaid'], pjsk_type=pjsk_type)}(第{i}抽)\n"
                        result.append(reality4[j]['id'])
                        break
            elif rannum < rate4 + rate3:  # 三星
                count3 += 1
                rannum2 = random.randint(0, len(reality3) - 1)
                alltext = alltext + f"★★★{reality3[rannum2]['prefix']} - {getcharaname(reality3[rannum2]['charaid'], pjsk_type=pjsk_type)}\n"
                result.append(reality3[rannum2]['id'])
            else:  # 二星
                count2 += 1
                rannum2 = random.randint(0, len(reality2) - 1)
                alltext = alltext + f"★★{reality2[rannum2]['prefix']} - {getcharaname(reality2[rannum2]['charaid'], pjsk_type=pjsk_type)}\n"
                result.append(reality2[rannum2]['id'])
        
        if num == 10:
            # 数据收集完成，出图交给绘图服务。
            pic = await render('gacha', {'card_ids': result, 'pjsk_type': pjsk_type})
            logger.debug("[fakegacha] 生成十连图片成功")
            return Message(
                f"id:{gacha['id']} [{gacha['name']}]\n" + image(pic)
            )
        elif num < 10:
            logger.debug(f"[fakegacha] 返回文本结果")
            return f"id:{gacha['id']}[{gacha['name']}]\n{alltext}"
        else:
            logger.debug(f"[fakegacha] 返回多抽结果")
            if birthday:
                return f"id:{gacha['id']}[{gacha['name']}]\n{num}抽模拟抽卡，只显示抽到的四星如下:\n{keytext}\n生日卡：{count4} 三星：{count3} 二星：{count2}"
            else:
                return f"id:{gacha['id']}[{gacha['name']}]\n{num}抽模拟抽卡，只显示抽到的四星如下:\n{keytext}\n四星：{count4} 三星：{count3} 二星：{count2}"
    except Exception as e:
        import traceback
        logger.error(f"[fakegacha] 抽卡过程出错: {e}")
        logger.error(f"[fakegacha] 错误堆栈: {traceback.format_exc()}")
        raise
