from __future__ import annotations

from typing import Any

_OPTION_ALIASES = {
    "live_type": "liveType",
    "event_id": "eventId",
    "event_type": "eventType",
    "event_unit": "eventUnit",
    "event_attr": "eventAttr",
    "custom_bonus_character_ids": "customBonusCharacterIds",
    "custom_bonus_attr": "customBonusAttr",
    "custom_bonus_support_units": "customBonusCharacterSupportUnits",
    "custom_bonus_character_support_units": "customBonusCharacterSupportUnits",
    "target_bonus_list": "targetBonusList",
    "world_bloom_character_id": "worldBloomCharacterId",
    "world_bloom_chapter_no": "worldBloomEventTurn",
    "world_bloom_event_turn": "worldBloomEventTurn",
    "world_bloom_finale_turn": "worldBloomFinaleTurn",
    "forced_leader_character_id": "forcedLeaderCharacterId",
    "support_master_max": "supportMasterMax",
    "support_skill_max": "supportSkillMax",
    "fixed_cards": "fixedCards",
    "fixed_characters": "fixedCharacters",
    "excluded_cards": "excludedCards",
    "challenge_live_character_id": "challengeLiveCharacterId",
    "unit_filter": "unitFilter",
    "attr_filter": "attrFilter",
    "filter_other_unit": "filterOtherUnit",
    "music_id": "musicId",
    "music_diff": "musicDiff",
    "best_skill_as_leader": "bestSkillAsLeader",
    "skill_order_choose_strategy": "skillOrderChooseStrategy",
    "specific_skill_order": "specificSkillOrder",
    "skill_reference_choose_strategy": "skillReferenceChooseStrategy",
    "keep_after_training_state": "keepAfterTrainingState",
    "multi_live_teammate_power": "multiLiveTeammatePower",
    "multi_live_teammate_score_up": "multiLiveTeammateScoreUp",
    "multi_live_score_up_lower_bound": "multiLiveScoreUpLowerBound",
    "other_score": "otherScore",
    "single_card_configs": "singleCardConfigs",
    "rarity_1_config": "rarity1Config",
    "rarity_2_config": "rarity2Config",
    "rarity_3_config": "rarity3Config",
    "rarity_4_config": "rarity4Config",
    "rarity_birthday_config": "rarityBirthdayConfig",
    "timeout_ms": "timeoutMs",
}

_DROP_OPTIONS = {
    "algorithm",
    "region",
    "user_data_str",
}


def translate_options_for_http(options: dict[str, Any]) -> dict[str, Any]:
    translated: dict[str, Any] = {}
    for key, value in options.items():
        if value is None or key in _DROP_OPTIONS:
            continue
        # 真实活动让服务端按 eventId 推回合，别把 chapterNo 当 turn 传
        if key == "world_bloom_chapter_no" and options.get("event_id") is not None:
            continue
        target = _OPTION_ALIASES.get(key, key)
        if target not in translated:
            translated[target] = value

    if options.get("member") not in (None, 5):
        raise ValueError(f"Allium HTTP 仅支持 5 人组卡，当前 member={options['member']}")
    translated.pop("member", None)

    return translated



def _first(data: dict[str, Any], *keys: str, default: Any = 0) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_http_deck(deck: Any, rank: int = 0) -> dict[str, Any] | None:
    if not isinstance(deck, dict):
        return None

    raw_cards = deck.get("cards") or []
    cards: list[dict[str, Any]] = []
    if isinstance(raw_cards, list):
        for raw_card in raw_cards:
            if not isinstance(raw_card, dict):
                continue
            card_id = _first(raw_card, "card_id", "cardId", "id", default=0)
            card = {
                "card_id": _as_int(card_id),
                "level": _as_int(_first(raw_card, "level", default=0)),
                "skill_level": _as_int(_first(raw_card, "skill_level", "skillLevel", default=0)),
                "skill_score_up": _as_float(
                    _first(raw_card, "skill_score_up", "skillScoreUp", default=0)
                ),
            }
            for source, target, converter in (
                ("power_total", "powerTotal", _as_int),
                ("event_bonus", "eventBonus", _as_float),
                ("master_rank", "masterRank", _as_int),
                ("special_training_status", "specialTrainingStatus", lambda value: value),
                ("default_image", "defaultImage", lambda value: value),
                ("after_training", "afterTraining", lambda value: value),
                ("trained", "trained", lambda value: value),
                ("episodes_read", "episodesRead", lambda value: value),
                ("episode1_read", "episode1Read", lambda value: value),
                ("episode2_read", "episode2Read", lambda value: value),
                ("has_canvas_bonus", "hasCanvasBonus", lambda value: value),
                ("canvas_power", "canvasPower", _as_int),
                ("is_virtual", "isVirtual", lambda value: value),
            ):
                value = _first(raw_card, source, target, default=None)
                if value is not None:
                    card[source] = converter(value)

            if "default_image" not in card and "trained" in card:
                card["default_image"] = "special_training" if card["trained"] else "original"
            if "after_training" not in card and "special_training_status" in card:
                card["after_training"] = str(card["special_training_status"]).strip().lower() in {
                    "done",
                    "special_training",
                    "trained",
                    "after_training",
                }
            episodes_read = card.get("episodes_read")
            if isinstance(episodes_read, list):
                if "episode1_read" not in card:
                    card["episode1_read"] = 1 in episodes_read
                if "episode2_read" not in card:
                    card["episode2_read"] = 2 in episodes_read
            cards.append(card)

    target_value = _as_int(_first(deck, "target_value", "targetValue", "score", default=0))
    event_point_raw = _first(deck, "event_point", "eventPoint", default=None)
    live_score_raw = _first(deck, "live_score", "liveScore", default=None)
    # targetValue 只拿来排序，展示优先用 eventPoint，无活动用 liveScore
    display_score = event_point_raw if event_point_raw is not None else live_score_raw
    if display_score is None:
        display_score = target_value

    return {
        "rank": _as_int(_first(deck, "rank", default=rank), rank),
        "target_value": target_value,
        "score": _as_int(display_score),
        "total_power": _as_int(_first(deck, "total_power", "totalPower", default=0)),
        "event_bonus_rate": _as_float(
            _first(deck, "event_bonus_rate", "eventBonusTotal", "eventBonus", default=0)
        ),
        "multi_live_score_up": _as_float(
            _first(deck, "multi_live_score_up", "multiLiveScoreUp", default=0)
        ),
        "live_score": _as_int(live_score_raw, 0),
        "event_point": _as_int(event_point_raw, 0),
        "cards": cards,
    }


def normalize_http_decks(payload: Any) -> list[dict[str, Any]]:
    data = payload
    if isinstance(data, dict) and isinstance(data.get("result"), dict):
        data = data["result"]
    if isinstance(data, list):
        if data and all(isinstance(item, dict) and isinstance(item.get("result"), dict) for item in data):
            raw_decks = [
                deck
                for item in data
                for deck in (item["result"].get("decks") or [])
            ]
        else:
            raw_decks = data
    elif isinstance(data, dict):
        raw_decks = data.get("decks") or []
    else:
        raw_decks = []

    normalized: list[dict[str, Any]] = []
    if not isinstance(raw_decks, list):
        return normalized
    for index, deck in enumerate(raw_decks, 1):
        item = normalize_http_deck(deck, rank=index)
        if item is not None and item["cards"]:
            normalized.append(item)
    return normalized
