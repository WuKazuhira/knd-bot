"""卡牌详情载荷视图。"""


class CardInfoView:
    _FIELDS = (
        "config", "pjsk_type", "id", "characterId", "costume3dId", "skillId",
        "unit", "cardRarityType", "attr", "isLimited", "cardParameters", "releaseAt",
        "charaName", "prefix", "gachaPhrase", "cardSkillName", "cardSkillDes", "assets",
    )

    @classmethod
    def payload_from_card(cls, card) -> dict:
        """把 CardInfo 对象拍平成载荷（供指令侧调用）。"""
        payload = {field: getattr(card, field, None) for field in cls._FIELDS}
        for name in ("event", "music", "gacha"):
            sub = getattr(card, name, None)
            payload[name] = dict(vars(sub)) if sub is not None else {}
        return payload
