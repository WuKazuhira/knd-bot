from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.typing import T_RuleChecker, T_State
from ._config import pjskguess, PJSK_GUESS, PJSK_ANSWER, PJSK_MATCHED, GUESS_CARD, GUESS_MUSIC
from ._utils import can_be_guess_answer, aliasToCharaId, matchMusicGuessAnswer


def check_reply() -> T_RuleChecker:
    async def check_args(event: GroupMessageEvent, state: T_State) -> bool:
        answer = event.get_plaintext().strip().lower()
        if not answer or not can_be_guess_answer(answer):
            return False
        for guess in pjskguess.keys():
            if (
                event.group_id in pjskguess[guess] and
                pjskguess[guess][event.group_id].get('isgoing', False)
            ):
                state[PJSK_GUESS] = guess
                state['pjsk_type'] = pjskguess[guess][event.group_id].get('pjsk_type', 0)
                state[PJSK_ANSWER] = answer
                if guess == GUESS_CARD:
                    charaid, name = await aliasToCharaId(answer, event.group_id)
                    if charaid == 0:
                        return False
                    state[PJSK_MATCHED] = (charaid, name)
                    return True
                if guess == GUESS_MUSIC:
                    matched = await matchMusicGuessAnswer(answer, state['pjsk_type'])
                    if not matched:
                        return False
                    state[PJSK_MATCHED] = matched
                    return True
        return False
    return check_args


def check_rule() -> T_RuleChecker:
    async def check_args(event: GroupMessageEvent, state: T_State) -> bool:
        for guess in pjskguess.keys():
            if (
                event.group_id in pjskguess[guess] and
                pjskguess[guess][event.group_id].get('isgoing', False)
            ):
                state[PJSK_GUESS] = guess
                state['pjsk_type'] = pjskguess[guess][event.group_id].get('pjsk_type', 0)
                return True
        return False
    return check_args