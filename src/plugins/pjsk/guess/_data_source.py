import json
import random
import time
from pathlib import Path
from typing import Tuple, Union

from mutagen.mp3 import MP3
from pydub import AudioSegment

from config.path_config import TEMP_PATH

from .._autoask import pjsk_update_manager
from .._config import SERVER_MAP, data_path
from .._song_utils import getPlayLevel
from .._utils import async_load_master_data, load_master_data
from ._config import SEdir
from ._utils import defaultVocal


# 指定资源
async def getMusic(musicid: int = 0, pjsk_type: int = 0) -> str:
    """
    获取指定曲目mp3
    :param musicid: 歌曲id
    :param pjsk_type: 服务器类型
    :returns: asset名称
    """
    asset = defaultVocal(musicid, pjsk_type=pjsk_type)
    path = f'ondemand/music/long/{asset}'
    file = f'{asset}.mp3'
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    if not (data_path / server_name / path / file).exists():
        await pjsk_update_manager.get_asset(path, file, pjsk_type=pjsk_type)
    return asset


async def getJacket(musicid: int = 0, pjsk_type: int = 0) -> str:
    """
    获取随机曲绘
    :param musicid: 歌曲id
    :param pjsk_type: 服务器类型
    :returns: asset名称
    """
    musicdata = await async_load_master_data('musics.json', pjsk_type)
    for music in musicdata:
        if music['id'] == musicid:
            asset = music['assetbundleName']
            path = f'startapp/music/jacket/{asset}'
            file = f'{asset}.png'
            server_name = SERVER_MAP.get(pjsk_type, 'jp')
            if not (data_path / server_name / path / file).exists():
                await pjsk_update_manager.get_asset(path, file, pjsk_type=pjsk_type)
            return asset
    else:
        return ''


async def getCard(charaid: int = 0, cardid: int = 0, pjsk_type: int = 0) -> Tuple[str, str]:
    """
    获取随机卡面
    :param charaid: 人物id，优先级更高
    :param cardid: 卡面id
    :param pjsk_type: 服务器类型
    :returns: 元组形式(卡面asset名称, 卡面稀有度)
    """
    assetbundleName, cardRarityType = '', ''
    cardsdata = await async_load_master_data('cards.json', pjsk_type)
    if charaid != 0:
        cardsdata = list(filter(lambda x:x['characterId'] == charaid, cardsdata))
        length = len(cardsdata)
        rannum = random.randint(0, length - 1)
        while (
            cardsdata[rannum]['releaseAt'] > int(time.time() * 1000)
            or cardsdata[rannum]['cardRarityType'] == 'rarity_1'
            or cardsdata[rannum]['cardRarityType'] == 'rarity_2'
        ):
            rannum = random.randint(0, length - 1)
        card = cardsdata[rannum]
        assetbundleName = card['assetbundleName']
        cardRarityType = card['cardRarityType']
    elif cardid != 0:
        for card_ in cardsdata:
            if card_['id'] == cardid:
                card = card_
                assetbundleName = card['assetbundleName']
                cardRarityType = card['cardRarityType']
                break
    if assetbundleName and cardRarityType:
        carddir = f'startapp/character/member/{assetbundleName}'
        cardfiles = ['card_normal.png'] if cardRarityType == 'rarity_birthday' else ['card_normal.png', 'card_after_training.png']
        server_name = SERVER_MAP.get(pjsk_type, 'jp')
        for cardfile in cardfiles:
            if not (data_path / server_name / carddir / cardfile).exists():
                await pjsk_update_manager.get_asset(carddir, cardfile, pjsk_type=pjsk_type)
    return assetbundleName, cardRarityType


# 随机函数
async def getRandomChart(pjsk_type: int = 0) -> Tuple[int, str]:
    """
    获取随机master谱面的musicId
    :returns: 元组形式(曲目id, 曲目名称,)
    """
    musicdata = await async_load_master_data('musics.json', pjsk_type)
    length = len(musicdata)
    rannum = random.randint(0, length - 1)
    while (
        musicdata[rannum]['publishedAt'] > int(time.time() * 1000)
    ):
        rannum = random.randint(0, length - 1)
    musicid = musicdata[rannum]['id']
    musicname = musicdata[rannum]['title']
    # 谱面底图由绘图服务在 guess_chart 任务里按需生成
    return musicid, musicname


