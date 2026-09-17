import datetime
import json
import random
import time
from typing import Dict, List, Optional, Union

import pytz
import requests
import yaml

from services import logger
from services.db_context import db

from ._common_utils import callapi
from ._config import SERVER_CONFIG, SUITE_API_KEYS, api_base_url_list, data_path
from ._event_utils import analysisunitid
from ._utils import async_load_master_data, get_server_data_path, get_userid_preprocess, load_master_data


def _normalize_suite_payload(data: Dict) -> Dict:
    """合并 Suite 常见包装格式，并把单个当前卡组统一成 userDecks。"""
    if not isinstance(data, dict):
        return {}

    normalized = dict(data)
    user_block = data.get("user") if isinstance(data.get("user"), dict) else {}
    sources = (
        data.get("userGamedata"),
        user_block.get("userGamedata"),
        user_block,
    )
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            if key not in normalized or normalized[key] in (None, {}, []):
                normalized[key] = value

    user_decks = normalized.get("userDecks")
    user_deck = normalized.get("userDeck")
    if isinstance(user_deck, dict) and (not isinstance(user_decks, list) or not user_decks):
        normalized["userDecks"] = [user_deck]

    return normalized


class PjskGuessRank(db.Model):
    __tablename__ = "pjsk_guess_rank"
    __table_args__ = {'extend_existing': True}

    id = db.Column(db.Integer(), primary_key=True)
    user_qq = db.Column(db.BigInteger(), nullable=False)
    group_id = db.Column(db.BigInteger(), nullable=False)
    game_type = db.Column(db.TEXT(), nullable=False)
    total_count = db.Column(db.JSON(), default=dict, nullable=False)
    daily_count = db.Column(db.Integer(), default=0, nullable=False)
    pjsk_type = db.Column(db.Integer(), default=0, nullable=False)
    last_guess_time = db.Column(db.DateTime(), nullable=False, default=datetime.datetime.min)

    @classmethod
    async def get_rank(cls, group_id: int, game_type: str, guess_diff: Optional[int] = None, pjsk_type: int = 0):
        """
        说明：
            获取某群某类型游戏的用户排行榜
        参数：
            :param group_id: 群号
            :param game_type: 游戏类型
            :param guess_diff: 游戏难度
        """
        user_ls = []
        count_ls = []
        query = cls.query.where(
            (cls.group_id == group_id) & (cls.game_type == game_type) & (cls.pjsk_type == pjsk_type)
        )
        if guess_diff is not None:
            for user in await query.gino.all():
                if count := user.total_count.get(str(guess_diff), 0):
                    user_ls.append(user.user_qq)
                    count_ls.append(count)
        else:
            for user in await query.gino.all():
                total = user.total_count
                count = sum(total.get(i) for i in total.keys())
                user_ls.append(user.user_qq)
                count_ls.append(count)
        return user_ls, count_ls

    @classmethod
    async def add_count(
        cls, user_qq: int, group_id: int, game_type: str, guess_diff: int, tips_used: bool = False, pjsk_type: int = 0
    ):
        """
        说明：
            添加次数
        参数：
            :param user_qq: qq号
            :param group_id: 群号
            :param game_type: 游戏类型
            :param guess_diff: 游戏难度
            :param pjsk_type: pjsk服务器类型
        """
        user = await cls._get_user_info(user_qq, group_id, game_type, pjsk_type=pjsk_type)
        guess_diff = str(guess_diff)
        total_count = user.total_count
        total_count[guess_diff] = total_count.get(guess_diff, 0) + 1
        lastdate = user.last_guess_time.date()
        nowdate = datetime.datetime.now().date()
        daily_count = 1 if nowdate > lastdate else user.daily_count+1
        if tips_used:
            await user.update(
                daily_count=daily_count,
                last_guess_time=datetime.datetime.now()
            ).apply()
        else:
            await user.update(
                total_count=total_count,
                daily_count=daily_count,
                last_guess_time=datetime.datetime.now()
            ).apply()

    @classmethod
    async def _get_user_info(cls, user_qq: int, group_id: int, game_type: str, pjsk_type: int = 0):
        """
        说明：
            获取用户信息
        参数：
            :param user_qq: qq号
            :param group_id: 群号
            :param game_type: 游戏类型
        """
        user = await cls.query.where(
            (cls.user_qq == user_qq) & (cls.group_id == group_id) & 
            (cls.game_type == game_type) & (cls.pjsk_type == pjsk_type)
        ).gino.first()
        return user or await cls.create(
            user_qq=user_qq,
            group_id=group_id,
            game_type=game_type,
            pjsk_type=pjsk_type,
            total_count={},
            daily_count=0,
            last_guess_time=datetime.datetime.now()
        )

    @classmethod
    async def check_today_count(cls, user_qq: int, group_id: int) -> bool:
        """
        说明：
            检查用户是否达到游戏获取金币上限
        参数：
            :param user_qq: qq号
            :param group_id: 群号
        """
        users = await cls.query.where((cls.user_qq == user_qq) & (cls.group_id == group_id)).gino.all()
        now_date = datetime.datetime.now().date()
        users = list(filter(lambda x: x.last_guess_time.date() >= now_date, users))
        daily_count = sum(user.daily_count for user in users)
        return daily_count >= 10



