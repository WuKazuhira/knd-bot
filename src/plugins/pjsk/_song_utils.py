import datetime
import json
import re
import time
import unicodedata

import yaml

from services import logger
from utils.http_utils import AsyncHttpx

from ._autoask import pjsk_update_manager
from ._common_utils import callapi, string_similar
from ._config import MUSIC_ALIAS_API_URL, SERVER_CONFIG, SERVER_MAP, data_path
from ._models import PjskSongsAlias
from ._utils import async_load_master_data, load_master_data


# 判断歌曲是否未实装
def isleak(musicid: int, musics=None, pjsk_type: int = 0):
    if musics is None:
        musics = load_master_data('musics.json', pjsk_type)
    for i in musics:
        if i['id'] == musicid:
            # 其它服务器的时间戳逻辑暂不处理
            if int(time.time() * 1000) < i['publishedAt']:
                return True
            else:
                return False
    return True


# 歌曲定数
def getPlayLevel(musicid: int, difficulty: str, musicDifficulties=None, pjsk_type: int = 0):
    if musicDifficulties is None:
        musicDifficulties = load_master_data('musicDifficulties.json', pjsk_type)
    for diff in musicDifficulties:
        if musicid == diff['musicId'] and diff['musicDifficulty'] == difficulty:
            return diff['playLevel']


# 更新从uniapi获取的歌曲alias
async def save_songs_data(song_id: int):
    url = f'https://api.unipjsk.com/getalias2/{song_id}'
    try:
        song_list = (await AsyncHttpx.get(url)).json()
        for song in song_list:
            if await PjskSongsAlias.add_alias(
                song_id, song['alias'], 114514, 114514, datetime.datetime.now(), True
            ):
                logger.info(f"更新歌曲id:{song_id}别称({song['alias']})成功")
    except Exception as e:
        logger.warning(f"从 unipjsk 更新曲目 {song_id} 失败: {e}")

# 从Haruki同步全量歌曲别称
async def sync_haruki_music_aliases(pjsk_type: int = 0):
    if not MUSIC_ALIAS_API_URL:
        logger.warning("未配置 endpoints.music_alias_api_url，跳过外部歌曲别名同步")
        return
    musics = await async_load_master_data('musics.json', pjsk_type)
    if not musics:
        return
    logger.info(f"开始从haruki同步歌曲别名...共计 {len(musics)} 首歌")
    updated_num = 0
    from ._config import SERVER_MAP
    server_name = SERVER_MAP.get(pjsk_type, 'jp')

    async def sync_music(mid: int):
        nonlocal updated_num
        try:
            url = MUSIC_ALIAS_API_URL.format(music_id=mid)
            resp = await AsyncHttpx.get(url, timeout=10)
            data = resp.json()
            if data and 'aliases' in data:
                aliases = data['aliases']
                # 排除韩语别名
                aliases = [a for a in aliases if not any('\uac00' <= c <= '\ud7af' for c in a)]
                for alias in aliases:
                    if await PjskSongsAlias.add_alias(
                        mid, alias, 114514, 114514, datetime.datetime.now(), True
                    ):
                        updated_num += 1
                        logger.info(f"更新歌曲id:{mid} Haruki别称({alias})成功")
        except Exception:
            pass

    import asyncio
    # 按照 batch_size 限制并发数
    batch_size = 10
    for i in range(0, len(musics), batch_size):
        batch = musics[i:i + batch_size]
        await asyncio.gather(*(sync_music(m['id']) for m in batch))
        await asyncio.sleep(1)
        
    logger.info(f"从haruki同步歌曲别名完成，共计更新 {updated_num} 条数据")

    
def _load_music_title_translations(pjsk_type: int = 0):
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    trans_path = data_path / server_name / 'translate.yaml'
    if not trans_path.exists():
        return {}

    with open(trans_path, encoding='utf-8') as f:
        trans_data = yaml.load(f, Loader=yaml.FullLoader) or {}

    if not isinstance(trans_data, dict):
        logger.warning(f'[{server_name}] 翻译文件格式异常，已跳过曲名翻译: {trans_path}')
        return {}

    music_titles = trans_data.get('music_titles', {})
    if not isinstance(music_titles, dict):
        logger.warning(f'[{server_name}] 曲名翻译格式异常，已跳过曲名翻译: {trans_path}')
        return {}

    return music_titles


def _normalize_song_query(text: str) -> str:
    text = unicodedata.normalize('NFKC', str(text or '')).lower()
    return ''.join(ch for ch in text if re.match(r'[\w\u3040-\u30ff\u3400-\u9fff]', ch))


