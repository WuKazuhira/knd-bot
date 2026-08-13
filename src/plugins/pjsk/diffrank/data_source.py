import asyncio
import csv
import json
import os
import re
import unicodedata
from difflib import SequenceMatcher

from .._config import SERVER_MAP, data_path


def get_server_data_path(pjsk_type: int = 0):
    return data_path / SERVER_MAP.get(pjsk_type, 'jp')


def get_constants_csv_path(pjsk_type: int = 0):
    """难度排行三服共用 JP 定数文件，避免 CN/TW 定数长期不同步。"""
    return get_server_data_path(0) / 'realtime' / 'constants.csv'

# Google Sheets 表格 ID 和 Sheet ID
SHEET_ID = "1Yv3GXnCIgEIbHL72EuZ-d5q_l-auPgddWi4Efa14jq0"
SHEET_GID = "182216"
# 导出为 CSV 的 URL
SHEET_CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={SHEET_GID}"

# 第二定数源：C:G 为歌名/别名候选，H 为 MASTER 定数。
MASTER_SHEET_ID = "1rtkwNcfqQFoe8wAtD8tOVtm-u7SRGNUeX6faNi51tQI"
MASTER_SHEET_GID = "1631453602"
MASTER_SHEET_RANGE = "C2:H616"
MASTER_SHEET_ROW_START = 2
MASTER_SHEET_ROW_END = 616
MASTER_SHEET_CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{MASTER_SHEET_ID}/gviz/tq"
    f"?tqx=out:csv&gid={MASTER_SHEET_GID}&range={MASTER_SHEET_RANGE}"
)
GOOGLE_SHEET_PROXY = os.getenv("PJSK_GOOGLE_SHEET_PROXY", "").strip()
_GOOGLE_SHEET_WORKING_PROXY: str | None | bool = False
_GOOGLE_SHEET_BAD_PROXIES: set[str | None] = set()


def _google_sheet_proxy_candidates() -> list[str | None]:
    global _GOOGLE_SHEET_WORKING_PROXY
    if _GOOGLE_SHEET_WORKING_PROXY is not False:
        return [_GOOGLE_SHEET_WORKING_PROXY]

    candidates = [
        GOOGLE_SHEET_PROXY,
        os.getenv("HTTPS_PROXY", "").strip(),
        os.getenv("HTTP_PROXY", "").strip(),
        "http://host.docker.internal:7890",
        "http://127.0.0.1:7890",
    ]
    result = []
    seen = set()
    for proxy in candidates:
        if not proxy or proxy in seen or proxy in _GOOGLE_SHEET_BAD_PROXIES:
            continue
        seen.add(proxy)
        result.append(proxy)
    if None not in _GOOGLE_SHEET_BAD_PROXIES:
        result.append(None)
    return result


async def _fetch_csv_text(session, url: str) -> str | None:
    import aiohttp

    from services.log import logger

    global _GOOGLE_SHEET_WORKING_PROXY
    last_error = None
    for proxy in _google_sheet_proxy_candidates():
        try:
            async with session.get(url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=30, connect=6)) as resp:
                if resp.status == 200:
                    _GOOGLE_SHEET_WORKING_PROXY = proxy
                    return await resp.text(encoding='utf-8')
                last_error = f"HTTP {resp.status}"
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            _GOOGLE_SHEET_BAD_PROXIES.add(proxy)
            continue
    logger.warning(f"[diffrank] 下载 Google Sheets 失败: {last_error}, url={url}")
    return None