class PjskSongsAlias(db.Model):
    __tablename__ = "pjsk_songs_alias"
    __table_args__ = {'extend_existing': True}
    id = db.Column(db.Integer(), primary_key=True)
    song_id = db.Column(db.Integer(), nullable=False)
    song_alias = db.Column(db.Unicode(), nullable=False)  # 包括曲名、翻译、玩家起的昵称
    user_qq = db.Column(db.BigInteger(), nullable=False)
    group_id = db.Column(db.BigInteger(), nullable=False)
    join_time = db.Column(db.DateTime(), nullable=False)
    is_pass = db.Column(db.Boolean(), default=False)
    _idx1 = db.Index("pjsk_alias_idx1", "song_id", "song_alias", unique=True)

    @classmethod
    async def add_alias(
            cls, song_id: int, alias: str, user_qq: int, group_id: int,
            join_time: datetime.datetime, is_pass: bool
    ) -> bool:
        """
        说明：
            添加别名
        参数：
            :param song_id: 歌曲id
            :param user_qq: qq号
            :param group_id: 群号
            :param alias: 别名
            :param join_time: 添加时间
        """
        if not await cls.check_alias_exists(alias):
            await cls.create(
                song_id=song_id, user_qq=user_qq, group_id=group_id,
                song_alias=alias, is_pass=is_pass, join_time=join_time
            )
            return True
        return False

    @classmethod
    async def delete_alias(cls, alias: str) -> bool:
        """
        说明：
            删除别名
        参数：
            :param alias: 别名
        """
        if await cls.check_alias_exists(alias):
            query = cls.query.where(cls.song_alias == alias).with_for_update()
            query = await query.gino.first()
            await query.delete()
            return True
        return False

    @classmethod
    async def check_alias_exists(cls, alias: str) -> bool:
        """
        说明：
            检测别名是否已存在
        参数：
            :param alias: 别名
        """
        query = await cls.select("song_alias").gino.all()
        query = [res[0] for res in query]
        if alias in query:
            return True
        return False

    @classmethod
    async def check_id_exists(cls, song_id: int) -> bool:
        """
        说明：
            检测歌曲id是否已存在
        参数：
            :param name: 主名
        """
        query = await cls.select("song_id").gino.all()
        query = set(res[0] for res in query)
        if song_id in query:
            return True
        return False

    @classmethod
    async def query_alias(cls, song_id: int) -> List[str]:
        """
        说明：
            查找对应歌曲id的所有别称
        参数：
            :param song_id: 歌曲id
        """
        query = await cls.select("song_alias").where(cls.song_id == song_id).gino.all()
        query = [res.song_alias for res in query]
        return query

    @classmethod
    async def query_sid(cls, song_alias: str) -> int:
        """
        说明：
            查找对应别称的歌曲id
        参数：
            :param song_alias: 别名
        """
        query = await cls.select("song_id").where(cls.song_alias == song_alias).gino.scalar()
        return query

    @classmethod
    async def query_alias_pairs(cls) -> List[tuple[int, str]]:
        """只读取歌曲 ID 与别名，用于本地模糊匹配候选。"""
        query = await cls.select("song_id", "song_alias").gino.all()
        return [(int(res.song_id), str(res.song_alias)) for res in query]


class PjskBind(db.Model):
    __tablename__ = "pjsk_bind"
    __table_args__ = {'extend_existing': True}

    id = db.Column(db.Integer(), primary_key=True)
    user_qq = db.Column(db.BigInteger(), nullable=False)
    pjsk_uid = db.Column(db.BigInteger(), nullable=False)
    pjsk_type = db.Column(db.Integer(), default=0, nullable=False)
    isprivate = db.Column(db.Boolean(), default=False, nullable=False)
    _rela = db.Index("pjsk_rela", "user_qq", "pjsk_type", unique=True)

    @classmethod
    async def set_look(
            cls, user_qq: int, isprivate: bool, pjsk_type: int = 0,
    ):
        """
        说明：
            添加绑定信息
        参数：
            :param user_qq: qq号
            :param isprivate: 是否隐藏信息
            :param pjsk_type: pjsk服务器类型(0:日服，1:台服，2:国际服)
        """
        try:
            user = (
                await cls.query.where((cls.user_qq == user_qq) & (cls.pjsk_type == pjsk_type))
                    .with_for_update()
                    .gino.first()
            )
            if user:
                await user.update(isprivate=isprivate).apply()
                return True
        except Exception as e:
            logger.info(f"User {user_qq} 修改pjsk信息公开设定时发生错误 {type(e)}：{e}")
        return False

    @classmethod
    async def add_bind(
            cls, user_qq: int, pjsk_uid: int, pjsk_type: int = 0, isprivate: bool = False
    ) -> bool:
        """
        说明：
            添加绑定信息
        参数：
            :param user_qq: qq号
            :param pjsk_uid: pjsk用户id号
            :param pjsk_type: pjsk服务器类型(0:日服，1:台服，2:国际服)
        """
        try:
            user = (
                await cls.query.where((cls.user_qq == user_qq) & (cls.pjsk_type == pjsk_type))
                    .with_for_update()
                    .gino.first()
            )
            if user:
                await user.update(pjsk_uid=pjsk_uid).apply()
            else:
                await cls.create(
                    user_qq=user_qq,
                    pjsk_uid=pjsk_uid,
                    pjsk_type=pjsk_type,
                    isprivate=isprivate
                )
            return True
        except Exception as e:
            logger.info(f"User {user_qq} 添加pjsk绑定信息时发生错误 {type(e)}：{e}")
            return False

    @classmethod
    async def del_bind(cls, user_qq: int, pjsk_type: int = 0) -> bool:
        """
        说明：
            删除绑定信息
        参数：
            :param user_qq: qq号
            :param pjsk_type: pjsk服务器类型(0:日服，1:台服，2:国际服)
        """
        status = await cls.delete.where(
            (cls.user_qq == user_qq) & (cls.pjsk_type == pjsk_type)
        ).gino.status()
        return status != "DELETE 0"

    @classmethod
    async def check_exists(cls, user_qq: int, pjsk_type: int = 0) -> bool:
        """
        说明：
            检测用户是否已存在绑定信息
        参数：
            :param user_qq: qq号:
            :param pjsk_type: pjsk服务器类型(0:日服，1:台服，2:国际服)
        """
        q = await cls.query.where(
            (cls.user_qq == user_qq) & (cls.pjsk_type == pjsk_type)
        ).with_for_update().gino.first()
        if q:
            return True
        return False

    @classmethod
    async def get_user_bind(cls, user_qq: int, pjsk_type: int = 0):
        """
        说明：
            获取用户的绑定信息
        参数：
            :param user_qq: qq号
            :param pjsk_type: pjsk服务器类型(0:日服，1:台服，2:国际服)
        """
        query = await cls.query.where(
            (cls.user_qq == user_qq) & (cls.pjsk_type == pjsk_type)
        ).gino.first()
        if query:
            return query.pjsk_uid, query.isprivate
        return None, False


