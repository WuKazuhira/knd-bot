"""Allium PR #39 HTTP 请求/响应与 KND 旧绘图数据之间的转换。"""

from __future__ import annotations

from typing import Any

# 本地插件字段 -> PR #39 BuildParams 的稳定 camelCase 字段。
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

# 这些字段是旧 Python facade/组卡编排内部控制项，不属于 PR #39 的 BuildParams。
_DROP_OPTIONS = {
    "algorithm",
    "region",
    "user_data_str",
}


def translate_options_for_http(options: dict[str, Any]) -> dict[str, Any]:
    """转换 KND options 为 Allium HTTP BuildParams。

    PR #39 的 JSON 解析器同时接受 camelCase 与 snake_case；因此这里只处理
    KND 特有的旧字段，不强行把整个参数字典重写成另一套方言。
    """
    translated: dict[str, Any] = {}
    for key, value in options.items():
        if value is None or key in _DROP_OPTIONS:
            continue
        target = _OPTION_ALIASES.get(key, key)
        # 后写的兼容字段不能覆盖调用方已经明确设置的新字段。
        if target not in translated:
            translated[target] = value

    # 10000 是 KND 的“任意歌曲/默认歌曲”占位符，不是 PR #39 服务必然载入的
    # 真实 music meta。省略后由 Allium 使用无歌曲 fallback 表，避免 unknown song。
    if translated.get("musicId") == 10000:
        translated.pop("musicId", None)

    # Allium 只支持五人卡组；省略 member 与显式 5 的语义相同。
    if options.get("member") not in (None, 5):
        raise ValueError(f"Allium HTTP 仅支持 5 人组卡，当前 member={options['member']}")
    translated.pop("member", None)

    return translated


_HTTP_TO_LOCAL_ALIASES = {
    "liveType": "live_type",
    "eventId": "event_id",
    "eventType": "event_type",
    "eventUnit": "event_unit",
    "eventAttr": "event_attr",
    "customBonusCharacterIds": "custom_bonus_character_ids",
    "customBonusAttr": "custom_bonus_attr",
    "customBonusCharacterSupportUnits": "custom_bonus_support_units",
    "targetBonusList": "target_bonus_list",
    "worldBloomCharacterId": "world_bloom_character_id",
    "worldBloomEventTurn": "world_bloom_event_turn",
    "worldBloomFinaleTurn": "world_bloom_finale_turn",
    "forcedLeaderCharacterId": "forced_leader_character_id",
    "supportMasterMax": "support_master_max",
    "supportSkillMax": "support_skill_max",
    "fixedCards": "fixed_cards",
    "fixedCharacters": "fixed_characters",
    "excludedCards": "excluded_cards",
    "challengeLiveCharacterId": "challenge_live_character_id",
    "unitFilter": "unit_filter",
    "attrFilter": "attr_filter",
    "filterOtherUnit": "filter_other_unit",
    "musicId": "music_id",
    "musicDiff": "music_diff",
    "bestSkillAsLeader": "best_skill_as_leader",
    "liveSkillOrder": "skill_order_choose_strategy",
    "skillOrderChooseStrategy": "skill_order_choose_strategy",
    "specificSkillOrder": "specific_skill_order",
    "skillReferenceChooseStrategy": "skill_reference_choose_strategy",
    "skillReferenceStrategy": "skill_reference_choose_strategy",
    "keepAfterTrainingState": "keep_after_training_state",
    "multiLiveTeammatePower": "multi_live_teammate_power",
    "multiLiveTeammateScoreUp": "multi_live_teammate_score_up",
    "multiLiveScoreUpLowerBound": "multi_live_score_up_lower_bound",
    "otherScore": "other_score",
    "singleCardConfigs": "single_card_configs",
    "rarity1Config": "rarity_1_config",
    "rarity2Config": "rarity_2_config",
    "rarity3Config": "rarity_3_config",
    "rarity4Config": "rarity_4_config",
    "rarityBirthdayConfig": "rarity_birthday_config",
    "timeoutMs": "timeout_ms",
    "multi_teammate_power": "multi_live_teammate_power",
    "multi_teammate_score_up": "multi_live_teammate_score_up",
    "custom_bonus_character_support_units": "custom_bonus_support_units",
}