async def _fetch_master_sheet_rows_precise(session) -> str | None:
    """逐行精确拉取第二来源；用于规避 Google gviz 大范围导出 H 列旧缓存/错位。"""
    from services.log import logger

    sem = asyncio.Semaphore(12)

    async def _fetch_row(row_no: int):
        url = (
            f"https://docs.google.com/spreadsheets/d/{MASTER_SHEET_ID}/gviz/tq"
            f"?tqx=out:csv&gid={MASTER_SHEET_GID}&range=C{row_no}:H{row_no}"
        )
        async with sem:
            text = await _fetch_csv_text(session, url)
        if not text:
            return row_no, None
        try:
            row = next(csv.reader(text.splitlines()), None)
        except Exception:
            row = None
        return row_no, row

    results = await asyncio.gather(
        *(_fetch_row(row_no) for row_no in range(MASTER_SHEET_ROW_START, MASTER_SHEET_ROW_END + 1)),
        return_exceptions=True,
    )
    rows = []
    failed = 0
    for result in sorted((r for r in results if not isinstance(r, Exception)), key=lambda x: x[0]):
        _, row = result
        if row is None:
            failed += 1
            rows.append([])
        else:
            rows.append(row)
    if failed:
        logger.warning(f"[diffrank] 第二定数源逐行拉取失败 {failed} 行")
    out = []
    for row in rows:
        buf = []
        for value in row:
            buf.append(str(value))
        out.append(buf)
    import io
    s = io.StringIO()
    writer = csv.writer(s)
    writer.writerows(out)
    return s.getvalue()


def _master_sheet_range_is_stale(content: str) -> bool:
    """检测大范围导出是否拿到旧 H 列；Bad Apple 行已知应为 29.3+。"""
    for row in csv.reader((content or '').splitlines()):
        if len(row) >= 6 and _normalize_music_name(row[0]) == _normalize_music_name('Bad Apple!! feat.SEKAI'):
            return _parse_constant(row[5]) is None
    return False


