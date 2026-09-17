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

    # 上游 allium-deck/server 会为 10000 补齐 omakase music meta，保留该占位符
    # 以维持 KND 默认歌曲语义。
    # Allium 只支持五人卡组；省略 member 与显式 5 的语义相同。
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

            # 兼容只返回 trained / episodesRead 的服务实现。
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