class UserProfile(object):
    def __init__(self):
        self.name = ''
        self.rank = 0
        self.userid = ''
        self.twitterId = ''
        self.word = ''
        self.userDecks = [0, 0, 0, 0, 0]
        self.special_training = [False, False, False, False, False]
        self.deck_master_ranks = [0, 0, 0, 0, 0]
        self.full_perfect = [0, 0, 0, 0, 0, 0]
        self.full_combo = [0, 0, 0, 0, 0, 0]
        self.clear = [0, 0, 0, 0, 0, 0]
        self.mvpCount = 0
        self.superStarCount = 0
        self.userProfileHonors = {}
        self.userHonorMissions = []
        self.characterRank = {}
        self.characterId = 0
        self.highScore = 0
        self.masterscore = {}
        self.expertscore = {}
        self.musicResult = {}
        self.isNewData = False
        self.updatedAt = 0
        for i in range(26, 38):
            self.masterscore[i] = [0, 0, 0, 0]
        for i in range(21, 32):
            self.expertscore[i] = [0, 0, 0, 0]

    async def getprofile(
        self,
        userid: str,
        query_type: str = 'unknown',
        data: Optional[Dict] = None,
        is_force_update: bool = False,
        pjsk_type: int = 0
    ):
        from services.log import logger
        
        if data is None:
            from ._gameapi import GameApiConfig, request_gameapi
            config = GameApiConfig(pjsk_type)
            api_url = config.profile_api_url
            if not api_url:
                raise ValueError(f"Server {config.server_name} does not support profile queries.")
            data = await request_gameapi(api_url.format(uid=userid), method='GET', data_type='json')

        # 判断数据格式
        self.isNewData = 'totalPower' in data
        self.isSuiteData = False
        self.userid = userid
        self.updatedAt = data.get("updatedAt", 0) or data.get("now", 0)
        self.twitterId = data.get('userProfile', {}).get('twitterId', "")
        self.word = data.get('userProfile', {}).get('word', "")
        user_block = data.get('user', {}) if isinstance(data.get('user', {}), dict) else {}
        gamedata_block = user_block.get('userGamedata', {}) if isinstance(user_block.get('userGamedata', {}), dict) else {}
        deck_block = data.get('userDeck', {}) if isinstance(data.get('userDeck', {}), dict) else {}
        decks_block = data.get('userDecks', []) if isinstance(data.get('userDecks', []), list) else []
        cards_block = data.get('userCards', []) if isinstance(data.get('userCards', []), list) else []
        honors_block = data.get('userProfileHonors', []) if isinstance(data.get('userProfileHonors', []), list) else []
        honor_missions_block = data.get('userHonorMissions', []) if isinstance(data.get('userHonorMissions', []), list) else []

        # 挑战最高分
        try:
            if self.isNewData:
                self.characterId = data['userChallengeLiveSoloResult']['characterId']
                self.highScore = data['userChallengeLiveSoloResult']['highScore']
            else:
                for i in data['userChallengeLiveSoloResults']:
                    if i['highScore'] > self.highScore:
                        self.characterId = i['characterId']
                        self.highScore = i['highScore']
        except:
            pass

        self.characterRank = data.get('userCharacters') or user_block.get('userCharacters') or gamedata_block.get('userCharacters') or []
        self.userProfileHonors = honors_block or user_block.get('userProfileHonors') or gamedata_block.get('userProfileHonors') or []
        self.userHonorMissions = data.get('userHonorMissions', []) or honor_missions_block or user_block.get('userHonorMissions', []) or gamedata_block.get('userHonorMissions', [])

        if self.isNewData:
            self.name = data.get('user', {}).get('name') or gamedata_block.get('name', '')
            self.rank = data.get('user', {}).get('rank') or gamedata_block.get('rank', 0)
            count_data = data.get('userMusicDifficultyClearCount', [])
            try:
                self.full_perfect = [count_data[i].get('allPerfect', 0) for i in range(min(6, len(count_data)))]
            except:
                self.full_perfect = ['无数据' for i in range(6)]
            try:
                self.full_combo = [count_data[i].get('fullCombo', 0) for i in range(min(6, len(count_data)))]
                self.clear = [count_data[i].get('liveClear', 0) for i in range(min(6, len(count_data)))]
            except:
                pass
            self.mvpCount = data.get('userMultiLiveTopScoreCount', {}).get('mvp', 0)
            self.superStarCount = data.get('userMultiLiveTopScoreCount', {}).get('superStar', 0)
        else:
            self.name = user_block.get('userGamedata', {}).get('name', gamedata_block.get('name', ''))
            self.rank = user_block.get('userGamedata', {}).get('rank', gamedata_block.get('rank', 0))

        for i in range(0, 5):
            if self.isNewData and isinstance(deck_block, dict):
                self.userDecks[i] = deck_block.get(f'member{i + 1}', 0)
            else:
                decknum = user_block.get('userGamedata', {}).get('deck', gamedata_block.get('deck', 1))
                for deck in decks_block:
                    if deck.get('deckId') == decknum:
                        self.userDecks[i] = deck.get(f'member{i + 1}', 0)
                        break
            for userCards in cards_block:
                if userCards.get('cardId') != self.userDecks[i]:
                    continue
                if userCards.get('defaultImage') == "special_training":
                    self.special_training[i] = True
                self.deck_master_ranks[i] = userCards.get('masterRank', 0)

    async def getsuite(
        self,
        userid: str,
        pjsk_type: int = 0
    ):
        """通过 Suite API 获取用户数据，供 b30/rop 使用"""
        from services.log import logger

        from ._gameapi import GameApiConfig, request_gameapi
        
        config = GameApiConfig(pjsk_type)
        if not config.suite_api_url:
            raise ValueError(f"Server {config.server_name} does not support suite API.")
        
        # 从配置读取完整字段列表，回退到最小集合
        query_params = SUITE_API_KEYS or ['userGamedata', 'userMusicResults', 'userProfileHonors', 'userDecks', 'userCards', 'upload_time']
        suite_url = config.suite_api_url.format(uid=userid) + '?mode=latest&key=' + ','.join(query_params)
        logger.debug(f"[getsuite] 请求 Suite API: {suite_url}")
        
        data = await request_gameapi(suite_url, method='GET', data_type='json')
        
        if isinstance(data, list):
            raise ValueError("Suite API 返回了列表而不是字典")
        
        gamedata = data.get('userGamedata', {})
        if not isinstance(gamedata, dict):
            gamedata = {}
        suite_data = _normalize_suite_payload(data)

        # 兼容两种返回：
        # 1) 直接扁平在根对象上
        # 2) 包在 userGamedata 里
        root_name = data.get('name') or suite_data.get('name', '')
        root_rank = data.get('rank') or suite_data.get('rank', 0)
        self.name = root_name
        self.rank = root_rank
        self.isSuiteData = True
        self.isNewData = True
        self.userid = userid
        self.suite_raw_data = data
        self.suite_data = suite_data
        
        logger.debug(f"[getsuite] 用户: {self.name}, 等级: {self.rank}")
        
        # 音乐统计数据
        count_data = data.get('userMusicDifficultyClearCount', []) or suite_data.get('userMusicDifficultyClearCount', [])
        try:
            self.full_perfect = [count_data[i].get('allPerfect', 0) for i in range(min(6, len(count_data)))]
        except:
            self.full_perfect = ['无数据' for i in range(6)]
        try:
            self.full_combo = [count_data[i].get('fullCombo', 0) for i in range(min(6, len(count_data)))]
            self.clear = [count_data[i].get('liveClear', 0) for i in range(min(6, len(count_data)))]
        except:
            pass
        self.mvpCount = data.get('userMultiLiveTopScoreCount', {}).get('mvp', 0) or suite_data.get('userMultiLiveTopScoreCount', {}).get('mvp', 0)
        self.superStarCount = data.get('userMultiLiveTopScoreCount', {}).get('superStar', 0) or suite_data.get('userMultiLiveTopScoreCount', {}).get('superStar', 0)
        self.characterRank = data.get('userCharacters') or suite_data.get('userCharacters') or []
        # Suite API 返回的值优先，但不能覆盖 getprofile() 已经设好的值（self.userProfileHonors 可能已有数据）
        self.userProfileHonors = data.get('userProfileHonors') or suite_data.get('userProfileHonors') or self.userProfileHonors or []
        self.userHonorMissions = data.get('userHonorMissions', []) or suite_data.get('userHonorMissions', []) or self.userHonorMissions or []
        
        # 挑战最高分
        try:
            for result in data.get('userChallengeLiveSoloResults', []) or suite_data.get('userChallengeLiveSoloResults', []):
                if result.get('highScore', 0) > self.highScore:
                    self.characterId = result.get('characterId', 0)
                    self.highScore = result.get('highScore', 0)
        except:
            pass
        
        # 处理用户卡组
        decknum = gamedata.get('deck', suite_data.get('deck', 1))
        user_decks = data.get('userDecks', []) or suite_data.get('userDecks', [])
        if isinstance(suite_data.get('userDeck'), dict) and not data.get('userDecks'):
            decknum = suite_data['userDeck'].get('deckId', decknum)
        user_cards = data.get('userCards', []) or suite_data.get('userCards', [])
        for i in range(0, 5):
            for deck in user_decks:
                if deck.get('deckId') == decknum:
                    self.userDecks[i] = deck.get(f'member{i + 1}', 0)
                    break
            for userCards in user_cards:
                if userCards.get('cardId') == self.userDecks[i]:
                    if userCards.get('defaultImage') == "special_training":
                        self.special_training[i] = True
                    self.deck_master_ranks[i] = userCards.get('masterRank', 0)
                    break
        
        # 计算 masterscore / expertscore（rop 需要）
        musicDifficulties = await async_load_master_data('musicDifficulties.json', pjsk_type)
        if isinstance(musicDifficulties, dict):
            musicDifficulties = list(musicDifficulties.values())
        
        # 先统计每首歌每个难度的总数
        for diff in musicDifficulties:
            if diff.get('musicDifficulty') == 'master':
                level = diff.get('playLevel', 0)
                if level in self.masterscore:
                    self.masterscore[level][3] += 1
            elif diff.get('musicDifficulty') == 'expert':
                level = diff.get('playLevel', 0)
                if level in self.expertscore:
                    self.expertscore[level][3] += 1
        
        # 再统计每首歌每个难度的 AP/FC/clear 数
        # userMusicResults 中同一首歌同一难度可能有多条（solo/multi），取最好成绩
        diff_index = {
            'easy': 0,
            'normal': 1,
            'hard': 2,
            'expert': 3,
            'master': 4,
            'append': 5,
        }
        for diff in musicDifficulties:
            music_id = diff.get('musicId')
            idx = diff_index.get(str(diff.get('musicDifficulty', '')).lower())
            if music_id is not None and idx is not None:
                self.musicResult.setdefault(music_id, [0, 0, 0, 0, 0, 0])

        best_results = {}
        music_results = data.get('userMusicResults', []) or suite_data.get('userMusicResults', [])
        for music in music_results:
            music_id = music.get('musicId')
            diff_type = (music.get('musicDifficultyType') or music.get('musicDifficulty') or '').lower()
            key = (music_id, diff_type)
            play_result = str(music.get('playResult', '')).replace('-', '_').replace(' ', '_').lower()
            # 取最好成绩：full_perfect > full_combo > clear，兼容 snake_case/camelCase
            result_rank = {
                'full_perfect': 3,
                'fullperfect': 3,
                'all_perfect': 3,
                'allperfect': 3,
                'full_combo': 2,
                'fullcombo': 2,
                'clear': 1,
                'live_clear': 1,
                'liveclear': 1,
            }.get(play_result, 0)
            if key not in best_results or result_rank > best_results[key]:
                best_results[key] = result_rank
        
        for (music_id, diff_type), result_rank in best_results.items():
            idx = diff_index.get(str(diff_type).lower())
            if music_id is not None and idx is not None:
                self.musicResult.setdefault(music_id, [0, 0, 0, 0, 0, 0])[idx] = result_rank
            # 找到对应的难度等级
            for diff in musicDifficulties:
                if diff.get('musicId') == music_id and diff.get('musicDifficulty') == diff_type:
                    level = diff.get('playLevel', 0)
                    if diff_type == 'master' and level in self.masterscore:
                        if result_rank >= 3:
                            self.masterscore[level][0] += 1  # AP
                            self.masterscore[level][1] += 1  # FC
                            self.masterscore[level][2] += 1  # clear
                        elif result_rank >= 2:
                            self.masterscore[level][1] += 1  # FC
                            self.masterscore[level][2] += 1  # clear
                        elif result_rank >= 1:
                            self.masterscore[level][2] += 1  # clear
                    elif diff_type == 'expert' and level in self.expertscore:
                        if result_rank >= 3:
                            self.expertscore[level][0] += 1
                            self.expertscore[level][1] += 1
                            self.expertscore[level][2] += 1
                        elif result_rank >= 2:
                            self.expertscore[level][1] += 1
                            self.expertscore[level][2] += 1
                        elif result_rank >= 1:
                            self.expertscore[level][2] += 1
                    break
        
        return data