async def getRandomJacket(pjsk_type: int = 0) -> Tuple[int, str, str]:
    """
    获取随机曲绘
    :returns: 元组形式(曲目id, 曲目名称, 曲绘asset名称)
    """
    musicdata = await async_load_master_data('musics.json', pjsk_type)
    length = len(musicdata)
    rannum = random.randint(0, length - 1)
    while (
        musicdata[rannum]['publishedAt'] > int(time.time() * 1000)
    ):
        rannum = random.randint(0, length - 1)
    musicid = musicdata[rannum]['id']
    musicname = musicdata[rannum]['title']
    asset = musicdata[rannum]['assetbundleName']
    path = f'startapp/music/jacket/{asset}'
    file = f'{asset}.png'
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    if not (data_path / server_name / path / file).exists():
        await pjsk_update_manager.get_asset(path, file, pjsk_type=pjsk_type)
    return musicid, musicname, asset


async def getRandomCard(pjsk_type: int = 0) -> Tuple[int, int, str, str, str, str]:
    """
    获取随机卡面
    :returns: 元组形式(卡面id, 卡面角色id, 卡面asset名称, 卡面名称, 角色名称， 卡面稀有度)
    """
    cardsdata = await async_load_master_data('cards.json', pjsk_type)
    length = len(cardsdata)
    rannum = random.randint(0, length - 1)
    while (
        cardsdata[rannum]['releaseAt'] > int(time.time() * 1000)
        or cardsdata[rannum]['cardRarityType'] == 'rarity_1'
        or cardsdata[rannum]['cardRarityType'] == 'rarity_2'
    ):
        rannum = random.randint(0, length - 1)
    cardid = cardsdata[rannum]['id']
    charaid = cardsdata[rannum]['characterId']
    charaname = {
        17: '宵崎奏',18: '朝比奈真冬',19: '东云绘名',20: '晓山瑞希',
        9: '小豆泽心羽',10: '白石杏',11: '东云彰人',12: '青柳冬弥',
        5: '花里实乃理',6: '桐谷遥',7: '桃井爱莉',8: '日野森雫',
        1: '星乃一歌',2: '天马咲希',3: '望月穗波',4: '日野森志步',
        13: '天马司',14: '凤绘梦',15: '草薙宁宁',16: '神代类',
        21: '初音未来',22: '镜音铃',23: '镜音连',24: '巡音流歌',25: 'MEIKO',26: 'KAITO'
    }.get(charaid, '')
    assetbundleName = cardsdata[rannum]['assetbundleName']
    prefix = cardsdata[rannum]['prefix']
    cardRarityType = cardsdata[rannum]['cardRarityType']
    carddir = f'startapp/character/member/{assetbundleName}'
    cardfiles = ['card_normal.png'] if cardRarityType == 'rarity_birthday' else ['card_normal.png', 'card_after_training.png']
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    for cardfile in cardfiles:
        if not (data_path / server_name / carddir / cardfile).exists():
            await pjsk_update_manager.get_asset(carddir, cardfile, pjsk_type=pjsk_type)
    return cardid, charaid, assetbundleName, charaname, prefix, cardRarityType


async def getRandomMusic(pjsk_type: int = 0) -> Tuple[int, str, str]:
    """
    获取随机曲目mp3
    :returns: 元组形式(曲目id, 曲目名称, 曲目mp3 asset名称)
    """
    musicdata = await async_load_master_data('musics.json', pjsk_type)
    length = len(musicdata)
    rannum = random.randint(0, len(musicdata) - 1)
    while (
        musicdata[rannum]['publishedAt'] > int(time.time() * 1000)
    ):
        rannum = random.randint(0, length - 1)

    musicid = musicdata[rannum]['id']
    musicname = musicdata[rannum]['title']
    asset = defaultVocal(musicid, pjsk_type=pjsk_type)
    path = f'ondemand/music/long/{asset}'
    file = f'{asset}.mp3'
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    if not (data_path / server_name / path / file).exists():
        await pjsk_update_manager.get_asset(path, file, pjsk_type=pjsk_type)
    return musicid, musicname, asset


def getRandomLyrics(pjsk_type: int = 0) -> Tuple[int, str, str]:
    """
    获取具有歌词文件的随机曲目
    :returns: 元组形式(曲目id, 曲目名称, 曲绘asset)
    """
    data = load_master_data('musics.json', pjsk_type)
    while True:
        item = random.choice(data)
        musicid = item.get('id')
        server_name = SERVER_MAP.get(pjsk_type, 'jp')
        lyrics_path = data_path / server_name / 'lyrics'
        if not lyrics_path.glob('*'):
            break
        lyrics_path = lyrics_path / f'{musicid}.txt'
        if lyrics_path.exists():
            musicname = item['title']
            asset = item['assetbundleName']
            return musicid, musicname, asset
    return 0, "", ""


