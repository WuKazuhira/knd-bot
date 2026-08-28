"""
组卡参数解析模块。
从用户输入中提取各种组卡选项，构建发送给后端的 options 字典。
"""
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .._config import (
    DECK_DEFAULT_MUSIC_CHALLENGE,
    DECK_DEFAULT_MUSIC_EVENT_AUTO,
    DECK_DEFAULT_MUSIC_EVENT_MULTI,
    DECK_DEFAULT_MUSIC_EVENT_SOLO,
    DECK_RECOMMEND_DEFAULT_ALGS,
    DECK_RECOMMEND_TIMEOUT,
    DECK_RECOMMEND_TIMEOUT_BONUS,
    DECK_RECOMMEND_TIMEOUT_NO_EVENT,
    DECK_RECOMMEND_TIMEOUT_SINGLE_ALG,
    DECK_RETURN_NUM_BONUS,
    DECK_RETURN_NUM_CHALLENGE,
    DECK_RETURN_NUM_MULTI,
)
from .._song_utils import get_songs_data
from .._utils import async_load_master_data, get_chara_alias_map, load_master_data

# 关键词定义

POWER_TARGET_KEYWORDS = ('综合力', '综合', '总合力', '总和', 'power')
SKILL_TARGET_KEYWORDS = ('倍率', '实效', 'skill', '时效')

SKILL_MAX_KEYWORDS = ("满技能", "满技", "skillmax", "技能满级", "slv4")
MASTER_MAX_KEYWORDS = ("满突破", "满破", "rankmax", "mastermax", "5破", "五破")
EPISODE_READ_KEYWORDS = ("剧情已读", "满剧情", "前后篇已读", "前后篇", "已读")
DISABLE_KEYWORDS = ("禁用", "disable")

UNIT_FILTER_KEYWORDS = {
    "light_sound": ["纯ln", "仅ln"],
    "idol": ["纯mmj", "仅mmj"],
    "street": ["纯vbs", "仅vbs"],
    "theme_park": ["纯ws", "仅ws"],
    "school_refusal": ["纯25h", "纯25时", "纯25", "仅25h", "仅25时", "仅25"],
    "piapro": ["纯vs", "纯v", "仅vs", "仅v"],
}

ATTR_FILTER_KEYWORDS = {
    "cool": ["纯蓝", "仅蓝", "纯cool", "仅cool"],
    "cute": ["纯粉", "仅粉", "纯橙", "仅橙", "纯cute", "仅cute"],
    "happy": ["纯橘", "仅橘", "纯黄", "仅黄", "纯happy", "仅happy"],
    "mysterious": ["纯紫", "仅紫", "纯mysterious", "仅mysterious"],
    "pure": ["纯绿", "仅绿", "纯pure", "仅pure"],
}

UNIT_KEYWORDS = {
    "light_sound": ["ln", "leo", "leoneed"],
    "idol": ["mmj", "moremorejump"],
    "street": ["vbs", "vivid"],
    "theme_park": ["ws", "wonderlands"],
    "school_refusal": ["25h", "25ji", "25时", "25"],
    "piapro": ["vs", "virtual"],
}

ATTR_KEYWORDS = {
    "cool": ["蓝", "cool"],
    "cute": ["粉", "橙", "cute"],
    "happy": ["橘", "黄", "happy"],
    "mysterious": ["紫", "mysterious"],
    "pure": ["绿", "pure"],
}

BOOST_BONUS_DICT = {
    0: 1, 1: 5, 2: 10, 3: 15, 4: 20,
    5: 25, 6: 27, 7: 29, 8: 31, 9: 33, 10: 35,
}

BOOST_KEYWORDS = ('boost', '火', '体力', '体')
AREA_ITEM_KEYWORDS = ('区域道具', '道具', 'areaitem')

MAX_PROFILE_KEYWORDS = ('顶配', '满配')
SUB_MAX_PROFILE_KEYWORDS = ('次顶配', '次满配', '中配')
CURRENT_DECK_KEYWORDS = ('当前', '目前')

CANVAS_KEYWORDS = ("满画布", "全画布", "画布", "满画板", "全画板", "画板")
TEAMMATE_POWER_KEYWORDS = ("队友综合力", "队友总合力", "队友综合", "队友总和")
TEAMMATE_SCOREUP_KEYWORDS = ("队友实效", "队友技能", "队友时效")
KEEP_AFTERTRAINING_STATE_KEYWORDS = ("bfes不变", "bf不变")

MUSIC_COMPARE_KEYWORDS = ('歌曲比较', '歌曲排行', '歌曲排名', '歌曲推荐', '歌曲对比')
WAR_PREPARE_KEYWORDS = ('战备', '备战')

MAX_KEYWORDS = ('最高', '最大', '最优', '最强', '最佳')
MIN_KEYWORDS = ('最低', '最小', '最差', '最弱', '最烂')
AVG_KEYWORDS = ('平均', '均值', '期望')

SKILL_ORDER_KEYWORDS = ('技能顺序', '技能排列')
SKILL_REF_KEYWORDS = ('技能抽取', '技能吸取')

DEFAULT_TEAMMATE_POWER = 250000
DEFAULT_TEAMMATE_SCOREUP = 200

OMAKASE_MUSIC_ID = 10000

