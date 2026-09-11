"""查卡概览出图。

绘制实现在 ../card.py（团体分组 / 属性分组两种版面），
这里只把它们暴露成渲染任务：指令侧传筛选后的卡面 id。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..card import build_attr_grouped_image, build_unit_grouped_image
from ..context import get_context
from ..primitives import image_to_jpeg, run_pjsk_thread
from ..registry import register


async def _load_common(pjsk_type: int, badge_fields_present: bool = False) -> Dict[str, Any]:
    ctx = get_context()
    common: Dict[str, Any] = {
        'allcards': await ctx.async_load_master_data('cards.json', pjsk_type),
        'skills': await ctx.async_load_master_data('skills.json', pjsk_type),
        'gameCharacters': await ctx.async_load_master_data('gameCharacters.json', pjsk_type),
    }
    if badge_fields_present:
        # Go 侧已完成徽章分类，避免绘图服务重新读取大型 costume3ds 表。
        common.update({
            'cardCostume3ds': [],
            'costume3ds': [],
            'card_supplies': [],
        })
    else:
        common.update({
            'cardCostume3ds': await ctx.async_load_master_data('cardCostume3ds.json', pjsk_type),
            'costume3ds': await ctx.async_load_master_data('costume3ds.json', pjsk_type),
            'card_supplies': await ctx.async_load_master_data('cardSupplies.json', pjsk_type),
        })
    return common


def _pick_cards(allcards: List[dict], card_ids: List[int]) -> List[dict]:
    by_id = {card['id']: card for card in allcards if isinstance(card, dict) and card.get('id') is not None}
    return [by_id[cid] for cid in card_ids if cid in by_id]


@register("findcard")
async def render_findcard(payload: dict) -> bytes:
    """团体/角色查卡概览。载荷：card_ids、ordered_chars、unit_internal、pjsk_type。"""
    pjsk_type = int(payload.get("pjsk_type", 0))
    badge_fields_present = "limited_card_ids" in payload or "fes_card_ids" in payload
    common = await _load_common(pjsk_type, badge_fields_present=badge_fields_present)
    limited_card_ids = {int(cid) for cid in payload.get("limited_card_ids") or []} if badge_fields_present else None
    fes_card_ids = {int(cid) for cid in payload.get("fes_card_ids") or []} if badge_fields_present else None
    target_cards = _pick_cards(common['allcards'], [int(c) for c in payload.get("card_ids") or []])
    pic = await build_unit_grouped_image(
        target_cards,
        common['allcards'],
        common['cardCostume3ds'],
        common['costume3ds'],
        common['skills'],
        common['gameCharacters'],
        payload.get("unit_internal") or 'all',
        [int(c) for c in payload.get("ordered_chars") or []],
        common['card_supplies'],
        pjsk_type=pjsk_type,
        limited_card_ids=limited_card_ids,
        fes_card_ids=fes_card_ids,
    )
    # 原实现是 pic.save(path, format='JPEG', quality=85)，保持不加水印。
    return await run_pjsk_thread(image_to_jpeg, pic, quality=85, watermark=False)


@register("findcard_by_attr")
async def render_findcard_by_attr(payload: dict) -> bytes:
    """按属性分组的查卡概览。载荷：card_ids、pjsk_type。"""
    pjsk_type = int(payload.get("pjsk_type", 0))
    badge_fields_present = "limited_card_ids" in payload or "fes_card_ids" in payload
    common = await _load_common(pjsk_type, badge_fields_present=badge_fields_present)
    limited_card_ids = {int(cid) for cid in payload.get("limited_card_ids") or []} if badge_fields_present else None
    fes_card_ids = {int(cid) for cid in payload.get("fes_card_ids") or []} if badge_fields_present else None
    target_cards = _pick_cards(common['allcards'], [int(c) for c in payload.get("card_ids") or []])
    pic = await build_attr_grouped_image(
        target_cards,
        common['allcards'],
        common['cardCostume3ds'],
        common['costume3ds'],
        common['skills'],
        common['gameCharacters'],
        common['card_supplies'],
        pjsk_type=pjsk_type,
        limited_card_ids=limited_card_ids,
        fes_card_ids=fes_card_ids,
    )
    return await run_pjsk_thread(image_to_jpeg, pic, quality=85, watermark=False)
