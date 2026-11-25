class EventInfoView:
    _FIELDS = (
        "id", "eventType", "name", "assetbundleName", "startAt",
        "aggregateAtorin", "aggregateAt", "unit", "bonusechara",
        "bonuseattr", "music", "cards",
    )

    @classmethod
    def payload_from_event(cls, event) -> dict:
        return {field: getattr(event, field, None) for field in cls._FIELDS}