# 难度名称词表（按词长降序匹配，避免短词误吃歌名）
DIFF_NAMES = [
    ("easy", ["easy", "ez"]),
    ("normal", ["normal", "nm"]),
    ("hard", ["hard", "hd"]),
    ("expert", ["expert", "exp", "ex"]),
    ("master", ["master", "mas", "ma"]),
    ("append", ["append", "apd", "ap"]),
]

DEFAULT_CHARA_ALIAS_MAP: Dict[str, int] = {
    'ick': 1, 'ichika': 1,
    'saki': 2,
    'hnm': 3, 'honami': 3,
    'shiho': 4,
    'mnr': 5, 'minori': 5,
    'hrk': 6, 'haruka': 6,
    'airi': 7,
    'szk': 8, 'shizuku': 8,
    'khn': 9, 'kohane': 9,
    'an': 10,
    'akt': 11, 'akito': 11,
    'toya': 12,
    'tks': 13,
    'emu': 14,
    'nene': 15,
    'rui': 16,
    'knd': 17, 'kanade': 17,
    'mfy': 18, 'mafuyu': 18,
    'ena': 19,
    'mzk': 20, 'mizuki': 20,
    'miku': 21,
    'rin': 22,
    'len': 23,
    'luka': 24,
    'meiko': 25,
    'kaito': 26,
}


def _get_deck_chara_alias_map() -> Dict[str, int]:
    """获取组卡用角色别名表；Docker 镜像不包含 data/ 时使用内置短名兜底。"""
    alias_map = dict(DEFAULT_CHARA_ALIAS_MAP)
    try:
        alias_map.update(get_chara_alias_map() or {})
    except Exception:
        pass
    return alias_map


def extract_diff(text: str, default: Optional[str] = None) -> Tuple[Optional[str], str]:
    """从文本中按分词精确提取难度，避免子串误吃歌名。

    返回 (难度, 剩余文本)。仅在难度词作为独立 token 出现时才剥离。
    """
    # 收集所有 (难度, 别名)，按别名长度降序，优先匹配长别名
    all_names: List[Tuple[str, str]] = []
    for diff, names in DIFF_NAMES:
        for name in names:
            all_names.append((diff, name))
    all_names.sort(key=lambda x: len(x[1]), reverse=True)

    tokens = text.split()
    for diff, name in all_names:
        name_lower = name.lower()
        for idx, token in enumerate(tokens):
            if token.lower() == name_lower:
                # 整词匹配：直接移除该 token
                remaining = tokens[:idx] + tokens[idx + 1:]
                return diff, " ".join(remaining).strip()
    return default, text.strip()


# 默认卡牌配置

def _default_card_config_12():
    return {
        'disable': False,
        'level_max': True,
        'episode_read': True,
        'master_max': True,
        'skill_max': True,
        'canvas': False,
    }

def _default_card_config_34bd():
    return {
        'disable': False,
        'level_max': True,
        'episode_read': False,
        'master_max': False,
        'skill_max': False,
        'canvas': False,
    }

def _nochange_card_config():
    return {
        'disable': False,
        'level_max': False,
        'episode_read': False,
        'master_max': False,
        'skill_max': False,
        'canvas': False,
    }


def parse_large_number(s: str) -> int:
    """解析大数字，支持 k/w/e/万/百万/亿 等单位"""
    s = s.replace(',', '').replace('_', '').replace('.', '')
    multiplier = 1
    if s.endswith('k') or s.endswith('K'):
        multiplier = 1000
        s = s[:-1]
    elif s.endswith('w') or s.endswith('W') or s.endswith('万'):
        multiplier = 10000
        s = s.rstrip('wW万')
    elif s.endswith('e') or s.endswith('E') or s.endswith('百万'):
        multiplier = 1000000
        s = s.rstrip('eE百万')
    elif s.endswith('亿'):
        multiplier = 100000000
        s = s[:-1]
    return int(float(s) * multiplier)


# 参数提取函数

def extract_live_type(args: str, options: dict) -> str:
    """提取 live 类型"""
    if "多人" in args or '协力' in args:
        options['live_type'] = "multi"
        args = args.replace("多人", "").replace("协力", "").strip()
    elif "单人" in args:
        options['live_type'] = "solo"
        args = args.replace("单人", "").strip()
    elif "自动" in args or "auto" in args:
        options['live_type'] = "auto"
        args = args.replace("自动", "").replace("auto", "").strip()
    else:
        options['live_type'] = "multi"
    return args.strip()


def extract_target(args: str, options: dict) -> str:
    """提取组卡目标（分数/综合力/实效）"""
    options['target'] = "score"
    for keyword in POWER_TARGET_KEYWORDS:
        if keyword in args:
            args = args.replace(keyword, "").strip()
            options['target'] = "power"
            break
    for keyword in SKILL_TARGET_KEYWORDS:
        if keyword in args:
            args = args.replace(keyword, "").strip()
            options['target'] = "skill"
            break
    return args.strip()


def extract_unit(args: str) -> Tuple[Optional[str], str]:
    """从参数中提取团名"""
    for unit, keywords in UNIT_KEYWORDS.items():
        for kw in sorted(keywords, key=len, reverse=True):
            if kw in args:
                return unit, args.replace(kw, "", 1).strip()
    return None, args


def extract_attr(args: str) -> Tuple[Optional[str], str]:
    """从参数中提取属性"""
    for attr, keywords in ATTR_KEYWORDS.items():
        for kw in sorted(keywords, key=len, reverse=True):
            if kw in args:
                return attr, args.replace(kw, "", 1).strip()
    return None, args