class MusicInfo(object):

    def __init__(self):
        self.id = 0
        self.title = ''
        self.lyricist = ''
        self.composer = ''
        self.arranger = ''
        self.publishedAt = 0
        self.hot = 0
        self.hotAdjust = 0
        self.length = 0
        self.fullPerfectRate = [0, 0, 0, 0, 0]
        self.fullComboRate = [0, 0, 0, 0, 0]
        self.clearRate = [0, 0, 0, 0, 0]
        self.playLevel = [0, 0, 0, 0, 0]
        self.noteCount = [0, 0, 0, 0, 0]
        self.playLevelAdjust = [0, 0, 0, 0, 0]
        self.fullComboAdjust = [0, 0, 0, 0, 0]
        self.fullPerfectAdjust = [0, 0, 0, 0, 0]
        self.fillerSec = 0
        self.categories = []

class EventInfo(object):

    def __init__(self):
        self.id = 0
        self.eventType = ''
        self.name = ''
        self.assetbundleName = ''
        self.startAt = ''
        self.aggregateAtorin = 0
        self.aggregateAt = ''
        self.unit = ''
        self.bonusechara = []
        self.bonuseattr = ''
        self.music = 0
        self.cards = []

    def getevent(self, eventid, pjsk_type: int = 0):
        data = load_master_data('events.json', pjsk_type)
        eventCards = load_master_data('eventCards.json', pjsk_type)
        eventDeckBonuses = load_master_data('eventDeckBonuses.json', pjsk_type)
        if isinstance(data, dict):
            data = list(data.values())
        if isinstance(eventCards, dict):
            eventCards = list(eventCards.values())
        if isinstance(eventDeckBonuses, dict):
            eventDeckBonuses = list(eventDeckBonuses.values())

        for events in data:
            if not isinstance(events, dict):
                continue
            if eventid == events['id']:
                self.id = events['id']
                self.eventType = events['eventType']
                self.name = events['name']
                self.assetbundleName = events['assetbundleName']
                self.startAt = datetime.datetime.fromtimestamp(
                    events['startAt'] / 1000, pytz.timezone('Asia/Shanghai')
                ).strftime('%Y/%m/%d %H:%M:%S')
                self.aggregateAtorin = events['aggregateAt']
                self.aggregateAt = datetime.datetime.fromtimestamp(
                    events['aggregateAt'] / 1000 + 1, pytz.timezone('Asia/Shanghai')
                ).strftime('%Y/%m/%d %H:%M:%S')
                try:
                    self.unit = events['unit']
                except:
                    pass
                break
        if self.id == 0:
            return False
        for cards in eventCards:
            if not isinstance(cards, dict):
                continue
            if cards['eventId'] == self.id:
                self.cards.append(cards['cardId'])
        for bonuse in eventDeckBonuses:
            if not isinstance(bonuse, dict):
                continue
            if bonuse['eventId'] == self.id:
                try:
                    self.bonuseattr = bonuse['cardAttr']
                    self.bonusechara.append(bonuse['gameCharacterUnitId'])
                except:
                    pass
        return True


