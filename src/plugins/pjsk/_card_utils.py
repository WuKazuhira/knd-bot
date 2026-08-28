import asyncio
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config.path_config import FONT_PATH

from ._autoask import pjsk_update_manager
from ._config import SERVER_MAP, data_path
from ._utils import (
    async_load_master_data,
    get_cached_render_image,
    get_pjsk_asset_cached,
    get_pjsk_font,
    load_master_data,
    master_data_by_id,
    open_pjsk_image,
    put_cached_render_image,
)

# 属性顺序（固定）
ATTR_ORDER = ['cool', 'cute', 'happy', 'mysterious', 'pure']
CARD_RENDER_LIMIT = max(4, min(8, (os.cpu_count() or 4)))
_CARD_TYPE_INDEX_CACHE: Dict[Tuple[int, int, int, int], set] = {}
_CARD_SUPPLY_TYPE_CACHE: Dict[Tuple[int, int], Dict[int, str]] = {}


def _card_ui_bg(width: int, height: int) -> Image.Image:
    """查卡概览用柔和渐变背景。"""
    top = (255, 246, 250)
    bottom = (236, 244, 255)
    img = Image.new('RGB', (width, height), top)
    draw = ImageDraw.Draw(img)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        draw.line((0, y, width, y), fill=color)
    return img


def _soft_card_shadow(size: Tuple[int, int], radius: int = 13, alpha: int = 26) -> Image.Image:
    shadow = Image.new('RGBA', size, (0, 0, 0, 0))
    d = ImageDraw.Draw(shadow)
    d.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=(70, 55, 90, alpha))
    return shadow.filter(ImageFilter.GaussianBlur(10))


def _load_master_rank_icon(master_rank: int, size: int = 22) -> Optional[Image.Image]:
    if master_rank <= 0 or master_rank > 5:
        return None
    rank_icon_path = data_path / 'chara' / f'train_rank_{master_rank}.png'
    if not rank_icon_path.exists():
        return None
    return open_pjsk_image(rank_icon_path, mode='RGBA', size=(size, size))