def extract_card_config(args: str, options: dict, default_nochange: bool = False) -> str:
    """提取卡牌配置"""
    if default_nochange:
        for rarity in ['rarity_1_config', 'rarity_2_config', 'rarity_3_config', 'rarity_4_config', 'rarity_birthday_config']:
            options[rarity] = _nochange_card_config()
    else:
        options['rarity_1_config'] = _default_card_config_12()
        options['rarity_2_config'] = _default_card_config_12()
        options['rarity_3_config'] = _default_card_config_34bd()
        options['rarity_4_config'] = _default_card_config_34bd()
        options['rarity_birthday_config'] = _default_card_config_34bd()

    all_configs = [
        options['rarity_1_config'],
        options['rarity_2_config'],
        options['rarity_3_config'],
        options['rarity_4_config'],
        options['rarity_birthday_config'],
    ]

    for keyword in SKILL_MAX_KEYWORDS:
        if keyword in args:
            for cfg in all_configs:
                cfg['skill_max'] = True
            args = args.replace(keyword, "").strip()
            break

    for keyword in MASTER_MAX_KEYWORDS:
        if keyword in args:
            for cfg in all_configs:
                cfg['master_max'] = True
            args = args.replace(keyword, "").strip()
            break

    for keyword in EPISODE_READ_KEYWORDS:
        if keyword in args:
            for cfg in all_configs:
                cfg['episode_read'] = True
            args = args.replace(keyword, "").strip()
            break

    for keyword in CANVAS_KEYWORDS:
        if keyword in args:
            for cfg in all_configs:
                cfg['canvas'] = True
            args = args.replace(keyword, "").strip()
            break

    for keyword in DISABLE_KEYWORDS:
        if keyword in args:
            for cfg in all_configs:
                cfg['disable'] = True
            args = args.replace(keyword, "").strip()
            break

    return args.strip()


def extract_fixed_cards(args: str, options: dict) -> str:
    """提取固定卡牌或固定角色。

    支持两种格式（# 必须放在参数最后）：
    - #123 456     固定卡牌ID
    - #mnr miku    固定角色（角色昵称），第一个角色强制作为队长
    """
    args = args.replace('＃', '#')
    if '#' not in args:
        return args.strip()

    args, fixed_args = args.split('#', 1)
    fixed_args = fixed_args.strip()
    if not fixed_args:
        return args.strip()

    segs = fixed_args.split()

    # 优先尝试全部解析为卡牌ID
    try:
        fixed_cards = list(map(int, segs))
        if fixed_cards:
            options['fixed_cards'] = fixed_cards[:5]
        return args.strip()
    except ValueError:
        pass

    # 否则按角色昵称解析为固定角色
    alias_map = _get_deck_chara_alias_map()
    fixed_characters: List[int] = []
    for seg in segs:
        cid = alias_map.get(seg.lower())
        if cid is not None and cid not in fixed_characters:
            fixed_characters.append(cid)
    if fixed_characters:
        fixed_characters = fixed_characters[:5]
        options['fixed_characters'] = fixed_characters
        options['forced_leader_character_id'] = fixed_characters[0]

    return args.strip()


def extract_additional_options(args: str) -> Tuple[dict, str]:
    """提取额外选项（火数、过滤等）"""
    ret = {}

    # 火数
    for boost in reversed(BOOST_BONUS_DICT.keys()):
        for keyword in BOOST_KEYWORDS:
            kw = f"{boost}{keyword}"
            if kw in args:
                ret['boost'] = boost
                args = args.replace(kw, "", 1).strip()
                break

    # 团过滤
    for unit, keywords in UNIT_FILTER_KEYWORDS.items():
        for keyword in keywords:
            if keyword in args:
                ret['unit_filter'] = unit
                args = args.replace(keyword, "", 1).strip()
                break

    # 属性过滤
    for attr, keywords in ATTR_FILTER_KEYWORDS.items():
        for keyword in keywords:
            if keyword in args:
                ret['attr_filter'] = attr
                args = args.replace(keyword, "", 1).strip()
                break

    # 顶配/次顶配
    for keyword in SUB_MAX_PROFILE_KEYWORDS:
        if keyword in args:
            ret['sub_max_profile'] = True
            args = args.replace(keyword, "", 1).strip()
            break

    for keyword in MAX_PROFILE_KEYWORDS:
        if keyword in args:
            ret['max_profile'] = True
            args = args.replace(keyword, "", 1).strip()
            break

    # 当前卡组
    for keyword in CURRENT_DECK_KEYWORDS:
        if keyword in args:
            ret['use_current_deck'] = True
            args = args.replace(keyword, "", 1).strip()
            break

    # 排除卡牌
    ret['excluded_cards'] = []
    segs = args.split()
    for seg in segs:
        if len(seg) > 1 and seg[0] == '-' and seg[1:].isdigit():
            try:
                x = int(seg[1:])
                if 0 < x < 5000:
                    ret['excluded_cards'].append(x)
                    args = args.replace(seg, "", 1).strip()
            except ValueError:
                pass

    # 队友综合力
    for keyword in TEAMMATE_POWER_KEYWORDS:
        if keyword in args:
            match = re.search(rf'{re.escape(keyword)}\s*(\d+)', args)
            if match:
                ret['teammate_power'] = int(match.group(1))
                args = args.replace(match.group(0), "", 1).strip()
            break

    # 队友实效
    for keyword in TEAMMATE_SCOREUP_KEYWORDS:
        if keyword in args:
            match = re.search(rf'{re.escape(keyword)}\s*(\d+)', args)
            if match:
                ret['teammate_scoreup'] = int(match.group(1))
                args = args.replace(match.group(0), "", 1).strip()
            break

    # BloomFes不变
    for keyword in KEEP_AFTERTRAINING_STATE_KEYWORDS:
        if keyword in args:
            ret['keep_after_training_state'] = True
            args = args.replace(keyword, "", 1).strip()
            break

    # 区域道具等级
    for keyword in AREA_ITEM_KEYWORDS:
        match = re.search(rf'{re.escape(keyword)}\s*(\d+)', args)
        if match:
            ret['area_item_level'] = int(match.group(1))
            args = args.replace(match.group(0), "", 1).strip()
            break

    # 实效下限
    match = re.search(r'实效[>=≥]\s*(\d+)', args)
    if match:
        ret['multi_live_score_up_lower_bound'] = int(match.group(1))
        args = args.replace(match.group(0), "", 1).strip()

    # 战备计算
    for keyword in WAR_PREPARE_KEYWORDS:
        if keyword in args:
            ret['war_prepare'] = True
            args = args.replace(keyword, "", 1).strip()
            break

    # 周回数
    round_match = re.search(r'(\d+(?:\.\d+)?)周回', args)
    if round_match:
        ret['rounds_per_hour'] = float(round_match.group(1))
        args = args.replace(round_match.group(0), "", 1).strip()

    # 当前PT和目标PT
    pt_patterns = [
        ('current_pt', r'(?:现在|今|现)\s*([0-9][0-9,._]*(?:\.\d+)?(?:k|w|e|万|百万|亿)?)'),
        ('target_pt', r'目标\s*([0-9][0-9,._]*(?:\.\d+)?(?:k|w|e|万|百万|亿)?)'),
    ]
    for key, pattern in pt_patterns:
        match = re.search(pattern, args)
        if match:
            try:
                ret[key] = parse_large_number(match.group(1))
                args = args.replace(match.group(0), "", 1).strip()
            except Exception:
                pass

    return ret, args.strip()


