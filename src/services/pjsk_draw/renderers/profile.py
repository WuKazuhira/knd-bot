"""个人信息载荷视图。"""


class ProfileView:
    _FIELDS = (
        "name", "rank", "word", "twitterId", "characterId", "characterRank",
        "userDecks", "special_training", "deck_master_ranks",
        "userProfileHonors", "userHonorMissions",
        "highScore", "clear", "full_combo", "full_perfect",
        "mvpCount", "superStarCount", "isNewData",
    )

    @classmethod
    def payload_from_profile(cls, profile) -> dict:
        """把 UserProfile 对象拍平成载荷（供指令侧调用）。"""
        return {field: getattr(profile, field, None) for field in cls._FIELDS}