def update_se(musicid: int):
    """
    更新谱面SE音效文件
    """

    pass


async def getRandomSE(pjsk_type: int = 0) -> Tuple[int, str]:
    """
    获取随机谱面音效musicid
    :returns: 元组形式(曲目id, 曲目名称)
    """
    musicdata = await async_load_master_data('musics.json', pjsk_type)
    musicDifficulties = await async_load_master_data('musicDifficulties.json', pjsk_type)
    length = len(musicdata)
    rannum = random.randint(0, len(musicdata) - 1)
    while (
        musicdata[rannum]['releaseAt'] > int(time.time() * 1000)
        and getPlayLevel(musicdata[rannum]['id'], 'master', musicDifficulties) < 29
    ):
        rannum = random.randint(0, length - 1)
    musicid = musicdata[rannum]['id']
    musicname = musicdata[rannum]['title']
    if not (SEdir / f'{musicid}.mp3').exists():
        update_se(musicid)
    vocal = defaultVocal(musicid, pjsk_type=pjsk_type)
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    musicpath = data_path / server_name / f'ondemand/music/long/{vocal}/{vocal}.mp3'
    if not musicpath.exists():
        await pjsk_update_manager.get_asset(f'ondemand/music/long/{vocal}', f'{vocal}.mp3', pjsk_type=pjsk_type)
    return musicid, musicname


# 裁剪函数
def cutMusic(
    assetbundleName: str, qunnum: int, cutlen: float = 1.7,
    reverse: bool = False, is_tip: bool = False, pjsk_type: int = 0
) -> Union[Tuple[Path, Path], Path]:
    """
    裁剪歌曲mp3
    """
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    path = data_path / server_name / 'ondemand/music/long'
    musicpath = path / f'{assetbundleName}/{assetbundleName}.mp3'
    length = MP3(musicpath).info.length
    music = AudioSegment.from_mp3(musicpath)
    music = music[8000:]
    starttime = random.randint(10, int(length) - 10)
    cut = music[starttime * 1000: starttime * 1000 + cutlen * 1000]
    if reverse:
        cut = cut.reverse()
    if is_tip:
        cut.export(TEMP_PATH / f"music_{qunnum}_tip.mp3", format="mp3")
        return TEMP_PATH / f"music_{qunnum}_tip.mp3"
    else:
        music.export(TEMP_PATH / f"music_{qunnum}_end.mp3")
        cut.export(TEMP_PATH / f"music_{qunnum}.mp3", format="mp3")
        return TEMP_PATH / f"music_{qunnum}.mp3", TEMP_PATH / f"music_{qunnum}_end.mp3"


async def cutSE(
    musicid: int, qunnum: int,
    is_tip: bool = False, pjsk_type: int = 0
) -> Tuple[Path, Path]:
    """
    裁剪歌曲音效mp3
    """
    musicpath = SEdir / f'{musicid}.mp3'
    length = MP3(musicpath).info.length
    se = AudioSegment.from_mp3(musicpath)
    starttime = random.randint(2, int(length) - 30)
    cut = se[starttime * 1000: starttime * 1000 + 20000]
    cut.export(TEMP_PATH / f"music_{qunnum}.mp3", format="mp3", bitrate="96k")

    musics = await async_load_master_data('musics.json', pjsk_type)
    for musicdata in musics:
        if musicdata['id'] == musicid:
            break
    vocal = defaultVocal(musicid, pjsk_type=pjsk_type)
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    musicpath = data_path / server_name / f'ondemand/music/long/{vocal}/{vocal}.mp3'
    if not musicpath.exists():
        await pjsk_update_manager.get_asset(f'ondemand/music/long/{vocal}', f'{vocal}.mp3', pjsk_type=pjsk_type)
    music = AudioSegment.from_mp3(musicpath).apply_gain(-3)
    cut2 = music[starttime * 1000 + musicdata['fillerSec'] * 1000: starttime * 1000 + 20000 + musicdata['fillerSec'] * 1000]
    mix = cut.overlay(cut2)
    mix.export(TEMP_PATH / f"music_{qunnum}_mix.mp3", format="mp3", bitrate="96k")
    return TEMP_PATH / f"music_{qunnum}.mp3", TEMP_PATH / f"music_{qunnum}_end.mp3"