def _music_has_diff(music_id: int, diff: str, pjsk_type: int = 0) -> bool:
    """校验歌曲是否拥有指定难度。"""
    if not diff:
        return True
    try:
        diffs = load_master_data('musicDifficulties.json', pjsk_type)
    except Exception:
        return True
    for d in diffs:
        if isinstance(d, dict) and d.get('musicId') == music_id and d.get('musicDifficulty') == diff:
            return True
    return False


def _resolve_nidx_music(idx: int, pjsk_type: int = 0) -> Optional[int]:
    """按负数索引返回歌曲ID（-1 为最新已实装曲）。"""
    if idx >= 0:
        return None
    try:
        musics = load_master_data('musics.json', pjsk_type)
    except Exception:
        return None
    now_ms = int(time.time() * 1000)
    published = [
        m for m in musics
        if isinstance(m, dict) and m.get('id') and m.get('publishedAt', 0) <= now_ms
    ]
    if not published:
        return None
    published.sort(key=lambda m: m.get('publishedAt', 0))
    if -idx > len(published):
        return None
    return published[idx]['id']


async def extract_music(args: str, options: dict, pjsk_type: int = 0) -> str:
    """提取歌曲ID和难度。

    匹配顺序：难度(精确分词) → 数字ID → 负数索引 → 精确别名 → 模糊匹配。
    若指定了难度但歌曲没有该难度，记录提示但不崩溃。
    """
    # 1. 提取难度（精确分词，避免误吃歌名）
    diff, args = extract_diff(args, default=None)
    if diff:
        options['music_diff'] = diff

    args = args.strip()
    musics = await async_load_master_data('musics.json', pjsk_type)
    valid_ids = {m['id'] for m in musics if isinstance(m, dict) and 'id' in m}

    # 2. 尝试数字ID（仅当该数字是有效歌曲ID时）
    if 'music_id' not in options and args:
        music_id_match = re.search(r'\b(\d{1,4})\b', args)
        if music_id_match:
            potential_id = int(music_id_match.group(1))
            if potential_id in valid_ids:
                options['music_id'] = potential_id
                args = args.replace(music_id_match.group(0), "", 1).strip()

    # 3. 尝试负数索引（-1=最新曲）
    if 'music_id' not in options and args:
        nidx_match = re.fullmatch(r'-\d{1,4}', args.strip())
        if nidx_match:
            mid = _resolve_nidx_music(int(args.strip()), pjsk_type)
            if mid:
                options['music_id'] = mid
                args = ''

    # 4. 精确别名匹配
    if 'music_id' not in options and args:
        result = await get_songs_data(args, isfuzzy=False, pjsk_type=pjsk_type)
        if result.get('status') == 'success' and result.get('musicId'):
            options['music_id'] = result['musicId']
            args = ''

    # 5. 模糊匹配（带相似度阈值，避免乱匹配）
    if 'music_id' not in options and args:
        result = await get_songs_data(args, isfuzzy=True, pjsk_type=pjsk_type)
        if result.get('status') == 'success' and result.get('musicId') and result.get('match', 0) >= 0.5:
            options['music_id'] = result['musicId']
            args = ''

    # 6. 难度校验
    if options.get('music_id') and options.get('music_id') != OMAKASE_MUSIC_ID and options.get('music_diff'):
        if not _music_has_diff(options['music_id'], options['music_diff'], pjsk_type):
            options['music_diff_missing'] = options['music_diff']

    # 默认值
    if 'music_id' not in options:
        options['music_id'] = OMAKASE_MUSIC_ID
    if 'music_diff' not in options:
        options['music_diff'] = 'master'

    return args.strip()