class GachaInfo(object):
    def __init__(self):
        self.id: int = 0
        self.gachaType: str = ''
        self.gachaCardRarityRateGroupId: int = 0
        self.name: str = ''
        self.assetbundleName: str = ''
        self.startAt: str = ''
        self.endAt: str = ''


def cardskill(skillid, skills, description=None):
    if isinstance(skills, dict):
        skills = list(skills.values())
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        if skill['id'] == skillid:
            if description is None:
                description = skill['description']
            count = description.count('{{')
            for i in range(0, count):
                start = description.find('{{')
                end = description.find('}}', start)
                if start < 0 or end < 0:
                    break
                placeholder = description[start:end + 2]
                para = description[start + 2:end].split(';')
                if len(para) < 2:
                    description = description.replace(placeholder, '', 1)
                    continue

                effect_ids = []
                for raw_id in para[0].split(','):
                    try:
                        effect_ids.append(int(raw_id.strip()))
                    except (TypeError, ValueError):
                        continue
                if not effect_ids:
                    description = description.replace(placeholder, '', 1)
                    continue

                skill_effects = skill['skillEffects']
                if isinstance(skill_effects, dict):
                    skill_effects = list(skill_effects.values())

                # 兼容 detail 为列表的情况
                def get_val(item, key, index):
                    if isinstance(item, dict):
                        return item.get(key, 0)
                    elif isinstance(item, (list, tuple)) and len(item) > index:
                        return item[index]
                    return 0

                replace_parts = []
                for effect in skill_effects:
                    if isinstance(effect, dict):
                        e_id = effect.get('id')
                        detail = effect.get('skillEffectDetails', [])
                    elif isinstance(effect, (list, tuple)) and len(effect) >= 1:
                        e_id = effect[0]
                        detail = effect[1] if len(effect) > 1 else []
                    else:
                        continue

                    if e_id not in effect_ids:
                        continue

                    if isinstance(detail, dict):
                        detail = list(detail.values())
                    elif detail is None:
                        detail = []

                    if para[1] == 'd':
                        # activateEffectDuration 索引通常在字典中是确定的，列表可能需要探索，假设为 0 或 1
                        effect_replace = '/'.join(str(get_val(i, "activateEffectDuration", 1)) for i in detail)
                    elif para[1] == 'e':
                        enhance = get_val(effect, 'skillEnhance', {})
                        effect_replace = str(enhance.get('activateEffectValue', 0) if isinstance(enhance, dict) else 0)
                    elif para[1] == 'm':
                        enhance = get_val(effect, 'skillEnhance', {})
                        e_val = enhance.get('activateEffectValue', 0) if isinstance(enhance, dict) else 0
                        effect_replace = '/'.join(
                            str(get_val(i, "activateEffectValue", 0) + 5 * e_val) for i in detail
                        )
                    else:
                        effect_replace = '/'.join(str(get_val(i, "activateEffectValue", 0)) for i in detail)

                    if effect_replace:
                        replace_parts.extend(effect_replace.split('/'))

                if replace_parts:
                    # 全等级 / 多效果取值相同则折叠，多个不同取值保留斜杠分隔
                    replace = replace_parts[0] if len(set(replace_parts)) == 1 else '/'.join(replace_parts)
                else:
                    replace = ''
                description = description.replace(placeholder, replace, 1)
            return description
    return ''