def _normalize_music_name(name: str) -> str:
    """规范化歌名，用于无 songID 来源的稳定匹配。"""
    if not name:
        return ''
    text = unicodedata.normalize('NFKC', str(name)).strip().casefold()
    # 表格内可能混有谱面/版本标记，去掉这些短标记避免干扰匹配。
    text = re.sub(r'[\[\]【】()（）〈〉<>「」『』]', '', text)
    text = re.sub(r'\b(?:master|expert|hard|normal|easy|append|ma|ex|hd|nm|ez|apd)\b', '', text)
    text = re.sub(r'\b(?:ソ|敷|交|多|mv|2d|3d)\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'[\s\u3000\-‐‑‒–—―_~〜～・･,，.。!！?？:：;；/／\\]+', '', text)
    return text


def _parse_constants(value: str) -> list[float]:
    """解析 `30.5(↑)` / `30.5+` / `37.9(↑) 37.8(↑)` 这类定数字符串。"""
    text = unicodedata.normalize('NFKC', str(value or '')).strip()
    constants = []
    for match in re.finditer(r'\d+(?:\.\d+)?', text):
        try:
            constants.append(round(float(match.group(0)), 1))
        except ValueError:
            continue
    return constants


def _parse_constant(value: str) -> float | None:
    constants = _parse_constants(value)
    return constants[0] if constants else None


def _build_music_name_index(pjsk_type: int) -> tuple[dict, dict]:
    musics = []
    try:
        with open(get_server_data_path(pjsk_type) / 'musics.json', 'r', encoding='utf-8') as f:
            musics = json.load(f)
    except Exception:
        return {}, {}

    exact = {}
    titles = {}
    for music in musics:
        if not isinstance(music, dict) or music.get('id') is None:
            continue
        mid = int(music['id'])
        title = str(music.get('title') or '')
        norm = _normalize_music_name(title)
        if norm:
            exact[norm] = mid
            titles[mid] = title
        pronunciation = str(music.get('pronunciation') or '')
        norm_pron = _normalize_music_name(pronunciation)
        if norm_pron:
            exact.setdefault(norm_pron, mid)
    return exact, titles


def _find_music_ids_in_row(row_names: list[str], title_map: dict) -> list[int]:
    """在整行歌名文本中扫描本地歌曲标题，兼容一个单元格写多个歌名。"""
    combined = _normalize_music_name(' '.join(row_names))
    if not combined:
        return []
    title_items = [
        (mid, _normalize_music_name(title))
        for mid, title in title_map.items()
        if _normalize_music_name(title)
    ]
    title_items.sort(key=lambda x: len(x[1]), reverse=True)
    matches = []
    used_spans = []
    for mid, norm_title in title_items:
        pos = combined.find(norm_title)
        if pos < 0:
            continue
        span = (pos, pos + len(norm_title))
        if any(not (span[1] <= s[0] or span[0] >= s[1]) for s in used_spans):
            continue
        matches.append((pos, mid))
        used_spans.append(span)
    matches.sort(key=lambda x: x[0])
    return [mid for _, mid in matches]


def _resolve_music_id_by_names(names: list[str], exact_index: dict, title_map: dict) -> tuple[int | None, str | None]:
    """用 C:G 的候选歌名匹配本地 musics.json，返回 (music_id, matched_name)。"""
    candidates = []
    for name in names:
        norm = _normalize_music_name(name)
        if not norm:
            continue
        if norm in exact_index:
            return exact_index[norm], name
        candidates.append((name, norm))

    # 精确匹配失败时才启用高阈值模糊匹配，避免误匹配。
    best_mid = None
    best_name = None
    best_score = 0.0
    for raw_name, norm in candidates:
        for mid, title in title_map.items():
            score = SequenceMatcher(None, norm, _normalize_music_name(title)).ratio()
            if score > best_score:
                best_score = score
                best_mid = mid
                best_name = raw_name
    if best_mid is not None and best_score >= 0.92:
        return best_mid, best_name
    return None, candidates[0][0] if candidates else None


def _parse_primary_rows(content: str) -> list[tuple[int, str, float]]:
    reader = csv.reader(content.splitlines())
    rows = []
    for i, row in enumerate(reader):
        if i == 0:  # 跳过标题行
            continue
        try:
            # B=index1, C=index2, F=index5, G=index6
            if len(row) < 7:
                continue
            diff_value = row[2].strip()
            diff_type = row[5].strip()
            music_id = row[6].strip()

            if not music_id or not diff_value or not diff_type:
                continue

            rows.append((int(music_id), diff_type.lower(), float(diff_value)))
        except (ValueError, IndexError):
            continue
    return rows


def _fill_missing_master_constants(merged: dict, pjsk_type: int) -> int:
    """将缺失的 MASTER 定数兜底为官方整数 playLevel.0。"""
    try:
        with open(get_server_data_path(pjsk_type) / 'musicDifficulties.json', 'r', encoding='utf-8') as f:
            difficulties = json.load(f)
    except Exception:
        return 0

    count = 0
    for item in difficulties:
        if not isinstance(item, dict):
            continue
        if item.get('musicDifficulty') != 'master':
            continue
        music_id = item.get('musicId')
        play_level = item.get('playLevel')
        if music_id is None or play_level is None:
            continue
        key = (int(music_id), 'master')
        if key not in merged:
            merged[key] = float(play_level)
            count += 1
    return count


def _parse_master_override_rows(content: str, pjsk_type: int) -> tuple[list[tuple[int, str, float]], list[str]]:
    """解析第二来源的 MASTER 定数。CSV 范围为 C:H：前 5 列歌名候选，最后 1 列定数。"""
    from services.log import logger

    exact_index, title_map = _build_music_name_index(pjsk_type)
    if not exact_index:
        return [], []

    rows = []
    misses = []
    reader = csv.reader(content.splitlines())
    for row in reader:
        if len(row) < 6:
            continue
        name_candidates = [c.strip() for c in row[:5] if c and c.strip()]
        constants = _parse_constants(row[5])
        if not name_candidates or not constants:
            continue

        row_music_ids = _find_music_ids_in_row(name_candidates, title_map)
        if len(row_music_ids) == len(constants):
            rows.extend((mid, 'master', const) for mid, const in zip(row_music_ids, constants))
            continue
        if len(row_music_ids) == 1:
            rows.append((row_music_ids[0], 'master', constants[0]))
            continue

        music_id, matched_name = _resolve_music_id_by_names(name_candidates, exact_index, title_map)
        if music_id is None:
            misses.append(matched_name or '/'.join(name_candidates))
            continue
        rows.append((music_id, 'master', constants[0]))

    if misses:
        preview = ', '.join(misses[:20])
        logger.warning(f"[diffrank] 第二定数源有 {len(misses)} 行歌名未匹配: {preview}")
    return rows, misses


async def update_diff_from_sheet(pjsk_type: int = 0) -> bool:
    """
    从 Google Sheets 下载定数数据，保存为 realtime/constants.csv（id, difficulty, constant 三列）
    """
    import aiohttp

    from services.log import logger

    try:
        async with aiohttp.ClientSession() as session:
            primary_content, master_content = await asyncio.gather(
                _fetch_csv_text(session, SHEET_CSV_URL),
                _fetch_csv_text(session, MASTER_SHEET_CSV_URL),
            )

        rows = _parse_primary_rows(primary_content or '') if primary_content else []
        if not rows:
            logger.warning("[diffrank] 第一定数源没有获取到定数数据")

        override_rows = []
        if master_content:
            if _master_sheet_range_is_stale(master_content):
                logger.warning("[diffrank] 第二定数源大范围导出疑似旧缓存，切换为逐行精确拉取")
                async with aiohttp.ClientSession() as precise_session:
                    precise_content = await _fetch_master_sheet_rows_precise(precise_session)
                if precise_content:
                    master_content = precise_content
            override_rows, _ = _parse_master_override_rows(master_content, pjsk_type)
        else:
            logger.warning("[diffrank] 第二定数源下载失败，将只使用第一来源")

        if not rows and not override_rows:
            logger.warning("[diffrank] 两个 Google 定数源均未获取到有效数据，保留现有 constants.csv")
            return False

        merged = {(mid, diff): const for mid, diff, const in rows}
        override_count = 0
        new_count = 0
        for mid, diff, const in override_rows:
            key = (mid, diff)
            if key in merged:
                override_count += 1
            else:
                new_count += 1
            merged[key] = const
        # 最后兜底：仅在至少一个真实定数源成功时，才用官方整数等级补齐缺失 MASTER。
        fallback_count = _fill_missing_master_constants(merged, pjsk_type)
        rows = [(mid, diff, const) for (mid, diff), const in merged.items()]

        csv_path = get_constants_csv_path(pjsk_type)
        csv_path.parent.mkdir(parents=True, exist_ok=True)

        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['id', 'difficulty', 'constant'])
            writer.writerows(rows)

        logger.info(
            f"[diffrank] 更新完成，共 {len(rows)} 条定数，已写入 {csv_path}；"
            f"第二来源覆盖 MASTER {override_count} 条，新增 {new_count} 条，"
            f"整数等级兜底 {fallback_count} 条"
        )
        return True

    except Exception as e:
        from services.log import logger
        logger.warning(f"[diffrank] 下载 Google Sheets 失败: {e}")
        return False