def extract_multilive_options(args: str, options: dict) -> str:
    """提取多人live相关设置"""
    if options.get('live_type') != 'multi':
        return args.strip()

    options['multi_live_teammate_power'] = DEFAULT_TEAMMATE_POWER
    options['multi_live_teammate_score_up'] = DEFAULT_TEAMMATE_SCOREUP

    return args.strip()


def extract_skill_strategy(args: str, options: dict) -> str:
    """提取技能顺序和技能吸取策略"""
    # 技能顺序策略
    for keyword in SKILL_ORDER_KEYWORDS:
        if keyword in args:
            for kw in MAX_KEYWORDS:
                if kw in args:
                    options['skill_order_choose_strategy'] = 'max'
                    args = args.replace(kw, "", 1).strip()
                    break
            for kw in MIN_KEYWORDS:
                if kw in args:
                    options['skill_order_choose_strategy'] = 'min'
                    args = args.replace(kw, "", 1).strip()
                    break
            for kw in AVG_KEYWORDS:
                if kw in args:
                    options['skill_order_choose_strategy'] = 'average'
                    args = args.replace(kw, "", 1).strip()
                    break
            args = args.replace(keyword, "", 1).strip()
            break

    # 技能吸取策略
    for keyword in SKILL_REF_KEYWORDS:
        if keyword in args:
            for kw in MAX_KEYWORDS:
                if kw in args:
                    options['skill_reference_choose_strategy'] = 'max'
                    args = args.replace(kw, "", 1).strip()
                    break
            for kw in MIN_KEYWORDS:
                if kw in args:
                    options['skill_reference_choose_strategy'] = 'min'
                    args = args.replace(kw, "", 1).strip()
                    break
            for kw in AVG_KEYWORDS:
                if kw in args:
                    options['skill_reference_choose_strategy'] = 'average'
                    args = args.replace(kw, "", 1).strip()
                    break
            args = args.replace(keyword, "", 1).strip()
            break

    return args.strip()


# 组合提取函数

def extract_event_id(args: str, pjsk_type: int = 0) -> Tuple[Optional[int], str]:
    """从参数中提取活动ID"""
    from services.log import logger
    
    logger.debug(f"[deck] extract_event_id 输入: args='{args}', pjsk_type={pjsk_type}")
    
    # 匹配 event123 或 活动123
    match = re.search(r'(?:活动|event)(\d+)', args)
    if match:
        event_id = int(match.group(1))
        logger.debug(f"[deck] 匹配到明确活动ID格式: event_id={event_id}")
        # 验证活动ID是否存在
        try:
            events = load_master_data('events.json', pjsk_type)
            logger.debug(f"[deck] 加载 events.json 成功, events类型={type(events)}, 长度={len(events) if events else 0}")
            if events and any(isinstance(e, dict) and e.get('id') == event_id for e in events):
                logger.debug(f"[deck] 活动ID {event_id} 验证通过")
                args = args.replace(match.group(0), "", 1).strip()
                return event_id, args
            else:
                logger.warning(f"[deck] 活动ID {event_id} 验证失败，但因为是明确格式，仍然使用")
        except Exception as e:
            logger.warning(f"[deck] 加载 events.json 失败: {e}，直接使用活动ID {event_id}")
        # 如果加载失败，直接使用该ID（不验证）
        args = args.replace(match.group(0), "", 1).strip()
        return event_id, args

    # 匹配纯数字（1-3位）
    match = re.search(r'\b(\d{1,3})\b', args)
    if match:
        event_id = int(match.group(1))
        logger.debug(f"[deck] 匹配到纯数字: event_id={event_id}")
        # 验证活动ID是否存在
        try:
            events = load_master_data('events.json', pjsk_type)
            logger.debug(f"[deck] 加载 events.json 成功, events类型={type(events)}, 长度={len(events) if events else 0}")
            if events and any(isinstance(e, dict) and e.get('id') == event_id for e in events):
                logger.debug(f"[deck] 纯数字 {event_id} 验证为有效活动ID")
                args = args.replace(match.group(0), "", 1).strip()
                return event_id, args
            else:
                logger.debug(f"[deck] 纯数字 {event_id} 不是有效活动ID，留给后续处理")
        except Exception as e:
            logger.warning(f"[deck] 加载 events.json 失败: {e}，纯数字 {event_id} 不作为活动ID")
        # 如果活动ID不存在，不移除数字，让后续的 extract_music 处理

    logger.debug(f"[deck] extract_event_id 返回: event_id=None, args='{args}'")
    return None, args


def get_current_event_id(pjsk_type: int = 0) -> Optional[int]:
    """获取当前活动ID"""
    from .._utils import currentevent
    try:
        event_info = currentevent(pjsk_type)
        if event_info and event_info.get('id'):
            return event_info['id']
    except Exception:
        pass
    return None


def get_wl_chapters(event_id: int, pjsk_type: int = 0) -> List[dict]:
    """获取指定活动的 World Link 章节。"""
    try:
        chapters = load_master_data('worldBlooms.json', pjsk_type)
    except Exception:
        return []
    ret = [
        chapter for chapter in chapters
        if isinstance(chapter, dict) and chapter.get('eventId') == event_id
    ]
    ret.sort(key=lambda x: x.get('chapterNo', 0))
    return ret


