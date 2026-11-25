from __future__ import annotations

from typing import Any, Dict, Optional


def build_header_payload(
    profile,
    userid: str,
    is_private: bool,
    suite_data: Optional[dict] = None,
    suite_raw_data: Optional[dict] = None,
) -> Dict[str, Any]:
    suite_data = suite_data if isinstance(suite_data, dict) else {}
    suite_raw_data = suite_raw_data if isinstance(suite_raw_data, dict) else {}
    return {
        "userid": str(userid),
        "name": profile.name or suite_data.get('name') or suite_raw_data.get('name') or '???',
        "rank": profile.rank or suite_data.get('rank', 0) or suite_raw_data.get('rank', 0),
        "is_private": is_private,
        "user_decks": profile.userDecks or suite_data.get('userDecks', []) or suite_raw_data.get('userDecks', []),
        "special_training": profile.special_training or suite_data.get('special_training', []),
        "user_profile_honors": (
            profile.userProfileHonors
            or suite_data.get('userProfileHonors', [])
            or suite_raw_data.get('userProfileHonors', [])
        ),
        "user_honor_missions": (
            profile.userHonorMissions
            or suite_data.get('userHonorMissions', [])
            or suite_raw_data.get('userHonorMissions', [])
        ),
        "suite_update_time": (
            suite_data.get('upload_time')
            or suite_raw_data.get('upload_time')
            or suite_data.get('updatedAt')
            or suite_raw_data.get('updatedAt')
            or getattr(profile, 'updatedAt', None)
        ),
    }