class CardInfo(object):
    def __init__(self, config: Optional[Dict] = None):
        self.config: Dict[str, bool] = (  # 基础配置
            config if config else {
                'event': True,  # 展示图是否展示出场活动
                'music': True,  # 展示图是否展示活动歌曲
                'gacha': True,  # 展示图是否展示来源卡池
            }
        )
        self.pjsk_type: int = 0  # pjsk服务器类型
        self.id: int = 0  # 卡面id
        self.characterId: int = 0  # 角色id
        self.costume3dId: int = 0  # 衣装id
        self.skillId: int = 0  # 技能id

        self.unit: str = 'none'  # 所属组合
        self.cardRarityType: str = ''  # 卡面星数
        self.attr: str = ''  # 卡面属性
        self.isLimited: bool = False  # 卡面是否限定
        self.cardParameters: Dict[str, int] = {}  # 卡面综合力
        self.releaseAt: str = ''  # 发布时间

        self.charaName: str = ''  # 角色名称(仅日文)
        self.prefix: str = ''  # 卡面名称(仅日文)
        self.gachaPhrase: Dict[str, str] = {}  # 招募语(含中日文显示)
        self.cardSkillName: Dict[str, str] = {}  # 技能名称(含中日文显示)
        self.cardSkillDes: Dict[str, str] = {}  # 技能效果(含中日文显示)

        self.event: EventInfo = EventInfo()  # 登场活动(如果有的话)
        self.music: MusicInfo = MusicInfo()  # 活动歌曲(如果有的话)
        self.gacha: GachaInfo = GachaInfo()  # 来源卡池(如果有的话)

        # 卡面所需图片资源
        self.assets: Dict[str, Union[str, Dict[str, List[str]]]] = {
            'card': '',  # 卡图
            'costume': {  # 附带衣装
                'hair': [],  # 发型
                'head': [],  # 发饰
                'body': []  # 服装
            },
        }

    def _get_music_info(self, pjsk_type: int = 0):
        if self.event.id == 0:
            # 活动 ID
            event_cards = load_master_data('eventCards.json', pjsk_type)
            if isinstance(event_cards, dict):
                event_cards = list(event_cards.values())
            for each in event_cards:
                if not isinstance(each, dict):
                    continue
                if each["cardId"] == self.id:
                    self.event.id = each["eventId"]
                    break
        if self.event.id == 0:
            raise Exception("卡面无对应活动")
        # 获取活动歌曲id
        event_musics = load_master_data('eventMusics.json', pjsk_type)
        if isinstance(event_musics, dict):
            event_musics = list(event_musics.values())
        for each in event_musics:
            if not isinstance(each, dict):
                continue
            if each["eventId"] == self.event.id:
                self.event.music = each["musicId"]
                break
        # 获取活动歌曲信息
        if self.event.music == 0:
            raise Exception("活动无对应歌曲")
        musics = load_master_data('musics.json', pjsk_type)
        if isinstance(musics, dict):
            musics = list(musics.values())
        for each_music in musics:
            if not isinstance(each_music, dict):
                continue
            if each_music["id"] == self.event.music:
                self.music.id = each_music["id"]
                self.music.title = each_music["title"]
                self.music.lyricist = each_music['lyricist']
                self.music.composer = each_music['composer']
                self.music.arranger = each_music['arranger']
                self.music.assetbundleName = each_music["assetbundleName"]
                self.music.publishedAt = each_music['publishedAt']
                break

    def _get_event_info(self, pjsk_type: int = 0):
        """
        根据卡面id获取当期活动信息
        """
        # 活动 ID
        event_cards = load_master_data('eventCards.json', pjsk_type)
        if isinstance(event_cards, dict):
            event_cards = list(event_cards.values())
        for each in event_cards:
            if not isinstance(each, dict):
                continue
            if each["cardId"] == self.id:
                self.event.id = each["eventId"]
                break
        if self.event.id == 0:
            raise Exception('卡面无对应活动')
        # 获取活动信息
        events = load_master_data('events.json', pjsk_type)
        if isinstance(events, dict):
            events = list(events.values())
        for each_event in events:
            if not isinstance(each_event, dict):
                continue
            if each_event["id"] == self.event.id:
                self.event.eventType = each_event['eventType']
                self.event.name = each_event['name']
                self.event.assetbundleName = each_event['assetbundleName']
                self.event.startAt = datetime.datetime.fromtimestamp(
                    each_event['startAt'] / 1000, pytz.timezone('Asia/Shanghai')
                ).strftime('%Y/%m/%d %H:%M:%S')
                self.event.aggregateAtorin = each_event['aggregateAt']
                self.event.aggregateAt = datetime.datetime.fromtimestamp(
                    each_event['aggregateAt'] / 1000 + 1, pytz.timezone('Asia/Shanghai')
                ).strftime('%Y/%m/%d %H:%M:%S')
                break
        # 获取参与活动的角色信息、活动属性
        eventDeckBonuses = load_master_data('eventDeckBonuses.json', pjsk_type)
        if isinstance(eventDeckBonuses, dict):
            eventDeckBonuses = list(eventDeckBonuses.values())
        for bonuse in eventDeckBonuses:
            if isinstance(bonuse, dict):
                b_event_id = bonuse.get('eventId')
            elif isinstance(bonuse, (list, tuple)) and len(bonuse) >= 1:
                b_event_id = bonuse[0]
            else:
                continue

            if b_event_id == self.event.id:
                if not self.event.bonuseattr:
                    try:
                        if isinstance(bonuse, dict):
                            self.event.bonuseattr = bonuse['cardAttr']
                        else:
                            # 假设属性在索引 2
                            self.event.bonuseattr = bonuse[2]
                    except:
                        pass
                try:
                    def get_bonuse_val(item, key, index):
                        if isinstance(item, dict):
                            return item.get(key)
                        elif isinstance(item, (list, tuple)) and len(item) > index:
                            return item[index]
                        return None

                    if get_bonuse_val(bonuse, 'bonusRate', 3) == 50:
                        self.event.bonusechara.append(get_bonuse_val(bonuse, "gameCharacterUnitId", 1))
                except:
                    pass

        game_character_units = load_master_data('gameCharacterUnits.json', pjsk_type)
        if isinstance(game_character_units, dict):
            game_character_units = list(game_character_units.values())
        tmp_bonuse_charas = []
        for unitid in self.event.bonusechara:
            charaid, unit, charapicname = analysisunitid(unitid, game_character_units, pjsk_type=pjsk_type)
            tmp_bonuse_charas.append({
                'id': charaid,
                'unit': unit,
                'asset': charapicname
            })
        # 对箱活加成角色作额外处理，只对杏二箱(id:37)后箱活作处理，之前的箱活加成角色不用变
        if self.event.id >= 37 and len(set(i['unit'] for i in tmp_bonuse_charas)) == 1:
            for bonuse_chara in tmp_bonuse_charas.copy():
                if bonuse_chara['id'] > 20:
                    tmp_bonuse_charas.remove(bonuse_chara)
            tmp_bonuse_charas.append({
                'unit': tmp_bonuse_charas[0]['unit'],
                'asset': 'vs_90.png'
            })
        self.event.bonusechara = tmp_bonuse_charas

    def _get_gacha_info(self, pjsk_type: int = 0):
        gachas = load_master_data('gachas.json', pjsk_type)
        if isinstance(gachas, dict):
            gachas = list(gachas.values())
            
        release_timestamp = int(datetime.datetime.strptime(self.releaseAt, '%Y/%m/%d %H:%M:%S').timestamp() * 1000)

        # 优先尝试依靠 Nanami-Bot 逻辑（当前UP卡）识别所属卡池
        for each_gacha in gachas:
            if not isinstance(each_gacha, dict): continue
            start_at = each_gacha.get("startAt", 0)
            end_at = each_gacha.get("endAt", float('inf'))
            if start_at <= release_timestamp <= end_at:
                gacha_pickups = each_gacha.get("gachaPickups", [])
                if isinstance(gacha_pickups, dict):
                    gacha_pickups = list(gacha_pickups.values())
                is_pickup = any(isinstance(p, dict) and p.get("cardId") == self.id for p in gacha_pickups)
                if is_pickup:
                    self._set_gacha_info_from_dict(each_gacha)
                    return

        # 如果没找到，退回原有的初次登场（gachaDetails）寻找逻辑以兼容开服卡/报酬卡
        for each_gacha in gachas:
            if not isinstance(each_gacha, dict):
                continue
            
            gacha_name = each_gacha.get("name", "")
            is_valid_type = each_gacha.get("gachaType") in ['ceil', 'regular', 'limited', 'fes', 'birthday']
            if not is_valid_type:
                continue
            if "出现率UP" in gacha_name or "出現率UP" in gacha_name or gacha_name.startswith('[1回限定]'):
                continue
            
            gacha_details = each_gacha.get("gachaDetails", [])
            if isinstance(gacha_details, dict):
                gacha_details = list(gacha_details.values())
            
            for each_card in gacha_details:
                if isinstance(each_card, dict):
                    c_id = each_card.get("cardId")
                elif isinstance(each_card, (list, tuple)) and len(each_card) >= 1:
                    c_id = each_card[0]
                else:
                    continue

                if c_id == self.id:
                    self._set_gacha_info_from_dict(each_gacha)
                    return
                    
    def _set_gacha_info_from_dict(self, each_gacha: dict):
        self.gacha.id = each_gacha.get("id", 0)
        self.gacha.gachaCardRarityRateGroupId = each_gacha.get("gachaCardRarityRateGroupId", 1)
        self.gacha.name = each_gacha.get("name", "")
        self.gacha.assetbundleName = each_gacha.get("assetbundleName", "")
        self.gacha.startAt = datetime.datetime.fromtimestamp(
            each_gacha.get('startAt', 0) / 1000, pytz.timezone('Asia/Shanghai')
        ).strftime('%Y/%m/%d %H:%M:%S')
        self.gacha.endAt = datetime.datetime.fromtimestamp(
            each_gacha.get('endAt', 0) / 1000, pytz.timezone('Asia/Shanghai')
        ).strftime('%Y/%m/%d %H:%M:%S')
    async def getinfo(self, cardid: int, pjsk_type: int = 0):
        """
        根据卡面id获取卡面信息
        """
        self.pjsk_type = pjsk_type
        allcards = await async_load_master_data('cards.json', pjsk_type)
        if isinstance(allcards, dict):
            allcards = list(allcards.values())
        for each_card in allcards:
            if not isinstance(each_card, dict):
                continue
            if each_card["id"] == cardid:
                self.id = each_card["id"]  # 卡面id
                self.characterId = each_card["characterId"]  # 角色id
                self.skillId = each_card["skillId"]  # 技能id

                self.cardRarityType = each_card["cardRarityType"]  # 卡面星数
                self.attr = each_card["attr"]  # 卡面属性
                if each_card.get("supportUnit", "none") != "none":
                    self.unit = each_card["supportUnit"]
                self.prefix = each_card["prefix"]  # 卡面名称
                if each_card["gachaPhrase"] != '-':  # 初始卡无招募语
                    self.gachaPhrase['JP'] = each_card["gachaPhrase"]  # 招募语
                self.cardSkillName['JP'] = each_card["cardSkillName"]  # 技能名称
                self.releaseAt = datetime.datetime.fromtimestamp(
                    each_card['releaseAt'] / 1000, pytz.timezone('Asia/Shanghai')
                ).strftime('%Y/%m/%d %H:%M:%S')  # 发布时间
                self.assets["card"] = each_card.get("assetbundleName", "")  # 卡面大图asset名称
                # 卡面综合力
                card_parameters = each_card.get("cardParameters", [])
                
                # CN/TW servers use a dict for card parameters: {"param1": [v1, v2..], "param2": [...]}
                if isinstance(card_parameters, dict):
                    for p_type, p_powers in card_parameters.items():
                        if isinstance(p_powers, (list, tuple)) and len(p_powers) > 0:
                            power_val = max(p_powers)
                        elif isinstance(p_powers, (int, float)):
                            power_val = p_powers
                        else:
                            continue
                        self.cardParameters[p_type] = power_val
                
                # JP server uses a list of dicts: [{"cardParameterType": "param1", "power": v1}, ...]
                elif isinstance(card_parameters, list):
                    for cardparams in card_parameters:
                        if isinstance(cardparams, dict):
                            p_type = cardparams.get("cardParameterType")
                            p_power = cardparams.get("power", 0)
                        elif isinstance(cardparams, (list, tuple)) and len(cardparams) >= 2:
                            p_type = cardparams[0]
                            p_power = cardparams[1]
                        else:
                            continue
                        
                        if p_type:
                            p_type_str = str(p_type).lower()
                            mapping = {
                                'performance': 'param1',
                                'vocal': 'param2',
                                'technical': 'param2', 'technique': 'param2',
                                'visual': 'param3', 'stamina': 'param3'
                            }
                            normalized_type = mapping.get(p_type_str, p_type_str)
                            # Update max power
                            current_power = self.cardParameters.get(normalized_type, 0)
                            self.cardParameters[normalized_type] = max(current_power, p_power)
                
                break
        else:
            raise KeyError('没有此id的卡面')
        # 日文技能效果
        skills = await async_load_master_data('skills.json', pjsk_type)
        if isinstance(skills, dict):
            skills = list(skills.values())
        for each_skill in skills:
            if not isinstance(each_skill, dict):
                continue
            if each_skill["id"] == self.skillId:
                self.cardSkillDes['JP'] = each_skill["description"]
                break

        # 角色名称(日文)、组合名称
        game_characters = await async_load_master_data('gameCharacters.json', pjsk_type)
        if isinstance(game_characters, dict):
            game_characters = list(game_characters.values())

        for each_chara in game_characters:
            if not isinstance(each_chara, dict):
                continue
            if each_chara["id"] == self.characterId:
                self.charaName = (
                        each_chara.get("firstName", "") + " " + each_chara.get("givenName", "")
                ).strip()  # 角色名称
                self.unit = self.unit if self.unit != 'none' else each_chara.get("unit", "none")  # 组合名称
                break

        # 获取衣装asset名
        costume3ds = await async_load_master_data('cardCostume3ds.json', pjsk_type)
        if isinstance(costume3ds, dict):
            costume3ds = list(costume3ds.values())
        card_costumes_ids = []
        for each_costume in costume3ds:
            if not isinstance(each_costume, dict):
                continue
            _costume3dId = each_costume.get("costume3dId")
            if each_costume.get('cardId') == self.id and _costume3dId:
                card_costumes_ids.append(_costume3dId)
        costume3ds_all = await async_load_master_data('costume3ds.json', pjsk_type)
        if isinstance(costume3ds_all, dict):
            costume3ds_all = list(costume3ds_all.values())
        for each_costume_id in card_costumes_ids:
            for each_model in costume3ds_all:
                if not isinstance(each_model, dict):
                    continue
                if each_model.get('id') == each_costume_id:
                    _parttype = each_model.get("partType")
                    _assetbundleName = each_model.get("assetbundleName")
                    if not _parttype or not _assetbundleName:
                        continue
                    if _parttype == 'hair':
                        self.isLimited = True
                    self.assets["costume"][_parttype] = self.assets["costume"].get(_parttype, [])
                    self.assets["costume"][_parttype].append(_assetbundleName)
                    break
        # 尝试获取翻译信息
        trans_file = get_server_data_path(pjsk_type) / 'translate.yaml'
        if trans_file.exists():
            with open(trans_file, encoding='utf-8') as f:
                trans = yaml.load(f, Loader=yaml.FullLoader)
                if not isinstance(trans, dict):
                    trans = {}
        else:
            trans = {}
        # 招募语
        try:
            self.gachaPhrase['CN'] = trans['card_gacha_phrase'][self.id]
        except:
            pass
        # 技能名称
        try:
            self.cardSkillName['CN'] = trans['card_skill_name'][self.id]
        except:
            pass
        # 技能效果
        try:
            self.cardSkillDes['CN'] = trans['skill_desc'][self.skillId]
        except:
            pass
        # 最后解析技能效果中的数值
        for key in self.cardSkillDes.keys():
            self.cardSkillDes[key] = cardskill(self.skillId, skills, self.cardSkillDes[key])

        # 获取活动信息
        if self.config.get('event', True):
            try:
                self._get_event_info(pjsk_type=pjsk_type)
            except:
                pass
        # 获取歌曲信息
        if self.config.get('music', True):
            try:
                self._get_music_info(pjsk_type=pjsk_type)
            except:
                pass

        # 获取卡池信息
        if self.config.get('gacha', True):
            try:
                self._get_gacha_info(pjsk_type=pjsk_type)
            except:
                pass