def _current_wl_chapter(chapters: List[dict]) -> Optional[dict]:
    """返回当前已经开始的 WL 章节；未开始时返回第一章。"""
    if not chapters:
        return None
    now_ms = int(time.time() * 1000)
    started = [c for c in chapters if c.get('chapterStartAt', 0) <= now_ms]
    if started:
        started.sort(key=lambda x: x.get('chapterStartAt', 0), reverse=True)
        return started[0]
    return chapters[0]


def extract_wl_chapter(args: str, event_id: Optional[int], pjsk_type: int = 0) -> Tuple[Optional[dict], str]:
    """从组卡参数中提取 WL 角色/章节。

    支持：wl、wl2、wlmfy、wl mfy、-c mfy、直接 mfy。
    仅当 event_id 对应 World Link 活动时生效。
    """
    if not event_id:
        return None, args.strip()
    chapters = get_wl_chapters(event_id, pjsk_type)
    if not chapters:
        return None, args.strip()

    raw_args = args.strip()
    alias_map = _get_deck_chara_alias_map()
    lower_args = raw_args.lower()

    def remove_once(text: str, token: str) -> str:
        return re.sub(rf'(?<!\S){re.escape(token)}(?!\S)', ' ', text, count=1, flags=re.IGNORECASE).strip()

    def find_by_cid(cid: int) -> Optional[dict]:
        return next((c for c in chapters if c.get('gameCharacterId') == cid), None)

    # wl2 / wl 2 / wl第2章
    seq_match = re.search(r'(?<!\S)wl\s*(?:第)?(\d+)(?:章)?(?!\S)', lower_args)
    if seq_match:
        seq = int(seq_match.group(1))
        chapter = next((c for c in chapters if c.get('chapterNo') == seq), None)
        if chapter:
            raw_args = re.sub(re.escape(seq_match.group(0)), ' ', raw_args, count=1, flags=re.IGNORECASE).strip()
            return chapter, raw_args

    tokens = raw_args.split()

    # -c mfy
    for i, token in enumerate(tokens[:-1]):
        if token.lower() in ('-c', 'c'):
            nick = tokens[i + 1].lower()
            cid = alias_map.get(nick)
            chapter = find_by_cid(cid) if cid is not None else None
            if chapter:
                tokens = tokens[:i] + tokens[i + 2:]
                return chapter, ' '.join(tokens).strip()

    # wlmfy / wl mfy
    sorted_aliases = sorted(alias_map.keys(), key=len, reverse=True)
    for nick in sorted_aliases:
        cid = alias_map[nick]
        chapter = find_by_cid(cid)
        if not chapter:
            continue
        compact = f'wl{nick}'
        for i, token in enumerate(tokens):
            tl = token.lower()
            if tl == compact:
                tokens.pop(i)
                return chapter, ' '.join(tokens).strip()
            if tl == 'wl' and i + 1 < len(tokens) and tokens[i + 1].lower() == nick:
                tokens = tokens[:i] + tokens[i + 2:]
                return chapter, ' '.join(tokens).strip()

    # 单独 wl：当前章节
    if any(token.lower() == 'wl' for token in tokens):
        chapter = _current_wl_chapter(chapters)
        if chapter:
            raw_args = remove_once(raw_args, 'wl')
            return chapter, raw_args

    # 直接角色昵称：活动组卡 mfy
    for i, token in enumerate(tokens):
        cid = alias_map.get(token.lower())
        chapter = find_by_cid(cid) if cid is not None else None
        if chapter:
            tokens.pop(i)
            return chapter, ' '.join(tokens).strip()

    return None, raw_args


def apply_wl_chapter_options(options: dict, chapter: Optional[dict]):
    """把 WL 章节写入 deck-service options。"""
    if not chapter:
        return
    options['world_bloom_character_id'] = chapter.get('gameCharacterId')
    options['world_bloom_chapter_no'] = chapter.get('chapterNo')