def translate_options_for_local(options: dict[str, Any]) -> dict[str, Any]:
    """将 PR #39 的 HTTP 参数兼容转换为旧 Python 引擎字段。"""
    translated: dict[str, Any] = {}
    for key, value in options.items():
        if value is None:
            continue
        target = _HTTP_TO_LOCAL_ALIASES.get(key, key)
        if target not in translated:
            translated[target] = value
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
    """把 PR #39 的 DeckOut 或旧响应中的卡组转换为绘图器格式。"""
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
                "skill_level": _as_int(_first(raw_card, "skill_level", "skillLevel", default=0)),
                "skill_score_up": _as_float(
                    _first(raw_card, "skill_score_up", "skillScoreUp", default=0)
                ),
            }
            for source, target in (
                ("default_image", "defaultImage"),
                ("master_rank", "masterRank"),
                ("episode1_read", "episode1Read"),
                ("episode2_read", "episode2Read"),
            ):
                if source in raw_card:
                    card[source] = raw_card[source]
                elif target in raw_card:
                    card[source] = raw_card[target]
            cards.append(card)

    return {
        "rank": _as_int(_first(deck, "rank", default=rank), rank),
        "score": _as_int(_first(deck, "score", "targetValue", default=0)),
        "total_power": _as_int(_first(deck, "total_power", "totalPower", default=0)),
        "event_bonus_rate": _as_float(
            _first(deck, "event_bonus_rate", "eventBonusTotal", "eventBonus", default=0)
        ),
        "multi_live_score_up": _as_float(
            _first(deck, "multi_live_score_up", "multiLiveScoreUp", default=0)
        ),
        "live_score": _as_int(_first(deck, "live_score", "liveScore", default=0)),
        "event_point": _as_int(_first(deck, "event_point", "eventPoint", default=0)),
        "cards": cards,
    }


def normalize_http_decks(payload: Any) -> list[dict[str, Any]]:
    """提取并转换 PR #39/旧 HTTP 响应中的 decks 列表。"""
    data = payload
    if isinstance(data, dict) and isinstance(data.get("result"), dict):
        data = data["result"]
    if isinstance(data, list):
        # 旧批量 JSON 服务返回 [{"result": {"decks": [...]}, ...}]。
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


def to_http_decks(decks: Any) -> list[dict[str, Any]]:
    """把仓内旧绘图结构转换成 PR #39 的 camelCase DeckOut 列表。"""
    if not isinstance(decks, list):
        return []
    result: list[dict[str, Any]] = []
    for index, deck in enumerate(decks, 1):
        if not isinstance(deck, dict):
            continue
        cards = []
        for raw_card in deck.get("cards") or []:
            if not isinstance(raw_card, dict):
                continue
            card = {
                "cardId": _as_int(_first(raw_card, "card_id", "cardId", "id", default=0)),
                "powerTotal": _as_int(
                    _first(raw_card, "power_total", "powerTotal", "power", default=0)
                ),
                "eventBonus": _as_float(
                    _first(raw_card, "event_bonus", "eventBonus", "event_bonus_rate", default=0)
                ),
                "skillScoreUp": _as_float(
                    _first(raw_card, "skill_score_up", "skillScoreUp", default=0)
                ),
            }
            cards.append(card)
        result.append(
            {
                "rank": _as_int(deck.get("rank", index), index),
                "targetValue": _as_int(_first(deck, "score", "targetValue", default=0)),
                "cards": cards,
                "totalPower": _as_int(_first(deck, "total_power", "totalPower", default=0)),
                "liveScore": _as_int(_first(deck, "live_score", "liveScore", default=0)),
                "eventPoint": _as_int(_first(deck, "event_point", "eventPoint", default=0)),
                "multiLiveScoreUp": _as_float(
                    _first(deck, "multi_live_score_up", "multiLiveScoreUp", default=0)
                ),
                "eventBonusTotal": _as_float(
                    _first(deck, "event_bonus_rate", "eventBonusTotal", default=0)
                ),
            }
        )
    return result