def render_card_thumbnail_tile(
    thumb: Image.Image,
    size: int = 84,
    has_card: bool = True,
    missing_font: Optional[ImageFont.FreeTypeFont] = None,
    master_rank: int = 0,
    card_id: int = 0,
    id_font: Optional[ImageFont.FreeTypeFont] = None,
) -> Image.Image:
    """统一渲染卡面缩略图 tile：圆角底、圆角卡面、ID、未持有、MasterRank。"""
    tile = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    shadow = _soft_card_shadow((size - 2, size - 2), radius=13, alpha=26)
    tile.paste(shadow, (2, 3), shadow.split()[3])

    card = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    cd.rounded_rectangle((1, 1, size - 2, size - 2), radius=13, fill=(255, 255, 255, 88), outline=(255, 255, 255, 190), width=1)

    inner = size - 6
    thumb = thumb.convert('RGBA').resize((inner, inner), Image.Resampling.LANCZOS)
    mask = Image.new('L', (inner, inner), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, inner - 1, inner - 1), radius=12, fill=255)
    card.paste(thumb, (3, 3), mask)

    if card_id:
        if id_font is None:
            id_font = ImageFont.truetype(str(FONT_PATH / 'SourceHanSansCN-Medium.otf'), max(9, int(size * 0.11)))
        id_text = str(card_id)
        try:
            bbox = id_font.getbbox(id_text)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
        except Exception:
            text_w, text_h = id_font.getsize(id_text)
        label_w = min(size - 12, text_w + 8)
        label_h = max(14, text_h + 4)
        cd.rounded_rectangle((6, 6, 6 + label_w, 6 + label_h), radius=6, fill=(255, 255, 255, 205))
        cd.text((10, 6 + label_h // 2), id_text, fill=(70, 60, 82), font=id_font, anchor='lm')

    if has_card and master_rank > 0:
        rank_size = max(18, int(size * 0.26))
        rank_icon = _load_master_rank_icon(master_rank, size=rank_size)
        if rank_icon is not None:
            card.paste(rank_icon, (size - rank_size - 5, size - rank_size - 5), rank_icon.split()[3])

    if not has_card:
        overlay = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle((3, 3, size - 4, size - 4), radius=12, fill=(20, 16, 26, 112))
        od.rounded_rectangle((10, size - 25, size - 10, size - 8), radius=8, fill=(255, 255, 255, 188))
        if missing_font is None:
            missing_font = ImageFont.truetype(str(FONT_PATH / 'SourceHanSansCN-Medium.otf'), max(10, int(size * 0.13)))
        od.text((size // 2, size - 17), '未持有', fill=(70, 60, 82), font=missing_font, anchor='mm')
        card.paste(overlay, (0, 0), overlay.split()[3])

    tile.paste(card, (0, 0), card.split()[3])
    return tile


def paste_card_thumbnail_tile(base: Image.Image, thumb: Image.Image, pos: Tuple[int, int], **kwargs):
    tile = render_card_thumbnail_tile(thumb, **kwargs)
    base.paste(tile, pos, tile.split()[3])


ATTR_TILE_COLORS = {
    'cool':       ((224, 238, 255), (78, 145, 255)),
    'cute':       ((255, 226, 240), (255, 104, 165)),
    'happy':      ((255, 242, 217), (255, 174, 75)),
    'mysterious': ((239, 228, 255), (147, 103, 255)),
    'pure':       ((222, 247, 228), (78, 190, 105)),
}

UNIT_REP_COLORS = {
    'light_sound': (68, 85, 221),
    'idol': (136, 221, 68),
    'street': (238, 17, 102),
    'theme_park': (255, 153, 0),
    'school_refusal': (136, 68, 153),
    'piapro': (60, 190, 205),
}


def _soft_color(color: Tuple[int, int, int], ratio: float = 0.78) -> Tuple[int, int, int]:
    return tuple(min(255, int(v * (1 - ratio) + 255 * ratio)) for v in color)


# 稀有度排序权重（越大越靠前）
RARITY_WEIGHT = {
    'rarity_4': 5,
    'rarity_birthday': 4,
    'rarity_3': 3,
    'rarity_2': 2,
    'rarity_1': 1,
}


async def _gather_limited(coros, limit: int = CARD_RENDER_LIMIT):
    sem = asyncio.Semaphore(limit)

    async def _run(coro):
        async with sem:
            return await coro

    return await asyncio.gather(*[_run(coro) for coro in coros], return_exceptions=True)


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


# 卡面缩略图
async def cardthumnail(cardid, istrained=False, cards=None, limitedbadge=False, fesbadge=False, pjsk_type: int = 0):
    if cards is None:
        card = master_data_by_id('cards.json', pjsk_type).get(cardid)
    else:
        card = next(
            (item for item in cards if isinstance(item, dict) and item.get('id') == cardid),
            None,
        )
    if card is None:
        return None

    rarity = card.get('cardRarityType', '')
    suffix = 'after_training' if istrained and rarity in ('rarity_3', 'rarity_4') else 'normal'
    cache_key = (
        'card-thumbnail-v2', pjsk_type, cardid, card.get('assetbundleName'), rarity,
        card.get('attr'), suffix, bool(limitedbadge), bool(fesbadge),
    )
    cached = get_cached_render_image(cache_key)
    if cached is not None:
        return cached

    card_frame = open_pjsk_image(data_path / f'chara/cardFrame_{rarity}.png', mode='RGBA')
    frame_w, frame_h = card_frame.size
    pic = await get_pjsk_asset_cached(
        'startapp/thumbnail/chara',
        f'{card["assetbundleName"]}_{suffix}.png',
        pjsk_type=pjsk_type,
        mode='RGBA',
        size=(frame_w, frame_h),
    )
    if pic is None:
        pic = Image.new('RGBA', (frame_w, frame_h), (220, 220, 220, 255))

    pic.paste(card_frame, (0, 0), card_frame.split()[-1])
    star_count = {
        'rarity_1': 1,
        'rarity_2': 2,
        'rarity_3': 3,
        'rarity_4': 4,
    }.get(rarity, 0)
    if star_count:
        star_name = (
            'rarity_star_afterTraining.png'
            if suffix == 'after_training' else 'rarity_star_normal.png'
        )
        star = open_pjsk_image(data_path / f'chara/{star_name}', mode='RGBA', size=(28, 28))
        star_y = frame_h - 38
        for idx in range(star_count):
            pic.paste(star, (8 + idx * 25, star_y), star.split()[-1])
    elif rarity == 'rarity_birthday':
        star = open_pjsk_image(data_path / 'chara/rarity_birthday.png', mode='RGBA', size=(32, 31))
        pic.paste(star, (8, frame_h - 40), star.split()[-1])

    attr = open_pjsk_image(
        data_path / f'chara/icon_attribute_{card["attr"]}.png',
        mode='RGBA',
        size=(34, 34),
    )
    pic.paste(attr, (6, 6), attr.split()[-1])

    try:
        badge = None
        if fesbadge:
            badge = open_pjsk_image(data_path / 'pics/badge_fesLimited.png', mode='RGBA')
        elif limitedbadge:
            badge = open_pjsk_image(data_path / 'pics/badge_limited.png', mode='RGBA')
        if badge is not None:
            pic.paste(badge, (frame_w - badge.width, 0), badge.split()[-1])
    except (FileNotFoundError, OSError):
        pass

    put_cached_render_image(cache_key, pic)
    return pic.copy()

# 卡面大图
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


async def cardlarge(cardid: int, istrained: bool = False, cards=None, pjsk_type: int = 0):
    if cards is None:
        cards = await async_load_master_data('cards.json', pjsk_type)
    suffix = 'after_training' if istrained else 'normal'
    for card in cards:
        if not isinstance(card, dict):
            continue
        if card['id'] == cardid:
            if card['cardRarityType'] not in ('rarity_3', 'rarity_4'):
                suffix = 'normal'
            cardFrame = open_pjsk_image(data_path / f'chara/cardFrame_L_{card["cardRarityType"]}.png', mode='RGBA')
            frame_w, frame_h = cardFrame.size
            pic = await pjsk_update_manager.get_asset(
                f'startapp/character/member/{card["assetbundleName"]}', f'card_{suffix}.png',
                pjsk_type=pjsk_type
            )
            if pic is None:
                pic = Image.new('RGBA', (frame_w, frame_h), (220, 220, 220, 255))
            else:
                pic = pic.resize((frame_w, frame_h))
            r, g, b, mask = cardFrame.split()
            pic.paste(cardFrame, (0, 0), mask)
            if card['cardRarityType'] == 'rarity_1':
                star = open_pjsk_image(data_path / 'chara/rarity_star_normal.png', mode='RGBA', size=(72, 70))
                r, g, b, mask = star.split()
                pic.paste(star, (16, frame_h - 86), mask)
            if card['cardRarityType'] == 'rarity_2':
                star = open_pjsk_image(data_path / 'chara/rarity_star_normal.png', mode='RGBA', size=(72, 70))
                r, g, b, mask = star.split()
                pic.paste(star, (16, frame_h - 148), mask)
                pic.paste(star, (16, frame_h - 86), mask)
            if card['cardRarityType'] == 'rarity_3':
                if istrained:
                    star = open_pjsk_image(data_path / 'chara/rarity_star_afterTraining.png', mode='RGBA', size=(72, 70))
                else:
                    star = open_pjsk_image(data_path / 'chara/rarity_star_normal.png', mode='RGBA', size=(72, 70))
                r, g, b, mask = star.split()
                pic.paste(star, (16, frame_h - 210), mask)
                pic.paste(star, (16, frame_h - 148), mask)
                pic.paste(star, (16, frame_h - 86), mask)
            if card['cardRarityType'] == 'rarity_4':
                if istrained:
                    star = open_pjsk_image(data_path / 'chara/rarity_star_afterTraining.png', mode='RGBA', size=(72, 70))
                else:
                    star = open_pjsk_image(data_path / 'chara/rarity_star_normal.png', mode='RGBA', size=(72, 70))
                r, g, b, mask = star.split()
                pic.paste(star, (16, frame_h - 272), mask)
                pic.paste(star, (16, frame_h - 210), mask)
                pic.paste(star, (16, frame_h - 148), mask)
                pic.paste(star, (16, frame_h - 86), mask)
            if card['cardRarityType'] == 'rarity_birthday':
                star = open_pjsk_image(data_path / 'chara/rarity_birthday.png', mode='RGBA', size=(72, 70))
                r, g, b, mask = star.split()
                pic.paste(star, (16, frame_h - 86), mask)
            attr = open_pjsk_image(data_path / f'chara/icon_attribute_{card["attr"]}.png', mode='RGBA', size=(88, 88))
            r, g, b, mask = attr.split()
            pic.paste(attr, (frame_w - 100, 12), mask)
            return pic
    return Image.new('RGB', (940, 530), (255, 255, 255))


async def findcardsingle(card, allcards, cardCostume3ds, costume3ds, skills, gameCharacters, card_supplies=None, pjsk_type: int = 0):
    """渲染查卡概览中的单个卡格：缩略图、卡名/id、右下技能图标。"""
    try:
        card_id = card.get('id') if isinstance(card, dict) else int(card)
    except Exception:
        return Image.new('RGB', (420, 260), (200, 200, 200))

    if allcards is None:
        allcards = await async_load_master_data('cards.json', pjsk_type)

    card_obj = None
    for item in allcards:
        if isinstance(item, dict) and item.get('id') == card_id:
            card_obj = item
            break

    if card_obj is None:
        return Image.new('RGB', (420, 260), (200, 200, 200))

    row_color, accent_color = ATTR_TILE_COLORS.get(card_obj.get('attr'), ((255, 250, 252), (220, 150, 175)))
    pic = Image.new('RGB', (420, 260), row_color)
    draw = ImageDraw.Draw(pic)
    soft_fill = tuple(min(255, int(v * 0.35 + 255 * 0.65)) for v in row_color)
    draw.rounded_rectangle((6, 6, 414, 254), radius=18, fill=soft_fill, outline=accent_color, width=2)
    draw.rounded_rectangle((18, 18, 402, 180), radius=14, fill=(255, 255, 255), outline=(255, 255, 255), width=1)

    try:
        limited_badge = False
        fes_badge = False
        cardtypenum = cardtype(card_obj['id'], cardCostume3ds, costume3ds)
        if cardtypenum == 1 or card_obj.get('cardRarityType') == 'rarity_birthday':
            if is_fes_card(card_obj, card_supplies, pjsk_type):
                fes_badge = True
            else:
                limited_badge = True

        if card_obj.get('cardRarityType') in ('rarity_3', 'rarity_4'):
            thumbnail = await cardthumnail(
                card_obj['id'], istrained=False, cards=allcards,
                limitedbadge=limited_badge, fesbadge=fes_badge, pjsk_type=pjsk_type
            )
            tile = render_card_thumbnail_tile(thumbnail, size=150)
            pic.paste(tile, (55, 15), tile.split()[3])
            thumbnail = await cardthumnail(
                card_obj['id'], istrained=True, cards=allcards,
                limitedbadge=limited_badge, fesbadge=fes_badge, pjsk_type=pjsk_type
            )
            tile = render_card_thumbnail_tile(thumbnail, size=150)
            pic.paste(tile, (215, 15), tile.split()[3])
        else:
            thumbnail = await cardthumnail(
                card_obj['id'], istrained=False, cards=allcards,
                limitedbadge=limited_badge, fesbadge=fes_badge, pjsk_type=pjsk_type
            )
            tile = render_card_thumbnail_tile(thumbnail, size=150)
            pic.paste(tile, (135, 15), tile.split()[3])

        draw = ImageDraw.Draw(pic)

        def _text_size(font, text: str):
            try:
                bbox = draw.textbbox((0, 0), text, font=font)
                return bbox[2] - bbox[0], bbox[3] - bbox[1]
            except Exception:
                return font.getsize(text)

        font = ImageFont.truetype(str(FONT_PATH / 'SourceHanSansCN-Medium.otf'), 27)
        title = card_obj.get('prefix') or card_obj.get('cardName') or ''
        text_width = _text_size(font, title)
        if text_width[0] > 380:
            font = ImageFont.truetype(str(FONT_PATH / 'SourceHanSansCN-Medium.otf'), max(12, int(27 / (text_width[0] / 380))))
            text_width = _text_size(font, title)
        text_coordinate = ((210 - text_width[0] / 2), int(198 - text_width[1] / 2))
        draw.text(text_coordinate, title, '#32263a', font)

        name = getcharaname(card_obj['characterId'], gameCharacters, pjsk_type=pjsk_type)
        font = ImageFont.truetype(str(FONT_PATH / 'SourceHanSansCN-Medium.otf'), 18)
        sub_text = f'ID {card_obj["id"]}  {name}'
        text_width = _text_size(font, sub_text)
        chip_x1 = max(18, int(210 - text_width[0] / 2) - 12)
        chip_x2 = min(360, int(210 + text_width[0] / 2) + 12)
        draw.rounded_rectangle((chip_x1, 222, chip_x2, 246), radius=12, fill=(255, 255, 255), outline=accent_color)
        draw.text((210 - text_width[0] / 2, 225), sub_text, '#785064', font)

        for skill in skills:
            if not isinstance(skill, dict):
                continue
            if skill.get('id') == card_obj.get('skillId'):
                descriptionSpriteName = skill.get('descriptionSpriteName')
                if descriptionSpriteName:
                    skill_path = data_path / f'chara/skill_{descriptionSpriteName}.png'
                    if skill_path.exists():
                        skillTypePic = open_pjsk_image(skill_path, mode='RGBA', size=(40, 40))
                        draw.rounded_rectangle((365, 208, 409, 252), radius=12, fill=(255, 255, 255), outline=accent_color)
                        r, g, b, mask = skillTypePic.split()
                        pic.paste(skillTypePic, (367, 210), mask)
                break
        return pic
    except Exception:
        return Image.new('RGB', (420, 260), (200, 200, 200))


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


def _unit_color_by_chara_id(chara_id: int) -> Tuple[int, int, int]:
    if 1 <= chara_id <= 4:
        return UNIT_REP_COLORS['light_sound']
    if 5 <= chara_id <= 8:
        return UNIT_REP_COLORS['idol']
    if 9 <= chara_id <= 12:
        return UNIT_REP_COLORS['street']
    if 13 <= chara_id <= 16:
        return UNIT_REP_COLORS['theme_park']
    if 17 <= chara_id <= 20:
        return UNIT_REP_COLORS['school_refusal']
    return UNIT_REP_COLORS['piapro']


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


def _near_square_card_grid(n: int, cell_w: int, cell_h: int, gap: int) -> tuple[int, int]:
    """返回尽量接近正方形的卡牌网格列数和行数。"""
    if n <= 0:
        return 0, 0
    best_cols, best_rows = 1, n
    best_score = None
    for cols in range(1, n + 1):
        rows = (n + cols - 1) // cols
        width = cols * cell_w + max(0, cols - 1) * gap
        height = rows * cell_h + max(0, rows - 1) * gap
        # 优先接近正方形，其次面积小，最后略偏向少列，避免小数量时过宽。
        score = (abs(width - height), width * height, cols)
        if best_score is None or score < best_score:
            best_score = score
            best_cols, best_rows = cols, rows
    return best_cols, best_rows


async def build_unit_grouped_image(
    target_cards: List[Dict[str, Any]],
    allcards: List[Dict[str, Any]],
    cardCostume3ds: List[Dict[str, Any]],
    costume3ds: List[Dict[str, Any]],
    skills: List[Dict[str, Any]],
    gameCharacters: List[Dict[str, Any]],
    unit_internal: str,
    ordered_chars: List[int],
    card_supplies: List[Dict[str, Any]] = None,
    pjsk_type: int = 0,
) -> Image.Image:
    """
    生成团体查询的卡面概览图：五属性分行 × 角色动态列。

    每个「属性 × 角色」单元格会根据卡牌数量自动排成接近 1:1 的小网格，
    避免同一角色同一属性卡牌过多时只能在窄列中纵向堆叠。
    """
    CELL_W = 420
    CELL_H = 260
    ATTR_ICON_SIZE = 50
    AVATAR_SIZE = 80
    AVATAR_ROW_H = 100
    ATTR_COL_W = 70
    CELL_GAP = 8             # 动态单元格内部卡片间距
    COL_GAP = 8              # 角色列间距
    ATTR_ROW_GAP = 6         # 属性行之间的间距
    TOP_PAD = 16
    BOTTOM_PAD = 16
    LEFT_PAD = 16
    RIGHT_PAD = 16

    BG_COLOR       = (255, 245, 248)
    HEADER_BG      = (255, 255, 255)
    ATTR_COL_BG    = (255, 255, 255)
    LINE_HEAVY     = (220, 150, 175)
    LINE_LIGHT     = (235, 190, 210)
    LINE_HEADER    = (200, 120, 150)

    grouped: Dict[int, Dict[str, List[Dict]]] = {
        cid: {attr: [] for attr in ATTR_ORDER}
        for cid in ordered_chars
    }
    for card in target_cards:
        cid = card.get('characterId')
        attr = card.get('attr', '')
        if cid in grouped and attr in grouped[cid]:
            grouped[cid][attr].append(card)

    for cid in ordered_chars:
        for attr in ATTR_ORDER:
            grouped[cid][attr].sort(
                key=lambda c: (
                    RARITY_WEIGHT.get(c.get('cardRarityType', ''), 0),
                    c.get('releaseAt', 0)
                ),
                reverse=True
            )

    active_chars = [cid for cid in ordered_chars if any(grouped[cid][attr] for attr in ATTR_ORDER)]
    active_attrs = [attr for attr in ATTR_ORDER if any(grouped[cid][attr] for cid in active_chars)]

    if not active_chars or not active_attrs:
        return Image.new('RGB', (ATTR_COL_W + CELL_W, AVATAR_ROW_H + CELL_H + TOP_PAD + BOTTOM_PAD), BG_COLOR)

    # 预计算每个动态单元格的网格信息。
    # cell_layout[(cid, attr)] = (inner_cols, inner_rows, cell_w, cell_h)
    cell_layout: Dict[tuple, tuple[int, int, int, int]] = {}
    for cid in active_chars:
        for attr in active_attrs:
            n = len(grouped[cid][attr])
            cols, rows = _near_square_card_grid(n, CELL_W, CELL_H, CELL_GAP)
            if n <= 0:
                cell_layout[(cid, attr)] = (0, 0, CELL_W, CELL_H)
            else:
                cell_w = cols * CELL_W + max(0, cols - 1) * CELL_GAP
                cell_h = rows * CELL_H + max(0, rows - 1) * CELL_GAP
                cell_layout[(cid, attr)] = (cols, rows, cell_w, cell_h)

    col_widths: Dict[int, int] = {}
    for cid in active_chars:
        col_widths[cid] = max(cell_layout[(cid, attr)][2] for attr in active_attrs)

    attr_heights: Dict[str, int] = {}
    for attr in active_attrs:
        attr_heights[attr] = max(cell_layout[(cid, attr)][3] for cid in active_chars)

    total_content_w = sum(col_widths[cid] for cid in active_chars) + (len(active_chars) - 1) * COL_GAP
    total_content_h = sum(attr_heights[attr] for attr in active_attrs) + (len(active_attrs) - 1) * ATTR_ROW_GAP

    img_w = LEFT_PAD + ATTR_COL_W + total_content_w + RIGHT_PAD
    img_h = TOP_PAD + AVATAR_ROW_H + total_content_h + BOTTOM_PAD

    pic = _card_ui_bg(img_w, img_h)
    draw = ImageDraw.Draw(pic)

    try:
        font_name = ImageFont.truetype(str(FONT_PATH / 'SourceHanSansCN-Medium.otf'), 18)
    except Exception:
        font_name = ImageFont.load_default()

    content_left = LEFT_PAD + ATTR_COL_W
    content_right = img_w - RIGHT_PAD
    content_top = TOP_PAD + AVATAR_ROW_H

    col_x: Dict[int, int] = {}
    cur_x = content_left
    for cid in active_chars:
        col_x[cid] = cur_x
        cur_x += col_widths[cid] + COL_GAP

    attr_row_y: Dict[str, int] = {}
    cur_y = content_top
    for attr in active_attrs:
        attr_row_y[attr] = cur_y
        cur_y += attr_heights[attr] + ATTR_ROW_GAP

    draw.rounded_rectangle(
        [LEFT_PAD, TOP_PAD, img_w - RIGHT_PAD, TOP_PAD + AVATAR_ROW_H - 1],
        radius=22,
        fill=HEADER_BG,
        outline=(255, 255, 255),
        width=2
    )
    draw.rounded_rectangle(
        [LEFT_PAD, TOP_PAD, LEFT_PAD + ATTR_COL_W - 1, img_h - BOTTOM_PAD - 1],
        radius=20,
        fill=ATTR_COL_BG,
        outline=(255, 255, 255),
        width=2
    )

    for row_idx, attr in enumerate(active_attrs):
        row_y = attr_row_y[attr]
        row_h = attr_heights[attr]
        row_bg, accent = ATTR_TILE_COLORS.get(attr, ((255, 250, 252), (220, 150, 175)))
        draw.rounded_rectangle([content_left, row_y, content_right - 1, row_y + row_h - 1], radius=14, fill=row_bg, outline=(255, 255, 255))
        draw.rounded_rectangle([LEFT_PAD + 6, row_y + 6, LEFT_PAD + ATTR_COL_W - 7, row_y + row_h - 7], radius=18, fill=_soft_color(accent), outline=accent, width=2)

    # 顶部角色头像行：每列使用角色代表色胶囊底。
    for cid in active_chars:
        x = col_x[cid]
        col_w = col_widths[cid]
        rep_color = _unit_color_by_chara_id(cid)
        draw.rounded_rectangle(
            [x + 8, TOP_PAD + 8, x + col_w - 8, TOP_PAD + AVATAR_ROW_H - 9],
            radius=22,
            fill=_soft_color(rep_color, 0.82),
            outline=rep_color,
            width=2
        )
        avatar_path = data_path / f'chara/chr_ts_{cid}.png'
        avatar_drawn = False
        if avatar_path.exists():
            try:
                avatar = open_pjsk_image(avatar_path, mode='RGBA', size=(AVATAR_SIZE, AVATAR_SIZE))
                ax = x + (col_w - AVATAR_SIZE) // 2
                ay = TOP_PAD + (AVATAR_ROW_H - AVATAR_SIZE) // 2
                r, g, b, mask = avatar.split()
                pic.paste(avatar, (ax, ay), mask)
                avatar_drawn = True
            except Exception:
                pass
        if not avatar_drawn:
            name = getcharaname(cid, gameCharacters, pjsk_type=pjsk_type) or str(cid)
            try:
                bbox = draw.textbbox((0, 0), name, font=font_name)
                tw = bbox[2] - bbox[0]
            except AttributeError:
                tw, _ = font_name.getsize(name)
            draw.text(
                (x + (col_w - tw) // 2, TOP_PAD + (AVATAR_ROW_H - 18) // 2),
                name, fill=rep_color, font=font_name
            )

    # 并发渲染所有卡片，并记录每张卡在动态单元格内的位置。
    all_tasks = []
    task_map = []  # (cid, attr, inner_col, inner_row)
    for attr in active_attrs:
        for cid in active_chars:
            cols, _, _, _ = cell_layout[(cid, attr)]
            if cols <= 0:
                continue
            for idx, card in enumerate(grouped[cid][attr]):
                all_tasks.append(
                    findcardsingle(card, allcards, cardCostume3ds, costume3ds,
                                   skills, gameCharacters, card_supplies, pjsk_type=pjsk_type)
                )
                task_map.append((cid, attr, idx % cols, idx // cols))

    all_results = await _gather_limited(all_tasks)

    # 属性图标：垂直居中于对应属性行。
    for attr in active_attrs:
        row_h = attr_heights[attr]
        attr_icon_path = data_path / f'chara/icon_attribute_{attr}.png'
        if attr_icon_path.exists():
            try:
                attr_icon = open_pjsk_image(attr_icon_path, mode='RGBA', size=(ATTR_ICON_SIZE, ATTR_ICON_SIZE))
                icon_x = LEFT_PAD + (ATTR_COL_W - ATTR_ICON_SIZE) // 2
                icon_y = attr_row_y[attr] + (row_h - ATTR_ICON_SIZE) // 2
                r, g, b, mask = attr_icon.split()
                pic.paste(attr_icon, (icon_x, icon_y), mask)
            except Exception:
                pass

    # 粘贴卡片：每个动态单元格在角色列/属性行内居中。
    for task_idx, result in enumerate(all_results):
        cid, attr, inner_col, inner_row = task_map[task_idx]
        cols, rows, cell_w, cell_h = cell_layout[(cid, attr)]
        base_x = col_x[cid] + (col_widths[cid] - cell_w) // 2
        base_y = attr_row_y[attr] + (attr_heights[attr] - cell_h) // 2
        cell_x = base_x + inner_col * (CELL_W + CELL_GAP)
        cell_y = base_y + inner_row * (CELL_H + CELL_GAP)
        if isinstance(result, Exception):
            placeholder = Image.new('RGB', (CELL_W, CELL_H), (200, 200, 200))
            pic.paste(placeholder, (cell_x, cell_y))
        else:
            pic.paste(result, (cell_x, cell_y))

    # 分隔线。
    draw.line(
        [(LEFT_PAD, content_top - 1), (img_w - RIGHT_PAD - 1, content_top - 1)],
        fill=LINE_HEADER, width=4
    )
    draw.line(
        [(content_left - 1, TOP_PAD), (content_left - 1, img_h - BOTTOM_PAD - 1)],
        fill=LINE_HEADER, width=4
    )

    for attr_idx, attr in enumerate(active_attrs):
        y = attr_row_y[attr] + attr_heights[attr]
        if attr_idx < len(active_attrs) - 1:
            draw.line(
                [(LEFT_PAD, y), (img_w - RIGHT_PAD - 1, y)],
                fill=LINE_HEAVY, width=3
            )

    for idx, cid in enumerate(active_chars[1:], start=1):
        lx = col_x[cid] - COL_GAP // 2
        draw.line(
            [(lx, TOP_PAD), (lx, img_h - BOTTOM_PAD - 1)],
            fill=LINE_LIGHT, width=2
        )

    return pic

async def build_attr_grouped_image(
    target_cards: List[Dict[str, Any]],
    allcards: List[Dict[str, Any]],
    cardCostume3ds: List[Dict[str, Any]],
    costume3ds: List[Dict[str, Any]],
    skills: List[Dict[str, Any]],
    gameCharacters: List[Dict[str, Any]],
    card_supplies: List[Dict[str, Any]] = None,
    pjsk_type: int = 0,
) -> Image.Image:
    """
    生成按属性分组的卡面概览图。

    布局：
    - 每行对应一个属性（cool/cute/happy/mysterious/pure），顺序固定
    - 行内按稀有度降序（四星>生日>三星>二星>一星），同稀有度内按 releaseAt 降序
    - 每格复用 findcardsingle 的样式：三/四星同时显示训练前+训练后，含卡名/id/技能图标
    - 行左侧显示属性图标
    - 若某属性无卡则跳过该行
    - 每行超过 MAX_COLS_PER_ROW 张时自动折行，防止图片过宽
    """
    CELL_W = 420
    CELL_H = 260
    ATTR_ICON_SIZE = 50
    LEFT_PAD = 16
    ATTR_COL_W = 70         # 左侧属性图标列宽度
    COL_GAP = 2             # 列间距（用线代替）
    ROW_GAP = 10            # 同属性行内卡片间距（折行时行间距）
    ATTR_ROW_GAP = 6        # 属性行之间的间距
    TOP_PAD = 16
    BOTTOM_PAD = 16
    RIGHT_PAD = 16
    MAX_COLS_PER_ROW = 20   # 每行最多显示的卡数，超出自动折行

    # 颜色方案：属性色系（与卡牌一览保持一致）
    BG_COLOR      = (255, 245, 248)
    ATTR_COL_BG   = (255, 255, 255)
    LINE_HEAVY    = (220, 150, 175)
    LINE_LIGHT    = (235, 190, 210)
    LINE_HEADER   = (200, 120, 150)

    # 按属性分组
    grouped: Dict[str, List[Dict]] = {attr: [] for attr in ATTR_ORDER}
    for card in target_cards:
        attr = card.get('attr', '')
        if attr in grouped:
            grouped[attr].append(card)

    # 行内排序：稀有度权重降序，同权重内 releaseAt 降序
    for attr in ATTR_ORDER:
        grouped[attr].sort(
            key=lambda c: (
                RARITY_WEIGHT.get(c.get('cardRarityType', ''), 0),
                c.get('releaseAt', 0)
            ),
            reverse=True
        )

    # 只保留有卡的属性行
    active_attrs = [attr for attr in ATTR_ORDER if grouped[attr]]
    if not active_attrs:
        return Image.new('RGB', (LEFT_PAD + CELL_W, CELL_H + TOP_PAD + BOTTOM_PAD), BG_COLOR)

    # 将每个属性的卡列表按 MAX_COLS_PER_ROW 切分为若干子行
    # subrows[attr] = [[card, ...], [card, ...], ...]
    subrows: Dict[str, List[List[Dict]]] = {}
    for attr in active_attrs:
        cards = grouped[attr]
        subrows[attr] = [cards[i:i + MAX_COLS_PER_ROW] for i in range(0, len(cards), MAX_COLS_PER_ROW)]

    # 每个属性块的高度 = 子行数 * CELL_H + (子行数-1) * ROW_GAP
    def attr_block_height(attr: str) -> int:
        n = len(subrows[attr])
        return n * CELL_H + (n - 1) * ROW_GAP

    max_cols = max((len(subrow) for rows in subrows.values() for subrow in rows), default=1)
    img_w = LEFT_PAD + ATTR_COL_W + max_cols * (CELL_W + COL_GAP) - COL_GAP + RIGHT_PAD
    total_h = sum(attr_block_height(attr) for attr in active_attrs)
    img_h = TOP_PAD + total_h + (len(active_attrs) - 1) * ATTR_ROW_GAP + BOTTOM_PAD

    pic = _card_ui_bg(img_w, img_h)
    draw = ImageDraw.Draw(pic)

    content_left = LEFT_PAD + ATTR_COL_W
    content_right = img_w - RIGHT_PAD

    # ── 属性列背景 ────────────────────────────────────────────────────────────
    draw.rounded_rectangle(
        [LEFT_PAD, TOP_PAD, LEFT_PAD + ATTR_COL_W - 1, img_h - BOTTOM_PAD - 1],
        radius=20,
        fill=ATTR_COL_BG,
        outline=(255, 255, 255),
        width=2
    )

    # ── 计算每个属性块的起始 y ────────────────────────────────────────────────
    attr_block_y: Dict[str, int] = {}
    cur_y = TOP_PAD
    for attr_idx, attr in enumerate(active_attrs):
        attr_block_y[attr] = cur_y
        # 属性色背景（整个属性块）
        block_h = attr_block_height(attr)
        row_bg, accent = ATTR_TILE_COLORS.get(attr, ((255, 250, 252), (220, 150, 175)))
        draw.rounded_rectangle(
            [content_left, cur_y, content_right - 1, cur_y + block_h - 1],
            radius=14,
            fill=row_bg,
            outline=(255, 255, 255)
        )
        draw.rounded_rectangle(
            [LEFT_PAD + 6, cur_y + 6, LEFT_PAD + ATTR_COL_W - 7, cur_y + block_h - 7],
            radius=18,
            fill=_soft_color(accent),
            outline=accent,
            width=2
        )
        cur_y += block_h + ATTR_ROW_GAP

    # ── 收集所有任务，一次性并发 ──────────────────────────────────────────────
    all_tasks = []
    task_positions = []  # (attr, subrow_idx, col_idx)
    for attr in active_attrs:
        for subrow_idx, subrow_cards in enumerate(subrows[attr]):
            for col_idx, card in enumerate(subrow_cards):
                all_tasks.append(
                    findcardsingle(card, allcards, cardCostume3ds, costume3ds,
                                   skills, gameCharacters, card_supplies, pjsk_type=pjsk_type)
                )
                task_positions.append((attr, subrow_idx, col_idx))

    all_results = await _gather_limited(all_tasks)

    # ── 绘制属性图标（垂直居中于整个属性块） ──────────────────────────────────
    for attr in active_attrs:
        block_h = attr_block_height(attr)
        block_y = attr_block_y[attr]
        try:
            attr_icon = open_pjsk_image(data_path / f'chara/icon_attribute_{attr}.png', mode='RGBA', size=(ATTR_ICON_SIZE, ATTR_ICON_SIZE))
            icon_x = LEFT_PAD + (ATTR_COL_W - ATTR_ICON_SIZE) // 2
            icon_y = block_y + (block_h - ATTR_ICON_SIZE) // 2
            r, g, b, mask = attr_icon.split()
            pic.paste(attr_icon, (icon_x, icon_y), mask)
        except Exception:
            pass

    # ── 贴卡格子 ──────────────────────────────────────────────────────────────
    for task_idx, result in enumerate(all_results):
        attr, subrow_idx, col_idx = task_positions[task_idx]
        cell_y = attr_block_y[attr] + subrow_idx * (CELL_H + ROW_GAP)
        cell_x = content_left + col_idx * (CELL_W + COL_GAP)
        if isinstance(result, Exception):
            placeholder = Image.new('RGB', (CELL_W, CELL_H), (200, 200, 200))
            pic.paste(placeholder, (cell_x, cell_y))
        else:
            pic.paste(result, (cell_x, cell_y))

    # ── 分隔线 ────────────────────────────────────────────────────────────────

    # 属性列右侧竖线
    draw.line(
        [(content_left - 1, TOP_PAD), (content_left - 1, img_h - BOTTOM_PAD - 1)],
        fill=LINE_HEADER, width=4
    )

    # 属性行之间的横线
    cur_y = TOP_PAD
    for attr_idx, attr in enumerate(active_attrs):
        block_h = attr_block_height(attr)
        cur_y += block_h
        if attr_idx < len(active_attrs) - 1:
            draw.line(
                [(LEFT_PAD, cur_y), (img_w - RIGHT_PAD - 1, cur_y)],
                fill=LINE_HEAVY, width=2
            )
        cur_y += ATTR_ROW_GAP

    # 列之间的竖线
    for col_idx in range(1, max_cols):
        lx = content_left + col_idx * (CELL_W + COL_GAP) - COL_GAP
        draw.line(
            [(lx, TOP_PAD), (lx, img_h - BOTTOM_PAD - 1)],
            fill=LINE_LIGHT, width=2
        )

    return pic

def get_chara_icon_by_chara_id(chara_id: int, size: tuple = None):
    """
    通过角色ID获取角色头像
    返回 PIL Image 对象
    """
    from PIL import Image
    
    # 角色头像路径格式：chr_sd_{id:02d}_01/chr_sd_{id:02d}_01.png
    icon_path = data_path / 'chara' / f'chr_sd_{chara_id:02d}_01' / f'chr_sd_{chara_id:02d}_01.png'
    
    if not icon_path.exists():
        # 文件不存在时返回默认图标
        default_img = Image.new('RGBA', (100, 100), (200, 200, 200, 255))
        if size:
            default_img = default_img.resize(size, Image.Resampling.LANCZOS)
        return default_img
    
    img = open_pjsk_image(icon_path, mode='RGBA', size=size)
    return img