async def build_event_options(args: str, pjsk_type: int = 0) -> dict:
    """构建活动组卡的完整 options"""
    from services.log import logger
    
    logger.debug(f"[deck] build_event_options 开始: args='{args}', pjsk_type={pjsk_type}")
    
    options = {}
    additional, args = extract_additional_options(args)

    args = extract_live_type(args, options)
    args = extract_multilive_options(args, options)
    args = extract_fixed_cards(args, options)
    args = extract_card_config(args, options)
    args = extract_target(args, options)
    args = extract_skill_strategy(args, options)

    # 算法
    options['algorithm'] = 'all'
    options['timeout_ms'] = int(DECK_RECOMMEND_TIMEOUT * 1000)
    if 'dfs' in args:
        options['algorithm'] = 'dfs'
        args = args.replace('dfs', '').strip()
        options['timeout_ms'] = int(DECK_RECOMMEND_TIMEOUT_SINGLE_ALG * 1000)
    elif options.get('fixed_characters'):
        # 固定角色的第一个角色就是实际队长约束；默认 all 会混跑 GA，
        # 部分后端/算法路径可能不严格保留该顺序，改用 DFS 保证计算时即按队长约束搜索。
        options['algorithm'] = 'dfs'
        options['timeout_ms'] = int(DECK_RECOMMEND_TIMEOUT_SINGLE_ALG * 1000)

    # 活动ID
    # 先尝试匹配团+属性（模拟活动）
    unit, new_args = extract_unit(args)
    attr, new_args2 = extract_attr(new_args if unit else args)

    if unit and attr:
        logger.debug(f"[deck] 识别为模拟活动: unit={unit}, attr={attr}")
        options['event_unit'] = unit
        options['event_attr'] = attr
        args = new_args2 if unit else new_args2
    else:
        # 匹配活动ID
        logger.debug(f"[deck] 尝试提取活动ID，当前 args='{args}'")
        event_id, args = extract_event_id(args, pjsk_type)
        logger.debug(f"[deck] extract_event_id 返回: event_id={event_id}, args='{args}'")
        
        if event_id is None:
            logger.debug(f"[deck] 未提取到活动ID，尝试获取当前活动")
            event_id = get_current_event_id(pjsk_type)
            logger.debug(f"[deck] 当前活动ID: {event_id}")
        
        if event_id:
            options['event_id'] = event_id
            chapter, args = extract_wl_chapter(args, event_id, pjsk_type)
            if chapter is None:
                # WL 活动组卡默认使用当前正在进行的章节角色。
                chapter = _current_wl_chapter(get_wl_chapters(event_id, pjsk_type))
            apply_wl_chapter_options(options, chapter)
            if chapter:
                logger.debug(
                    f"[deck] 识别为 WL 章节组卡: event_id={event_id}, "
                    f"chapter={chapter.get('chapterNo')}, cid={chapter.get('gameCharacterId')}"
                )
            logger.debug(f"[deck] 最终使用活动ID: {event_id}")
        else:
            logger.warning(f"[deck] 没有找到任何活动ID")

    # 歌曲
    logger.debug(f"[deck] 提取歌曲前 args='{args}'")
    args = await extract_music(args, options, pjsk_type)
    logger.debug(f"[deck] 提取歌曲后: music_id={options.get('music_id')}, music_diff={options.get('music_diff')}, args='{args}'")

    # 组卡限制
    options['limit'] = DECK_RETURN_NUM_MULTI

    # 技能顺序策略（默认值）
    if 'skill_order_choose_strategy' not in options:
        options['skill_order_choose_strategy'] = 'average'
    if 'skill_reference_choose_strategy' not in options:
        options['skill_reference_choose_strategy'] = 'average'

    # 应用 additional 中的选项到 options
    if additional.get('teammate_power'):
        options['multi_live_teammate_power'] = additional['teammate_power']
    if additional.get('teammate_scoreup'):
        options['multi_live_teammate_score_up'] = additional['teammate_scoreup']
    if additional.get('keep_after_training_state'):
        options['keep_after_training_state'] = True
    # area_item_level 在 _get_user_data_bytes 中处理，不传给后端
    if additional.get('multi_live_score_up_lower_bound'):
        options['multi_live_score_up_lower_bound'] = additional['multi_live_score_up_lower_bound']
    
    # 排除卡牌：通过 single_card_configs 实现
    if additional.get('excluded_cards'):
        if 'single_card_configs' not in options:
            options['single_card_configs'] = []
        for card_id in additional['excluded_cards']:
            options['single_card_configs'].append({
                'card_id': card_id,
                'disable': True,
            })
    
    if additional.get('unit_filter'):
        options['unit_filter'] = additional['unit_filter']
    if additional.get('attr_filter'):
        options['attr_filter'] = additional['attr_filter']

    logger.debug(f"[deck] build_event_options 完成，最终 options: event_id={options.get('event_id')}, music_id={options.get('music_id')}")
    
    return {
        'options': options,
        'last_args': args.strip(),
        'additional': additional,
    }


async def build_challenge_options(args: str, pjsk_type: int = 0) -> dict:
    """构建挑战组卡的完整 options"""
    options = {}
    additional, args = extract_additional_options(args)

    args = extract_live_type(args, options)
    options['live_type'] = 'challenge_auto' if options.get('live_type') == 'auto' else 'challenge'
    args = extract_fixed_cards(args, options)
    args = extract_card_config(args, options)
    args = extract_target(args, options)
    args = extract_skill_strategy(args, options)

    # 算法
    options['algorithm'] = 'all'
    options['timeout_ms'] = int(DECK_RECOMMEND_TIMEOUT * 1000)
    if 'dfs' in args:
        options['algorithm'] = 'dfs'
        args = args.replace('dfs', '').strip()
        options['timeout_ms'] = int(DECK_RECOMMEND_TIMEOUT_SINGLE_ALG * 1000)

    # 指定角色（挑战组卡可以指定角色）
    # 从参数中提取角色昵称
    alias_map = _get_deck_chara_alias_map()
    
    segs = args.split()
    full_nickname = None
    part_nickname = None
    
    for seg in segs:
        seg_lower = seg.lower()
        # 完全匹配
        if seg_lower in alias_map and not any(c.isdigit() for c in seg):
            if not full_nickname:
                full_nickname = seg_lower
        # 部分匹配
        for nickname in alias_map.keys():
            if nickname in seg_lower and not any(c.isdigit() for c in seg):
                if not part_nickname:
                    part_nickname = nickname
    
    # 优先使用完全匹配的昵称
    if full_nickname:
        options['challenge_live_character_id'] = alias_map[full_nickname]
        args = args.replace(full_nickname, '', 1).strip()
    elif part_nickname:
        options['challenge_live_character_id'] = alias_map[part_nickname]
        # 找到原始输入中包含这个昵称的词
        for seg in segs:
            if part_nickname in seg.lower():
                args = args.replace(seg, '', 1).strip()
                break
    # 不指定角色情况下每个角色都组1个最强卡

    # 歌曲
    args = await extract_music(args, options, pjsk_type)

    # 组卡限制
    options['limit'] = DECK_RETURN_NUM_CHALLENGE

    # 技能顺序策略（默认值）
    if 'skill_order_choose_strategy' not in options:
        options['skill_order_choose_strategy'] = 'max'
    if 'skill_reference_choose_strategy' not in options:
        options['skill_reference_choose_strategy'] = 'max'

    # 应用 additional 中的选项到 options
    # 排除卡牌：通过 single_card_configs 实现
    if additional.get('excluded_cards'):
        if 'single_card_configs' not in options:
            options['single_card_configs'] = []
        for card_id in additional['excluded_cards']:
            options['single_card_configs'].append({
                'card_id': card_id,
                'disable': True,
            })
    
    if additional.get('unit_filter'):
        options['unit_filter'] = additional['unit_filter']
    if additional.get('attr_filter'):
        options['attr_filter'] = additional['attr_filter']

    return {
        'options': options,
        'last_args': args.strip(),
        'additional': additional,
    }