def load_constants(pjsk_type: int = 0) -> dict:
    """
    读取 realtime/constants.csv，返回 {(musicId, difficulty): constant} 字典
    文件不存在时返回空字典
    """
    csv_path = get_constants_csv_path(pjsk_type)
    if not csv_path.exists():
        return {}
    constants = {}
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                mid = int(row['id'])
                diff = row['difficulty'].lower()
                constant = float(row['constant'])
                constants[(mid, diff)] = constant
    except Exception:
        pass
    return constants


# 定数填写系统，将难度定数做成csv格式方便填写，填写后转回json给bot用
def get_raw_diff_list(music_id: int, raw_music_difficulties=None, pjsk_type: int = 0):
    if raw_music_difficulties is None:
        path = get_server_data_path(pjsk_type) / 'musicDifficulties.json'
        with open(path, 'r', encoding='utf-8') as f:
            raw_music_difficulties = json.load(f)
    for i in range(len(raw_music_difficulties)):
        if raw_music_difficulties[i]['musicId'] == music_id:
            return [raw_music_difficulties[j]['playLevel'] for j in range(i, i + 5)]
    return None


def get_custom_diff_list(music_id: int, custom_music_difficulties=None, pjsk_type: int = 0):
    if custom_music_difficulties is None:
        path = get_server_data_path(pjsk_type) / 'realtime/musicDifficulties.json'
        if not (get_server_data_path(pjsk_type) / 'realtime/musicDifficulties.json').exists():
            return None
        with open(path, 'r', encoding='utf-8') as f:
            custom_music_difficulties = json.load(f)
    for i in range(len(custom_music_difficulties)):
        if custom_music_difficulties[i]['musicId'] == music_id:
            try:
                fullComboAdjust_list = []
                fullPerfectAdjust_list = []
                for j in range(i + 3, i + 5):
                    fullComboAdjust = custom_music_difficulties[j].get('fullComboAdjust', '')
                    fullPerfectAdjust = custom_music_difficulties[j].get('fullPerfectAdjust', '')
                    playLevel = custom_music_difficulties[j]['playLevel']

                    fullComboAdjust_list.append(fullComboAdjust + playLevel if fullComboAdjust != '' else '')
                    fullPerfectAdjust_list.append(fullPerfectAdjust + playLevel if fullPerfectAdjust != '' else '')

                return [fullComboAdjust_list, fullPerfectAdjust_list]
            except (KeyError, TypeError):
                return None
    return None