def _safe_similarity(s1: str, s2: str) -> float:
    if not s1 or not s2:
        return 0.0
    return max(0.0, string_similar(s1, s2))


def _song_match_score(query: str, candidate: str) -> float:
    query = str(query or '').strip()
    candidate = str(candidate or '').strip()
    if not query or not candidate:
        return 0.0
    q_norm = _normalize_song_query(query)
    c_norm = _normalize_song_query(candidate)
    if not q_norm or not c_norm:
        return 0.0
    if q_norm == c_norm:
        return 1.0

    raw_score = _safe_similarity(query.lower(), candidate.lower())
    norm_score = _safe_similarity(q_norm, c_norm)
    score = max(raw_score * 0.35 + norm_score * 0.65, norm_score)

    short, long = (q_norm, c_norm) if len(q_norm) <= len(c_norm) else (c_norm, q_norm)
    if short and short in long:
        contain_score = 0.72 + 0.24 * (len(short) / max(len(long), 1))
        if long.startswith(short):
            contain_score += 0.04
        score = max(score, min(contain_score, 0.98))

    return min(score, 1.0)


def _split_translations(value: str) -> list[str]:
    return [item.strip() for item in str(value or '').split('/') if item.strip()]


def _song_result(music_id: int, match: float, title: str, translate: str = '', *, candidates=None,
                 matched_alias: str = '', exact: bool = False):
    if translate == title:
        translate = ''
    return {
        'match': match,
        'musicId': music_id,
        'status': 'success' if music_id else 'false',
        'title': title,
        'translate': translate,
        'candidates': candidates or [],
        'matched_alias': matched_alias,
        'exact': exact,
    }


async def _matchname_candidates(alias: str, pjsk_type: int = 0, limit: int = 5) -> list[dict]:
    data = await async_load_master_data('musics.json', pjsk_type)
    trans = _load_music_title_translations(pjsk_type)
    music_by_id = {int(music['id']): music for music in data}
    candidates: dict[int, dict] = {}

    def add_candidate(music_id: int, name: str, source: str):
        music = music_by_id.get(int(music_id))
        if not music or not name:
            return
        score = _song_match_score(alias, name)
        if score <= 0:
            return
        old = candidates.get(int(music_id))
        if old is None or score > old['match']:
            translate = trans.get(int(music_id), '')
            candidates[int(music_id)] = _song_result(
                int(music_id), score, music['title'], translate,
                matched_alias=name, exact=False,
            ) | {'source': source}

    for music in data:
        music_id = int(music['id'])
        add_candidate(music_id, music['title'], 'title')
        for title in _split_translations(trans.get(music_id, '')):
            add_candidate(music_id, title, 'translate')

    try:
        alias_pairs = await PjskSongsAlias.query_alias_pairs()
    except Exception as e:
        logger.warning(f'读取歌曲别名用于模糊匹配失败: {e}')
        alias_pairs = []
    for music_id, song_alias in alias_pairs:
        add_candidate(music_id, song_alias, 'alias')

    result = sorted(candidates.values(), key=lambda item: item['match'], reverse=True)
    return result[:limit]


# 模糊搜索曲名的具体函数
def _matchname(alias, pjsk_type: int = 0):
    match = {'match': 0, 'musicId': 0, 'status': 'false', 'title': '', 'translate': ''}
    data = load_master_data('musics.json', pjsk_type)
    trans = _load_music_title_translations(pjsk_type)

    for musics in data:
        name = musics['title']
        similar = string_similar(alias.lower(), name.lower())
        if similar > match['match']:
            match['match'] = similar
            match['musicId'] = musics['id']
            match['title'] = musics['title']
        try:
            translate = trans[musics['id']]
            if '/' in translate:
                alltrans = translate.split('/')
                for i in alltrans:
                    similar = string_similar(alias.lower(), i.lower())
                    if similar > match['match']:
                        match['match'] = similar
                        match['musicId'] = musics['id']
                        match['title'] = musics['title']
            else:
                similar = string_similar(alias.lower(), translate.lower())
                if similar > match['match']:
                    match['match'] = similar
                    match['musicId'] = musics['id']
                    match['title'] = musics['title']
        except KeyError:
            pass
    try:
        match['translate'] = trans[match['musicId']]
        if match['translate'] == match['title']:
            match['translate'] = ''
    except KeyError:
        match['translate'] = ''
    if match['match'] > 0:
        match['status'] = 'success'
    return match


