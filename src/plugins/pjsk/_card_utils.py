"""卡牌数据层：卡面类型判定、角色名、团体角色映射。

卡牌的绘制部分已迁至绘图服务 services.pjsk_draw.card。
"""

import os
from typing import Dict, List, Tuple

from services.pjsk_draw import open_pjsk_image

from ._autoask import pjsk_update_manager
from ._config import SERVER_MAP, data_path
from ._utils import async_load_master_data, load_master_data

_CARD_TYPE_INDEX_CACHE: Dict[Tuple[int, int, int, int], set] = {}
_CARD_SUPPLY_TYPE_CACHE: Dict[Tuple[int, int], Dict[int, str]] = {}


# 卡面类型
def cardtype(cardid, cardCostume3ds, costume3ds):
    """返回卡面类型；同一批主数据只构建一次限定卡索引。"""
    cache_key = (id(cardCostume3ds), len(cardCostume3ds), id(costume3ds), len(costume3ds))
    limited_cards = _CARD_TYPE_INDEX_CACHE.get(cache_key)
    if limited_cards is None:
        hair_costumes = {
            item.get('id') for item in costume3ds
            if isinstance(item, dict) and item.get('partType') == 'hair'
        }
        limited_cards = {
            item.get('cardId') for item in cardCostume3ds
            if isinstance(item, dict) and item.get('costume3dId') in hair_costumes
        }
        _CARD_TYPE_INDEX_CACHE.clear()
        _CARD_TYPE_INDEX_CACHE[cache_key] = limited_cards
    return 1 if cardid in limited_cards else 0


# 判断是否为 fes 限定
def is_fes_card(card, card_supplies=None, pjsk_type: int = 0):
    """判断卡面是否是fes限定

    :param card: 卡面数据（dict）或卡面id（int）
    :param card_supplies: cardSupplies.json数据，可以不传
    :param pjsk_type: 服务器类型
    :return: True表示是fes限定，False表示不是
    """
    try:
        # 如果传入的是卡面id，需要先加载cards数据
        if isinstance(card, int):
            cards = load_master_data('cards.json', pjsk_type)
            card_id = card
            card = None
            for c in cards:
                if isinstance(c, dict) and c.get('id') == card_id:
                    card = c
                    break
            if not card:
                return False

        # 取卡池类型
        card_supply_id = card.get('cardSupplyId')
        if not card_supply_id:
            return False

        # 加载cardSupplies数据
        if card_supplies is None:
            card_supplies = load_master_data('cardSupplies.json', pjsk_type)

        cache_key = (id(card_supplies), len(card_supplies))
        supply_types = _CARD_SUPPLY_TYPE_CACHE.get(cache_key)
        if supply_types is None:
            supply_types = {
                supply.get('id'): supply.get('cardSupplyType', '')
                for supply in card_supplies if isinstance(supply, dict) and supply.get('id') is not None
            }
            _CARD_SUPPLY_TYPE_CACHE.clear()
            _CARD_SUPPLY_TYPE_CACHE[cache_key] = supply_types
        supply_type = supply_types.get(card_supply_id, '')
        return supply_type in ('colorful_festival_limited', 'bloom_festival_limited')
    except Exception:
        return False


# 卡面原图（下载 + 转 jpg 控制体积，不做合成）
async def cardidtopic(cardid: int, allcards=None, pjsk_type: int = 0):
    """ 获取卡面大图

    :param cardid: 卡面id
    :param allcards: card.json，可以不传
    """
    if allcards is None:
        allcards = await async_load_master_data('cards.json', pjsk_type)
    assetbundleName = ''
    cardRarityType = ''
    for card in allcards:
        if not isinstance(card, dict):
            continue
        if card['id'] == cardid:
            assetbundleName = card['assetbundleName']
            cardRarityType = card['cardRarityType']
    if assetbundleName == '':
        return []
    if cardRarityType in ["rarity_3", "rarity_4"]:
        cl = ['card_normal.png', 'card_after_training.png']
    else:
        cl = ['card_normal.png']
    for c in cl:
        await pjsk_update_manager.get_asset(f'startapp/character/member/{assetbundleName}', c, pjsk_type=pjsk_type)
    path = data_path / SERVER_MAP.get(pjsk_type, 'jp') / f'startapp/character/member/{assetbundleName}'
    files = os.listdir(path)
    files_file = [f for f in files if (path / f).is_file()]
    if not (path / 'card_normal.jpg').exists():  # 频道bot最多发送4MB 这里转jpg缩小大小
        im = open_pjsk_image(path / 'card_normal.png', mode='RGB')
        im.save(path / 'card_normal.jpg', quality=95)

    if 'card_after_training.png' in files_file:
        if not (path / 'card_after_training.jpg').exists():  # 频道bot最多发送4MB 这里转jpg缩小大小
            im = open_pjsk_image(path / 'card_after_training.png', mode='RGB')
            im.save(path / 'card_after_training.jpg', quality=95)
        return [path / 'card_normal.jpg', path / 'card_after_training.jpg']
    else:
        return [path / 'card_normal.jpg']


# 角色名称
def getcharaname(characterid, gameCharacters=None, pjsk_type: int = 0):
    if gameCharacters is None:
        gameCharacters = load_master_data('gameCharacters.json', pjsk_type)
    for i in gameCharacters:
        if not isinstance(i, dict):
            continue
        if i['id'] == characterid:
            try:
                return i['firstName'] + i['givenName']
            except KeyError:
                return i['givenName']


# 团体内部名 → 该团体的主要角色 characterId 列表（按游戏内编号顺序）
UNIT_MAIN_CHARS: Dict[str, List[int]] = {
    'light_sound':    [1, 2, 3, 4],
    'idol':           [5, 6, 7, 8],
    'street':         [9, 10, 11, 12],
    'theme_park':     [13, 14, 15, 16],
    'school_refusal': [17, 18, 19, 20],
    'piapro':         [21, 22, 23, 24, 25, 26],
}

# UNIT_CHAR_RANGE key → 团体内部名（用于从筛选条件反查）
UNIT_KEY_TO_INTERNAL: Dict[str, str] = {
    'ln': 'light_sound', 'leo': 'light_sound', 'leoneed': 'light_sound', 'light_sound': 'light_sound',
    'mmj': 'idol', 'moremorejump': 'idol', 'idol': 'idol',
    'vbs': 'street', 'vivid': 'street', 'street': 'street',
    'ws': 'theme_park', 'wonderlands': 'theme_park', 'theme_park': 'theme_park',
    '25h': 'school_refusal', '25ji': 'school_refusal', '25': 'school_refusal',
    '25时': 'school_refusal', 'nightcord': 'school_refusal', 'school_refusal': 'school_refusal',
    'vs': 'piapro', 'virtual': 'piapro', 'piapro': 'piapro', 'v': 'piapro',
}


def get_unit_vs_chars(unit_internal: str, gameCharacterUnits: List[Dict]) -> List[int]:
    """
    从 gameCharacterUnits 中找出属于指定团体的虚拟歌手 characterId 列表。
    虚拟歌手 characterId 为 21-26，每人在各团体都有对应的 unit 记录。
    """
    if unit_internal == 'piapro':
        return []  # piapro 本身就是虚拟歌手团，不需要额外附加
    vs_chars = []
    seen = set()
    for entry in gameCharacterUnits:
        if not isinstance(entry, dict):
            continue
        cid = entry.get('gameCharacterId', 0)
        if cid < 21 or cid > 26:
            continue
        if entry.get('unit') == unit_internal and cid not in seen:
            vs_chars.append(cid)
            seen.add(cid)
    vs_chars.sort()
    return vs_chars
