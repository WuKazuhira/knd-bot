from typing import Tuple, Optional
from utils.http_utils import AsyncHttpx
from ._config import GUESS_CARD, GUESS_MUSIC, pjskguess
from .._config import data_path
from .._song_utils import get_songs_data, save_songs_data
from ...image_management.pjsk_images.pjsk_db_source import PjskAlias
import json
import re


def pre_check(gid: int):
    # This function checks for global guess state, which is not server-specific as groups usually guess one at a time.
    # However, if we wanted to support concurrent guesses for different servers in the same group, we'd need to refactor pjskguess structure.
    # For now, we'll keep it as is or add pjsk_type if really needed.
    try:
        if pjskguess[GUESS_CARD][gid].get('isgoing', False):
            return '猜卡面已经开始，请等待这轮结束！(开启者发送 结束猜卡面 可以提前结束)'
    except KeyError:
        pass
    try:
        if pjskguess[GUESS_MUSIC][gid].get('isgoing', False):
            return '猜曲已经开始，请等待这轮结束！(开启者发送 结束猜曲 可以提前结束)'
    except KeyError:
        pass
    return ''


def can_be_guess_answer(answer: str) -> bool:
    answer = answer.strip().lower()
    if not answer:
        return False
    if len(answer) > 32:
        return False
    if re.fullmatch(r'[\W_]+', answer):
        return False
    if any(sep in answer for sep in ('，', ',', '。', '！', '？', '；', ';', '：', ':', '、', '\n', '\r')):
        return False
    tokens = answer.split()
    if len(tokens) > 3:
        return False
    return True


async def matchMusicGuessAnswer(answer: str, pjsk_type: int = 0) -> Optional[Tuple[int, str]]:
    data = await get_songs_data(answer, isfuzzy=False, pjsk_type=pjsk_type)
    if data['status'] == 'success':
        name = data.get('translate', '') or data.get('title', '这个')
        return data['musicId'], name
    fuzzy = await get_songs_data(answer, isfuzzy=True, pjsk_type=pjsk_type)
    if fuzzy['status'] == 'success' and fuzzy.get('match', 0) >= 0.85:
        name = fuzzy.get('translate', '') or fuzzy.get('title', '这个')
        return fuzzy['musicId'], name
    return None


async def aliasToMusicId(alias: str, pjsk_type: int = 0) -> Tuple[int, str]:
    # 首先查询本地数据库有无对应别称id
    data = await get_songs_data(alias, isfuzzy=False, pjsk_type=pjsk_type)
    # 若无结果则访问uniapi
    if data['status'] != 'success':
        # 在本地模糊搜索得到结果
        data = await get_songs_data(alias, isfuzzy=True, pjsk_type=pjsk_type)
        if data['status'] != 'success':
            return 0, ''
    name = data.get('translate', '') or data.get('title', '这个')
    return data['musicId'], name


async def aliasToCharaId(alias: str, group_id: Optional[int] = None) -> Tuple[int, str]:
    from .._utils import get_chara_alias_map
    chard2id = get_chara_alias_map()
    # Add fallback dict just in case yaml is missing
    if not chard2id:
        chard2id = {
            'ick': 1, 'saki': 2, 'hnm': 3, 'shiho': 4,
            'mnr': 5, 'hrk': 6, 'airi': 7, 'szk': 8,
            'khn': 9, 'an': 10, 'akt': 11, 'toya': 12,
            'tks': 13, 'emu': 14, 'nene': 15, 'rui': 16,
            'knd': 17, 'mfy': 18, 'ena': 19, 'mzk': 20,
            'miku': 21, 'rin': 22, 'len': 23, 'luka': 24, 'meiko': 25, 'kaito': 26
        }
    id2name = {
        17: '宵崎奏',18: '朝比奈真冬',19: '东云绘名',20: '晓山瑞希',
        9: '小豆泽心羽',10: '白石杏',11: '东云彰人',12: '青柳冬弥',
        5: '花里实乃理',6: '桐谷遥',7: '桃井爱莉',8: '日野森雫',
        1: '星乃一歌',2: '天马咲希',3: '望月穗波',4: '日野森志步',
        13: '天马司',14: '凤绘梦',15: '草薙宁宁',16: '神代类',
        21: '初音未来',22: '镜音铃',23: '镜音连',24: '巡音流歌',25: 'MEIKO',26: 'KAITO'
    }
    _id = chard2id.get(alias, 0)
    if _id == 0:
        if group_id is None:
            name = await PjskAlias.query_name(alias)
        else:
            name = await PjskAlias.query_name(alias, group_id=group_id)
        charaid = chard2id.get(name, 0)
    else:
        charaid = _id
    charaname = id2name.get(charaid, '')
    return charaid, charaname


# 其他
def defaultVocal(musicid: int, pjsk_type: int = 0) -> str:
    """
    默认vocal
    :returns: 歌曲asset名称
    """
    data = load_master_data('musicVocals.json', pjsk_type)
    assetbundleName = ''
    for vocal in data:
        if vocal['musicId'] == musicid:
            if vocal['musicVocalType'] == 'sekai' or vocal['musicVocalType'] == 'instrumental':
                return vocal['assetbundleName']
            elif vocal['musicVocalType'] == 'original_song' or vocal['musicVocalType'] == 'virtual_singer':
                assetbundleName = vocal['assetbundleName']
    return assetbundleName
