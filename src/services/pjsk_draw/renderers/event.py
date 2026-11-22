"""活动详情载荷视图。"""


class EventInfoView:
    _FIELDS = (
        "id", "eventType", "name", "assetbundleName", "startAt",
        "aggregateAtorin", "aggregateAt", "unit", "bonusechara",
        "bonuseattr", "music", "cards",
    )

    @classmethod
    def payload_from_event(cls, event) -> dict:
        """把 EventInfo 对象拍平成载荷（供指令侧调用）。"""
        return {field: getattr(event, field, None) for field in cls._FIELDS}