# 准确/模糊搜索曲名
async def get_songs_data(alias: str, isfuzzy: bool = False, pjsk_type: int = 0):
    alias = str(alias or '').strip()
    data = await async_load_master_data('musics.json', pjsk_type)
    music_by_id = {int(music['id']): music for music in data}
    trans = _load_music_title_translations(pjsk_type)

    def by_id(song_id: int, *, matched_alias: str = '', exact: bool = True):
        music = music_by_id.get(int(song_id))
        if not music:
            return None
        return _song_result(
            int(song_id), 1.0 if exact else 0.0, music['title'], trans.get(int(song_id), ''),
            matched_alias=matched_alias or music['title'], exact=exact,
        )

    if alias.isdigit():
        ret = by_id(int(alias), matched_alias=alias, exact=True)
        if ret:
            return ret

    sid = await PjskSongsAlias.query_sid(alias)
    if sid:
        ret = by_id(int(sid), matched_alias=alias, exact=True)
        if ret:
            return ret

    normalized_alias = _normalize_song_query(alias)
    for music_id, music in music_by_id.items():
        if _normalize_song_query(music['title']) == normalized_alias:
            return by_id(music_id, matched_alias=music['title'], exact=True)
        for title in _split_translations(trans.get(music_id, '')):
            if _normalize_song_query(title) == normalized_alias:
                return by_id(music_id, matched_alias=title, exact=True)

    if isfuzzy:
        candidates = await _matchname_candidates(alias, pjsk_type)
        if candidates:
            best = dict(candidates[0])
            best['candidates'] = candidates
            best['status'] = 'success'
            return best
    return {
        "match": 0,
        "musicId": 0,
        "status": "false",
        "title": "",
        "translate": "",
        "candidates": [],
        "matched_alias": "",
        "exact": False,
    }




# 歌曲 BPM
async def parse_bpm(music_id, pjsk_type: int = 0):
    try:
        server_name = SERVER_MAP.get(pjsk_type, 'jp')
        await pjsk_update_manager.update_assets(rf'startapp/music/music_score/{music_id:04d}_01', 'expert', pjsk_type=pjsk_type)

        with open(
            data_path / server_name / rf'startapp/music/music_score/{music_id:04d}_01/expert', encoding='utf-8'
        ) as f:
            r = f.read()
    except FileNotFoundError:
        return 0, [{'time': 0.0, 'bpm': '无数据'}], 0

    score = {}
    max_time = 0
    for line in r.split('\n'):
        match: re.Match = re.match(r'#(...)(...?)\s*\:\s*(\S*)', line)
        if match:
            time, key, value = match.groups()
            score[(time, key)] = value
            if time.isdigit():
                max_time = max(max_time, int(time) + 1)

    bpm_palette = {}
    for time, key in score:
        if time == 'BPM':
            bpm_palette[key] = float(score[(time, key)])

    bpm_events = {}
    for time, key in score:
        if time.isdigit() and key == '08':
            value = score[(time, key)]
            length = len(value) // 2

            for i in range(length):
                bpm_key = value[i * 2:(i + 1) * 2]
                if bpm_key == '00':
                    continue
                bpm = bpm_palette[bpm_key]
                t = int(time) + i / length
                bpm_events[t] = bpm

    bpm_sequence = [{
        'time': time,
        'bpm': bpm,
    } for time, bpm in sorted(bpm_events.items())]

    for i in range(len(bpm_sequence)):
        if i > 0 and bpm_sequence[i]['bpm'] == bpm_sequence[i - 1]['bpm']:
            bpm_sequence[i]['deleted'] = True

    bpm_sequence = [bpm_event for bpm_event in bpm_sequence if bpm_event.get('deleted') != True]

    bpms = {}
    for i in range(len(bpm_sequence)):
        bpm = bpm_sequence[i]['bpm']
        if bpm not in bpms:
            bpms[bpm] = 0.0

        if i + 1 < len(bpm_sequence):
            bpms[bpm] += (bpm_sequence[i + 1]['time'] - bpm_sequence[i]['time']) / bpm
        else:
            bpms[bpm] += (max_time - bpm_sequence[i]['time']) / bpm

    sorted_bpms = sorted([(bpms[bpm], bpm) for bpm in bpms], reverse=True)
    mean_bpm = sorted_bpms[0][1]

    return mean_bpm, bpm_sequence, max_time


# 歌曲标题
def idtoname(musicid, musics=None, pjsk_type: int = 0):
    if musics is None:
        musics = load_master_data('musics.json', pjsk_type)
    for i in musics:
        if i['id'] == musicid:
            return i['title']
    return ""