def generate_diff_csv(pjsk_type: int = 0):
    csvdata = []
    server_path = get_server_data_path(pjsk_type)
    with open(server_path / 'musics.json', 'r', encoding='utf-8') as f:
        musics = json.load(f)
    with open(server_path / 'musicDifficulties.json', 'r', encoding='utf-8') as f:
        raw_diff_data = json.load(f)
    with open(server_path / 'realtime/musicDifficulties.json', 'r', encoding='utf-8') as f:
        custom_diff_data = json.load(f)
    for music in musics:
        raw_diff = get_raw_diff_list(music['id'], raw_diff_data, pjsk_type=pjsk_type)
        custom_diff = get_custom_diff_list(music['id'], custom_diff_data, pjsk_type=pjsk_type)
        if custom_diff is not None:
            csvdata.append([music['title'], music['id'], music['publishedAt'], raw_diff[3], custom_diff[0][0], custom_diff[1][0],
                                                        raw_diff[4], custom_diff[0][1], custom_diff[1][1]])
        else:
            csvdata.append([music['title'], music['id'], music['publishedAt'], raw_diff[3], '', '',
                                                        raw_diff[4], '', ''])
    csvdata.sort(key=lambda x: x[2], reverse=True)
    realtime_path = server_path / "realtime"
    realtime_path.mkdir(parents=True, exist_ok=True)
    with open(realtime_path / "musics.csv", "w", newline='', encoding='utf-8-sig') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["曲名", "id", "time", 'EXPERT', "FC定数", "AP定数", 'MASTER', "FC定数", "AP定数"])
        writer.writerows(csvdata)


def generate_diff_json(pjsk_type: int = 0):
    server_path = get_server_data_path(pjsk_type)
    with open(server_path / 'musicDifficulties.json', 'r', encoding='utf-8') as f:
        diff_data = json.load(f)

    realtime_csv = server_path / "realtime/musics.csv"
    if not realtime_csv.exists():
        return
    with open(realtime_csv, "r", encoding='utf-8-sig') as csvfile:
        reader = csv.reader(csvfile)
        for line in reader:
            try:
                music_id = int(line[1])

                for i in range(len(diff_data)):
                    if diff_data[i]['musicId'] == music_id:
                        break

                diff_data[i + 3]['fullComboAdjust'] = float(line[4]) - int(line[3])
                diff_data[i + 3]['fullPerfectAdjust'] = float(line[5]) - int(line[3])
                diff_data[i + 3]['playLevelAdjust'] = (
                    diff_data[i + 3]['fullComboAdjust'] * 2/3 + diff_data[i + 3]['fullPerfectAdjust'] * 1/3
                )

                diff_data[i + 4]['fullComboAdjust'] = float(line[7]) - int(line[6])
                diff_data[i + 4]['fullPerfectAdjust'] = float(line[8]) - int(line[6])
                diff_data[i + 4]['playLevelAdjust'] = (
                    diff_data[i + 4]['fullComboAdjust'] * 2/3 + diff_data[i + 4]['fullPerfectAdjust'] * 1/3
                )
            except ValueError:
                pass

    realtime_json = server_path / 'realtime/musicDifficulties.json'
    realtime_json.parent.mkdir(parents=True, exist_ok=True)
    with open(realtime_json, 'w', encoding='utf-8') as f:
        f.write(json.dumps(diff_data, indent=4, ensure_ascii=False))
