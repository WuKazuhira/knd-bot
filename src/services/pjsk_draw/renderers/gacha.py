"""模拟抽卡（gacha）十连图。

原 plugins/pjsk/gacha/_data_source.py 的绘制部分。
"""

from __future__ import annotations

from typing import List

from PIL import Image

from utils.pjsk_paths import STATIC_PATH

from ..context import get_context
from ..primitives import image_to_png, run_pjsk_thread
from ..registry import register

static_path = STATIC_PATH


# 抽卡图
async def gachapic(charas: List, pjsk_type: int = 0):
    pic = Image.open(static_path / f'pics/gacha.png')
    cards = await get_context().async_load_master_data('cards.json', pjsk_type)
    cover = Image.new('RGB', (1550, 600), (255, 255, 255))
    pic.paste(cover, (314, 500))
    for i in range(0, 5):
        cardpic = await gachacardthumnail(charas[i], False, cards, pjsk_type=pjsk_type)
        cardpic = cardpic.resize((263, 263))
        r, g, b, mask = cardpic.split()
        pic.paste(cardpic, (336 + 304 * i, 520), mask)
    for i in range(0, 5):
        cardpic = await gachacardthumnail(charas[i+5], False, cards, pjsk_type=pjsk_type)
        cardpic = cardpic.resize((263, 263))
        r, g, b, mask = cardpic.split()
        pic.paste(cardpic, (336 + 304 * i, 825), mask)
    pic = pic.convert('RGB')
    return pic

# gacha 卡面缩略图
async def gachacardthumnail(cardid: int, istrained: bool = False, cards=None, pjsk_type: int = 0) -> 'Image':
    if cards is None:
        cards = await get_context().async_load_master_data('cards.json', pjsk_type)
    if istrained:
        suffix = 'after_training'
    else:
        suffix = 'normal'
    for card in cards:
        if card['id'] == cardid:
            if card['cardRarityType'] != 'rarity_3' and card['cardRarityType'] != 'rarity_4':
                suffix = 'normal'
            pic = Image.new('RGBA', (338, 338), (0, 0, 0, 0))
            cardpic = await get_context().get_asset(
                f'startapp/character/member_cutout/{card["assetbundleName"]}', f'{suffix}.png',
                pjsk_type=pjsk_type
            )
            # 确保 cardpic 是 RGBA 模式
            if cardpic is not None:
                if cardpic.mode != 'RGBA':
                    cardpic = cardpic.convert('RGBA')
                picmask = Image.open(static_path / 'pics/gachacardmask.png')
                if picmask.mode != 'RGBA':
                    picmask = picmask.convert('RGBA')
                # 确保 cardpic 和 pic 大小一致
                if cardpic.size != pic.size:
                    cardpic = cardpic.resize(pic.size)
                r, g, b, mask = picmask.split()
                # 确保 mask 大小和 pic 一致
                if mask.size != pic.size:
                    mask = mask.resize(pic.size)
                pic.paste(cardpic, (0, 0), mask)
            cardFrame = Image.open(static_path / f'chara/cardFrame_{card["cardRarityType"]}.png')
            cardFrame = cardFrame.resize((338, 338))
            r, g, b, mask = cardFrame.split()

            pic.paste(cardFrame, (0, 0), mask)
            if card['cardRarityType'] == 'rarity_1':
                star = Image.open(static_path / 'chara/rarity_star_normal.png')
                star = star.resize((61, 61))
                r, g, b, mask = star.split()
                pic.paste(star, (21, 256), mask)
            if card['cardRarityType'] == 'rarity_2':
                star = Image.open(static_path / 'chara/rarity_star_normal.png')
                star = star.resize((60, 60))
                r, g, b, mask = star.split()
                pic.paste(star, (21, 256), mask)
                pic.paste(star, (78, 256), mask)
            if card['cardRarityType'] == 'rarity_3':
                if istrained:
                    star = Image.open(static_path / 'chara/rarity_star_afterTraining.png')
                else:
                    star = Image.open(static_path / 'chara/rarity_star_normal.png')
                star = star.resize((60, 60))
                r, g, b, mask = star.split()
                pic.paste(star, (21, 256), mask)
                pic.paste(star, (78, 256), mask)
                pic.paste(star, (134, 256), mask)
            if card['cardRarityType'] == 'rarity_4':
                if istrained:
                    star = Image.open(static_path / 'chara/rarity_star_afterTraining.png')
                else:
                    star = Image.open(static_path / f'chara/rarity_star_normal.png')
                star = star.resize((60, 60))
                r, g, b, mask = star.split()
                pic.paste(star, (21, 256), mask)
                pic.paste(star, (78, 256), mask)
                pic.paste(star, (134, 256), mask)
                pic.paste(star, (190, 256), mask)
            if card['cardRarityType'] == 'rarity_birthday':
                star = Image.open(static_path / 'chara/rarity_birthday.png')
                star = star.resize((60, 60))
                r, g, b, mask = star.split()
                pic.paste(star, (21, 256), mask)
            attr = Image.open(static_path / f'chara/icon_attribute_{card["attr"]}.png')
            attr = attr.resize((76, 76))
            r, g, b, mask = attr.split()
            pic.paste(attr, (1, 1), mask)
            return pic


@register("gacha")
async def render_gacha(payload: dict) -> bytes:
    """十连结果图。载荷：card_ids（10 张卡面 id）、pjsk_type。"""
    pic = await gachapic(
        [int(cid) for cid in payload.get("card_ids") or []],
        pjsk_type=int(payload.get("pjsk_type", 0)),
    )
    return await run_pjsk_thread(image_to_png, pic)