async def build_no_event_options(args: str, pjsk_type: int = 0) -> dict:
    """构建长草组卡的完整 options"""
    options = {}
    additional, args = extract_additional_options(args)

    args = extract_live_type(args, options)
    args = extract_multilive_options(args, options)
    args = extract_fixed_cards(args, options)
    args = extract_card_config(args, options)
    args = extract_target(args, options)
    args = extract_skill_strategy(args, options)

    # 算法
    options['algorithm'] = 'all'
    options['timeout_ms'] = int(DECK_RECOMMEND_TIMEOUT_NO_EVENT * 1000)
    if 'dfs' in args:
        options['algorithm'] = 'dfs'
        args = args.replace('dfs', '').strip()
        options['timeout_ms'] = int(DECK_RECOMMEND_TIMEOUT_SINGLE_ALG * 1000)
    elif options.get('fixed_characters'):
        # 固定角色的第一个角色就是实际队长约束；默认 all 会混跑 GA，
        # 部分后端/算法路径可能不严格保留该顺序，改用 DFS 保证计算时即按队长约束搜索。
        options['algorithm'] = 'dfs'
        options['timeout_ms'] = int(DECK_RECOMMEND_TIMEOUT_SINGLE_ALG * 1000)

    # 无活动（不设置 event_id，让后端知道这是无活动组卡）
    # options['event_id'] = None  # 不传递 None 值

    # 歌曲
    args = await extract_music(args, options, pjsk_type)

    # 组卡限制
    options['limit'] = DECK_RETURN_NUM_MULTI

    # 技能顺序策略（默认值）
    if 'skill_order_choose_strategy' not in options:
        options['skill_order_choose_strategy'] = 'average'
    if 'skill_reference_choose_strategy' not in options:
        options['skill_reference_choose_strategy'] = 'average'

    # 应用 additional 中的选项到 options
    if additional.get('teammate_power'):
        options['multi_live_teammate_power'] = additional['teammate_power']
    if additional.get('teammate_scoreup'):
        options['multi_live_teammate_score_up'] = additional['teammate_scoreup']
    
    # 排除卡牌：通过 single_card_configs 实现
    if additional.get('excluded_cards'):
        if 'single_card_configs' not in options:
            options['single_card_configs'] = []
        for card_id in additional['excluded_cards']:
            options['single_card_configs'].append({
                'card_id': card_id,
                'disable': True,
            })
    
    if additional.get('unit_filter'):
        options['unit_filter'] = additional['unit_filter']
    if additional.get('attr_filter'):
        options['attr_filter'] = additional['attr_filter']

    return {
        'options': options,
        'last_args': args.strip(),
        'additional': additional,
    }


async def build_bonus_options(args: str, pjsk_type: int = 0) -> dict:
    """构建加成组卡的完整 options"""
    options = {}
    additional, args = extract_additional_options(args)

    options['algorithm'] = 'dfs'
    options['timeout_ms'] = int(DECK_RECOMMEND_TIMEOUT_BONUS * 1000)
    options['target'] = 'bonus'
    options['live_type'] = 'solo'

    # 卡牌配置（加成组卡不修改卡牌状态）
    for rarity in ['rarity_1_config', 'rarity_2_config', 'rarity_3_config', 'rarity_4_config', 'rarity_birthday_config']:
        options[rarity] = _nochange_card_config()

    # 活动ID
    event_id, args = extract_event_id(args, pjsk_type)
    if event_id is None:
        event_id = get_current_event_id(pjsk_type)
    if event_id:
        options['event_id'] = event_id
        chapter, args = extract_wl_chapter(args, event_id, pjsk_type)
        if chapter is None:
            chapter = _current_wl_chapter(get_wl_chapters(event_id, pjsk_type))
        apply_wl_chapter_options(options, chapter)

    # 歌曲（加成组卡使用默认歌曲）
    options['music_id'] = OMAKASE_MUSIC_ID
    options['music_diff'] = 'master'

    # 组卡限制
    options['limit'] = DECK_RETURN_NUM_BONUS

    # 目标加成值
    try:
        target_bonus = list(map(int, args.split()))
        if target_bonus:
            options['target_bonus_list'] = target_bonus
            args = ''
    except ValueError:
        pass

    return {
        'options': options,
        'last_args': args.strip(),
        'additional': additional,
    }
