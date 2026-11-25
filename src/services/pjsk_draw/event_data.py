from __future__ import annotations

from typing import Dict, List, Optional, Set

from .context import get_context


def normalize_master_list(data):
    if isinstance(data, dict):
        return list(data.values())
    return data or []


def get_event_card_ids(event_id: int, pjsk_type: int = 0) -> Set[int]:
    event_cards = normalize_master_list(get_context().load_master_data('eventCards.json', pjsk_type))
    return {
        int(ec['cardId']) for ec in event_cards
        if isinstance(ec, dict) and ec.get('eventId') == event_id and ec.get('cardId')
    }


def get_event_music_ids(event_id: int, pjsk_type: int = 0) -> List[int]:
    event_musics = normalize_master_list(get_context().load_master_data('eventMusics.json', pjsk_type))
    matched = [
        em for em in event_musics
        if isinstance(em, dict) and em.get('eventId') == event_id and em.get('musicId')
    ]
    matched.sort(key=lambda x: x.get('seq', 0))
    return [int(em['musicId']) for em in matched]


def get_ban_events_id_set(pjsk_type: int = 0) -> Set[int]:
    load_master_data = get_context().load_master_data
    events = normalize_master_list(load_master_data('events.json', pjsk_type))
    event_music_ids = {
        int(em['eventId']) for em in normalize_master_list(load_master_data('eventMusics.json', pjsk_type))
        if isinstance(em, dict) and em.get('eventId')
    }
    normal_event_ids = {
        int(event['id']) for event in events
        if isinstance(event, dict)
        and event.get('eventType') in ('marathon', 'cheerful_carnival')
        and event.get('id') in event_music_ids
    }
    normal_event_ids.add(74)  # SDL3 特判
    return normal_event_ids


def _is_fes_card_for_ban(card: Dict, card_supply_by_id: Dict[int, str]) -> bool:
    supply_type = card_supply_by_id.get(card.get('cardSupplyId'), '')
    return 'festival_limited' in supply_type


def get_event_banner_chara_ids(
    pjsk_type: int = 0,
    *,
    cards=None,
    event_cards=None,
    card_supplies=None,
) -> Dict[int, int]:
    load = get_context().load_master_data
    if cards is None:
        cards = load('cards.json', pjsk_type)
    if event_cards is None:
        event_cards = load('eventCards.json', pjsk_type)
    if card_supplies is None:
        card_supplies = load('cardSupplies.json', pjsk_type)
    card_by_id = {
        int(card['id']): card for card in normalize_master_list(cards)
        if isinstance(card, dict) and card.get('id')
    }
    supplies = {
        int(supply['id']): supply.get('cardSupplyType', '')
        for supply in normalize_master_list(card_supplies)
        if isinstance(supply, dict) and supply.get('id')
    }
    banner_card_ids = {}
    for link in normalize_master_list(event_cards):
        if not isinstance(link, dict) or not link.get('cardId'):
            continue
        event_id = link.get('eventId')
        card_id = int(link['cardId'])
        card = card_by_id.get(card_id)
        if card is None or _is_fes_card_for_ban(card, supplies):
            continue
        if event_id not in banner_card_ids or card_id < banner_card_ids[event_id]:
            banner_card_ids[event_id] = card_id
    return {
        event_id: card_by_id[card_id].get('characterId')
        for event_id, card_id in banner_card_ids.items()
    }


def get_event_banner_chara_id(event_id: int, pjsk_type: int = 0) -> Optional[int]:
    load_master_data = get_context().load_master_data
    cards = normalize_master_list(load_master_data('cards.json', pjsk_type))
    card_by_id = {int(c['id']): c for c in cards if isinstance(c, dict) and c.get('id')}
    card_supply_by_id = {
        int(cs['id']): cs.get('cardSupplyType', '')
        for cs in normalize_master_list(load_master_data('cardSupplies.json', pjsk_type))
        if isinstance(cs, dict) and cs.get('id')
    }

    candidate_ids = []
    for card_id in get_event_card_ids(event_id, pjsk_type):
        card = card_by_id.get(card_id)
        if not card:
            continue
        if _is_fes_card_for_ban(card, card_supply_by_id):
            continue
        candidate_ids.append(card_id)
    if not candidate_ids:
        return None
    banner_card = card_by_id.get(min(candidate_ids))
    if not banner_card:
        return None
    return banner_card.get('characterId')


def analysisunitid(unitid, gameCharacterUnits=None, pjsk_type: int = 0):
    if gameCharacterUnits is None:
        gameCharacterUnits = get_context().load_master_data('gameCharacterUnits.json', pjsk_type)
    for units in gameCharacterUnits:
        if not isinstance(units, dict):
            continue
        if units['id'] == unitid:
            if unitid <= 20:
                return unitid, units['unit'], f'chr_ts_90_{unitid}.png'
            elif units['gameCharacterId'] == 21:
                if unitid != 21:
                    return 21, units['unit'], f'chr_ts_90_21_{unitid - 25}.png'
                else:
                    return 21, 'piapro', f'chr_ts_90_21.png'
            else:
                return units['gameCharacterId'], units['unit'], f'chr_ts_90_{units["gameCharacterId"]}_2.png'
