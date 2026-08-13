import datetime
import math
import random
import re
import time
from pathlib import Path
from typing import Any, Tuple, Optional
from apscheduler.triggers.date import DateTrigger
from nonebot import on_regex, on_command, on_message, get_bot
from nonebot.adapters.onebot.v11 import GROUP, GroupMessageEvent, Bot, MessageSegment
from nonebot.internal.matcher import Matcher
from nonebot.params import RegexGroup
from nonebot.typing import T_State
from models.bag_user import BagUser
from services import logger
from utils.data_utils import init_rank
from utils.imageutils import text2image, pic2b64
from utils.utils import scheduler
from utils.message_builder import at, image
from utils.limit_utils import access_count, access_cd
from ._config import pjskguess, GUESS_MUSIC, GUESS_CARD, PJSK_GUESS, max_tips_count, guess_time, PJSK_ANSWER, \
    PJSK_MATCHED, max_guess_count
from ._data_source import (
    getRandomChart, getRandomJacket, getRandomMusic, getRandomSE, getRandomLyrics,
    cutJacket, cutMusic, cutSE, cutCard, getRandomCard, cutChart, getMusic, getJacket,
    getCard, cutLyrics
)
from ._function import (
    getSongLevel, getSongAuthor, getSongSinger, getCharaUnit, getCharaInfo,
    getCharaBirth, getCharaFeature, getSongNoteCount, getSongLyrics
)
from ._rule import check_rule, check_reply
from ._utils import pre_check, aliasToMusicId, aliasToCharaId
from .._config import BUG_ERROR
from .._models import PjskGuessRank
import json

__plugin_name__ = "pjsk猜卡面/猜曲/猜谱面"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.1
__plugin_usage__ = f"""
usage：
    pjsk 猜曲、猜卡面、猜谱面
    若群内已有unibot请勿开启此bot该功能
    由于功能容易刷屏，仅在特定群开放
    指令：
        pjsk [类型] 猜曲/识曲           ：[类型]有普通、阴间、非人类、听歌、倒放、谱面、歌词几种类型，默认为普通   
        pjsk听歌识曲                    ：启动听歌识曲模式，无需艾特即可作答
        pjsk猜曲 [数字]                 ：[数字]对应上方的[类型]，范围从1~7，默认为1   
        
        pjsk猜谱面                      ：效果与 'pjsk谱面猜曲' 或 'pjsk猜曲6' 相同
        
        pjsk [类型] 猜卡面               ：[类型]有普通、阴间、非人类几种类型，默认为普通   
        pjsk猜卡面 [数字]                ：[数字]对应上方的[类型]，范围从1~3，默认为1   
        
        给点提示/来点提示                   ：在游戏进行时由发起者使用，可以随机得到一些提示
        结束猜曲/结束猜卡面/结束猜谱面        ：在游戏进行时由发起者使用，可以提前结束游戏
        pjsk *[类型] 猜曲排行榜            ：获取群内猜曲排名，没有[类型]参数时为全难度猜曲排行榜
        pjsk *[类型] 猜卡面排行榜           ：获取群内猜卡面排名
        pjsk猜谱面排行榜                   ：获取群内猜谱面排名
    举例：
        pjsk猜曲/pjsk阴间猜曲/pjsk听歌识曲/pjsk猜曲6
        pjsk猜卡面
        pjsk猜曲排行榜/pjsk非人类猜卡面排行榜
    注意：
        每轮游戏时长90秒，所有人都可以参与，回答正确者视游戏难度奖励一些金币(可在商店功能中使用)
        每轮游戏的发起者可以使用3次提示机会，但每次使用提示会扣除25%的奖励，同时此轮游戏不会计入排行榜
        若游戏被发起者以外的参与者抢答正确，抢答者只能获得90%的金币奖励
        每轮游戏中，每人只有3次猜测机会，超过3次后的任何回答都将被bot忽略
        限制每个群每半小时最多猜10次，每人一天只能猜10次
""".strip()
__plugin_settings__ = {
    "level": 5,
    "default_status": False,
    "cmd": ["pjsk猜曲", "pjsk听歌识曲", "pjsk识曲", "pjsk猜卡面", "pjsk猜谱面", "烧烤相关", "uni移植"],
}
__plugin_cd_limit__ = {"cd": 1800, "count_limit": 10, "rst": "别急，等[cd]秒后再用！", "limit_type": "group"}
__plugin_count_limit__ = {
    "max_count": 10,
    "limit_type": "user",
    "rst": "今天已经猜了[count]次了，还请明天再继续呢[at]",
}

# pjsk猜卡面
pjsk_guesscard = on_regex(
    r'^(cn|tw|jp)? *(?:pjsk|sekai) *(正常|阴间|非人类)? *猜卡面 *(\d*)$',
    flags=re.I, permission=GROUP, priority=5, block=True
)

# pjsk猜曲目
pjsk_guessmusic = on_regex(
    r'^(cn|tw|jp)? *(?:pjsk|sekai) *(正常|阴间|非人类|听歌|倒放|谱面|歌词)? *(?:猜曲|识曲) *(\d*)$',
    flags=re.I, permission=GROUP, priority=5, block=True
)

# pjsk猜谱面
pjsk_guessmap = on_regex(
    r'^(cn|tw|jp)? *(?:pjsk|sekai) *猜谱面 *$',
    flags=re.I, permission=GROUP, priority=5, block=True
)

pjsk_guessreply = on_message(rule=check_reply(), permission=GROUP, priority=4, block=True)

pjsk_guessrank = on_regex(
    r'^(cn|tw|jp)? *(?:pjsk|sekai) *(?:(正常|阴间|非人类|听歌|倒放|谱面|歌词)?(猜曲|猜卡面|猜谱面)) *排[行名]榜? *(\d*)',
    flags=re.I, permission=GROUP, priority=4, block=True
)

pjsk_guesstip = on_command('来点提示', aliases={"来丶提示", "给点提示", "给丶提示"}, rule=check_rule(), permission=GROUP, priority=3, block=True)

pjsk_gameover = on_command('结束猜曲', aliases={"结束猜谱面", "结束猜卡面"}, rule=check_rule(), permission=GROUP, priority=3, block=True)


@pjsk_guessreply.handle()
async def _(matcher: Matcher, event: GroupMessageEvent, state: T_State):
    global pjskguess
    answer = state[PJSK_ANSWER]
    if not answer:
        matcher.block = False
        return
    game_type = state[PJSK_GUESS]
    pjsk_type = state.get('pjsk_type', 0)
    matched = state.get(PJSK_MATCHED)
    if not matched:
        matcher.block = False
        return
    # 判断玩家回答次数是否超标
    if max_guess_count <= pjskguess[game_type][event.group_id]['gameusers'].get(event.user_id, 0):
        return
    # 玩家回答次数加一
    guessed_count = pjskguess[game_type][event.group_id]['gameusers'].get(event.user_id, 0)
    pjskguess[game_type][event.group_id]['gameusers'][event.user_id] = guessed_count + 1
    # 判断游戏类型
    if game_type == GUESS_CARD:
        charaid, name = matched
        if charaid == pjskguess[game_type][event.group_id]['charaid']:
            await endgame(
                event.group_id, event.self_id, GUESS_CARD, event.user_id, True,
                plugin_name=matcher.plugin_name, event=event
            )
        else:
            await pjsk_guessreply.finish(
                f'您猜错了，答案不是{name}哦\n(剩余作答次数:{max_guess_count-guessed_count-1}/{max_guess_count})',
                at_sender=True
            )
    else:
        musicid, name = matched
        if musicid == pjskguess[game_type][event.group_id]['musicid']:
            await endgame(
                event.group_id, event.self_id, GUESS_MUSIC, event.user_id, True,
                plugin_name=matcher.plugin_name, event=event
            )
        else:
            await pjsk_guessreply.finish(
                f'您猜错了，答案不是{name}哦\n(剩余作答次数:{max_guess_count-guessed_count-1}/{max_guess_count})',
                at_sender=True
            )


@pjsk_guesscard.handle()
async def _(matcher: Matcher, event: GroupMessageEvent, reg_group: Tuple[Any, ...] = RegexGroup()):
    global pjskguess
    prefix = reg_group[0].strip() if reg_group[0] else 'jp'
    pjsk_type = 0
    if prefix == 'cn':
        pjsk_type = 2
    elif prefix == 'tw':
        pjsk_type = 1
        
    if reply := pre_check(event.group_id):
        await pjsk_guesscard.finish(reply)
    msgs = []
    cardid, charaid, asset, cardname, charaname, rarity = await getRandomCard(pjsk_type=pjsk_type)
    if not reg_group[1] and not reg_group[2] or reg_group[1] == '正常' or reg_group[2] == '1':
        size, isbw, guessDiff = 250, False, 1
        text = ''
    elif reg_group[1] == '阴间' or reg_group[2] == '2':
        size, isbw, guessDiff = 250, True, 2
        text = '阴间'
    elif reg_group[1] == '非人类' or reg_group[2] == '3':
        size, isbw, guessDiff = 60, False, 3
        text = '非人类'
    else:
        await pjsk_guesscard.finish(BUG_ERROR)
        return
    file, endfile = cutCard(asset, rarity, event.group_id, size, isbw, pjsk_type=pjsk_type)
    msgs.append(image(b64=pic2b64(text2image(
        f'PJSK{text}卡面竞猜 （随机裁切）\n直接发送你的答案即可参加猜卡面（不要使用回复）\n'
        f'\n你有{guess_time}秒的时间回答\n可手动发送“结束猜卡面”来结束猜卡面'
    ))))
    if not file.exists():
        await matcher.finish("由于资源缺失，猜卡面启动失败捏", at_sender=True)
    msgs.append(image(file))
    for msg in msgs:
        await matcher.send(msg)
    pjskguess[GUESS_CARD][event.group_id] = {
        'isgoing': True, 'diff': guessDiff, 'tips': None, 'time': time.time(), 'gameusers': {},
        'charaid': charaid, 'cardname': cardname, 'cardid': cardid, 'charaname': charaname,
        'userid': event.user_id, 'file': file, 'endfile': endfile, 'tipfile': None,
        'pjsk_type': pjsk_type
    }
    delta = datetime.timedelta(seconds=guess_time)
    trigger = DateTrigger(run_date=datetime.datetime.now() + delta)
    scheduler.add_job(
        func=endgame,
        trigger=trigger,
        kwargs={'group_id':event.group_id, 'game_type': GUESS_CARD, 'self_id': event.self_id},
        # misfire_grace_time=5,
    )


@pjsk_guessmusic.handle()
async def _(matcher: Matcher, event: GroupMessageEvent, reg_group: Tuple[Any, ...] = RegexGroup()):
    global pjskguess
    prefix = reg_group[0].strip() if reg_group[0] else 'jp'
    pjsk_type = 0
    if prefix == 'cn':
        pjsk_type = 2
    elif prefix == 'tw':
        pjsk_type = 1
        
    if reply := pre_check(event.group_id):
        await pjsk_guesscard.finish(reply)
    msgs = []
    if not reg_group[1] and not reg_group[2] or reg_group[1] == '正常' or reg_group[2] == '1':
        musicid, musicname, asset = await getRandomJacket(pjsk_type=pjsk_type)
        file, endfile = cutJacket(asset, event.group_id, size=140, isbw=False, pjsk_type=pjsk_type)
        guessDiff = 1
        text = '曲绘'
        msgs.append(image(file))
    elif reg_group[1] == '阴间' or reg_group[2] == '2':
        musicid, musicname, asset = await getRandomJacket(pjsk_type=pjsk_type)
        file, endfile = cutJacket(asset, event.group_id, size=140, isbw=True, pjsk_type=pjsk_type)
        guessDiff = 2
        text = '阴间曲绘'
        msgs.append(image(file))
    elif reg_group[1] == '非人类' or reg_group[2] == '3':
        musicid, musicname, asset = await getRandomJacket(pjsk_type=pjsk_type)
        file, endfile = cutJacket(asset, event.group_id, size=30, isbw=False, pjsk_type=pjsk_type)
        guessDiff = 3
        text = '非人类曲绘'
        msgs.append(image(file))
    elif reg_group[1] == '听歌' or reg_group[2] == '4':
        musicid, musicname, asset = await getRandomMusic(pjsk_type=pjsk_type)
        file, endfile = cutMusic(asset, event.group_id, 1.7, False, pjsk_type=pjsk_type)
        guessDiff = 4
        text = '听歌识曲'
        msgs.append(record(file))
    elif reg_group[1] == '倒放' or reg_group[2] == '5':
        musicid, musicname, asset = await getRandomMusic(pjsk_type=pjsk_type)
        file, endfile = cutMusic(asset, event.group_id, 5, True, pjsk_type=pjsk_type)
        guessDiff = 5
        text = '倒放识曲'
        msgs.append(record(file))
    elif reg_group[1] == '谱面' or reg_group[2] == '6':
        musicid, musicname = await getRandomChart(pjsk_type=pjsk_type)
        file, endfile = cutChart(musicid, event.group_id, pjsk_type=pjsk_type)
        guessDiff = 6
        text = '谱面'
        msgs.append(image(file))
    elif reg_group[1] == '歌词' or reg_group[2] == '7':
        musicid, musicname, asset = getRandomLyrics(pjsk_type=pjsk_type)
        if musicid == 0:
            await pjsk_guessmusic.finish(BUG_ERROR)
        lyrics, endfile = cutLyrics(musicid, asset, event.group_id, pjsk_type=pjsk_type)
        file = None
        guessDiff = 7
        text = '歌词'
        msgs.append(MessageSegment.text(lyrics))
    else:
        await pjsk_guessmusic.finish(BUG_ERROR)
        return
    msgs.insert(0, image(b64=pic2b64(text2image(
        f'PJSK{text}竞猜 （随机裁切）\n直接发送你的答案即可参加猜曲（不要使用回复）\n'
        f'\n你有{guess_time}秒的时间回答\n可手动发送“结束猜曲”来结束猜曲'
    ))))
    if file and not file.exists() and guessDiff != 7: # 歌词模式 file 为 None
        await matcher.finish("由于资源缺失，猜曲启动失败捏", at_sender=True)
    for msg in msgs:
        await matcher.send(msg)
    pjskguess[GUESS_MUSIC][event.group_id] = {
        'isgoing': True, 'diff': guessDiff, 'tips': None, 'time': time.time(), 'gameusers': {},
        'musicid': musicid, 'musicname': musicname,
        'userid': event.user_id, 'file': file, 'endfile': endfile, 'tipfile': None,
        'pjsk_type': pjsk_type
    }
    delta = datetime.timedelta(seconds=guess_time)
    trigger = DateTrigger(run_date=datetime.datetime.now() + delta)
    scheduler.add_job(
        func=endgame,
        trigger=trigger,
        kwargs={'group_id':event.group_id, 'game_type': GUESS_MUSIC, 'self_id': event.self_id},
        # misfire_grace_time=5,
    )


@pjsk_guessmap.handle()
async def _(matcher: Matcher, event: GroupMessageEvent, reg_group: Tuple[Any, ...] = RegexGroup()):
    global pjskguess
    prefix = reg_group[0].strip() if reg_group[0] else 'jp'
    pjsk_type = 0
    if prefix == 'cn':
        pjsk_type = 2
    elif prefix == 'tw':
        pjsk_type = 1
        
    if reply := pre_check(event.group_id):
        await pjsk_guesscard.finish(reply)
    msgs = []
    musicid, musicname = await getRandomChart(pjsk_type=pjsk_type)
    file, endfile = cutChart(musicid, event.group_id, pjsk_type=pjsk_type)
    guessDiff = 6
    msgs.append(image(b64=pic2b64(text2image(
        'PJSK谱面竞猜 （随机裁切）\n艾特我+你的答案以参加猜曲（不要使用回复）\n'
        f'\n你有{guess_time}秒的时间回答\n可手动发送“结束猜谱面”来结束猜曲'
    ))))
    if not file.exists():
        await matcher.finish("由于资源缺失，猜谱面启动失败捏", at_sender=True)
    msgs.append(image(file))
    for msg in msgs:
        await matcher.send(msg)
    pjskguess[GUESS_MUSIC][event.group_id] = {
        'isgoing': True, 'diff': guessDiff, 'tips': None, 'time': time.time(), 'gameusers': {},
        'musicid': musicid, 'musicname': musicname,
        'userid': event.user_id, 'file': file, 'endfile': endfile, 'tipfile': None,
        'pjsk_type': pjsk_type
    }
    delta = datetime.timedelta(seconds=guess_time)
    trigger = DateTrigger(run_date=datetime.datetime.now() + delta)
    scheduler.add_job(
        func=endgame,
        trigger=trigger,
        kwargs={'group_id':event.group_id, 'game_type': GUESS_MUSIC, 'self_id': event.self_id},
        # misfire_grace_time=5,
    )


@pjsk_gameover.handle()
async def _(event: GroupMessageEvent, state: T_State):
    game_type = state[PJSK_GUESS]
    text = '猜曲' if game_type == GUESS_MUSIC else '猜卡面'
    if pjskguess[game_type][event.group_id]['userid'] != event.user_id:
        await pjsk_gameover.finish(f"您不是游戏发起者，不可以提前结束{text}", at_sender=True)
    else:
        await endgame(event.group_id, event.self_id, game_type, advanceover=True)


@pjsk_guesstip.handle()
async def _(event: GroupMessageEvent, state: T_State):
    global pjskguess
    game_type = state[PJSK_GUESS]
    pjsk_type = state.get('pjsk_type', 0)
    if event.user_id != pjskguess[game_type][event.group_id]['userid']:
        await pjsk_guesstip.finish("您不是游戏发起者，不可以使用提示功能", at_sender=True)
    voidflag = False
    if pjskguess[game_type][event.group_id]['tips'] is None:
        pjskguess[game_type][event.group_id]['tips'] = []
        voidflag = True
    alltips = pjskguess[game_type][event.group_id]['tips']
    # 猜卡面：学校0、性别1、爱好2、团队信息3、卡面信息(卡面属性4、卡面是否特训5、卡面稀有度6)
    if game_type == GUESS_CARD:
        # 若未提示过，初始化字段
        if voidflag:
            cardid = pjskguess[GUESS_CARD][event.group_id]['cardid']
            charaid = pjskguess[GUESS_CARD][event.group_id]['charaid']
            diff = pjskguess[GUESS_CARD][event.group_id]['diff']
            if charaid in [21, 22, 23, 24, 25, 26]:
                _ts = [0, 2]
            else:
                if diff == 1:
                    _ts = random.sample(range(4), k=max_tips_count-1)
                elif diff == 2:
                    _ts = random.sample(range(3), k=max_tips_count-1)
                else:
                    _ts = [0, 1]
            for _t in _ts:
                content = '获取提示失败，可能是バグ'
                if _t == 0:
                    # 获得角色身高、性别、学校之一
                    content = getCharaInfo(charaid, pjsk_type=pjsk_type) or content
                elif _t == 1:
                    # 获得角色爱好、强项、弱项之一
                    content = getCharaFeature(charaid, pjsk_type=pjsk_type) or content
                elif _t == 2:
                    # 获得角色出生月份
                    content = getCharaBirth(charaid, pjsk_type=pjsk_type) or content
                else:
                    # 获得角色团队信息
                    content = getCharaUnit(charaid, pjsk_type=pjsk_type) or content
                alltips.append(content)
            # 截取相同卡面
            size, isbw = 280, False
            if diff == 3:
                size, isbw = 180, True
                asset, rarity = await getCard(cardid=cardid, pjsk_type=pjsk_type)
                tipfile = cutCard(asset, rarity, event.group_id, size, isbw, is_tip=True, pjsk_type=pjsk_type)
                pjskguess[game_type][event.group_id]['tipfile'] = tipfile
                content = [MessageSegment.image(f"file:///{tipfile.absolute()}"), "这里是卡面另一块卡面截图"]
                alltips.append(content)
            # 截取同角色其它卡面
            else:
                if diff == 1:
                    size, isbw = 280, False
                elif diff == 2:
                    size, isbw = 230, False
                asset, rarity = await getCard(charaid=charaid, pjsk_type=pjsk_type)
                tipfile = cutCard(asset, rarity, event.group_id, size, isbw, is_tip=True, pjsk_type=pjsk_type)
                pjskguess[game_type][event.group_id]['tipfile'] = tipfile
                content = [MessageSegment.image(f"file:///{tipfile.absolute()}"), "这里是角色另一块卡面截图"]
                alltips.append(content)

    # 猜曲：难度等级0、演唱者1、歌名提示2
    else:
        # 若未提示过，初始化字段
        if voidflag:
            musicid = pjskguess[GUESS_MUSIC][event.group_id]['musicid']
            diff = pjskguess[GUESS_MUSIC][event.group_id]['diff']
            for _t in random.sample(range(3), k=max_tips_count-1):
                content = '没能获取到提示，可能是バグ'
                if _t == 0:
                    # 获得歌曲难度等级
                    content = getSongLevel(musicid, pjsk_type=pjsk_type) or content
                elif _t == 1:
                    # 获得歌曲演唱者信息
                    try:
                        content = getSongSinger(musicid, pjsk_type=pjsk_type) or content
                    # 获取到的是初音未来，换个提示
                    except KeyError:
                        # 获得歌曲谱面物量
                        content = getSongNoteCount(musicid, pjsk_type=pjsk_type) or content
                elif _t == 2:
                    # 获得歌曲作者信息
                    content = getSongAuthor(musicid, pjsk_type=pjsk_type) or content
                alltips.append(content)
            # 随机截图&音效
            size, isbw, cutlen, reverse = 140, False, 1.7, False
            if diff == 1:
                size, isbw = 140, False
            elif diff == 2:
                size, isbw = 115, False
            elif diff == 3:
                size, isbw = 90, True
            elif diff == 4:
                cutlen, reverse = 5, False
            elif diff == 5:
                cutlen, reverse = 3, False
            if diff in [1,2,3]:
                asset = await getJacket(musicid, pjsk_type=pjsk_type)
                tipfile = cutJacket(asset, event.group_id, size, isbw, is_tip=True, pjsk_type=pjsk_type)
                content = [MessageSegment.image(f"file:///{tipfile.absolute()}"), "这里是另一块曲绘截图"]
                alltips.append(content)
            elif diff in [4, 5]:
                asset = await getMusic(musicid, pjsk_type=pjsk_type)
                tipfile = cutMusic(asset, event.group_id, cutlen, reverse, is_tip=True, pjsk_type=pjsk_type)
                content = [MessageSegment.record(f"file:///{tipfile.absolute()}"), "这里是另一段音频裁剪"]
                alltips.append(content)
            elif diff == 6:
                tipfile = cutChart(musicid, event.group_id, is_tip=True, pjsk_type=pjsk_type)
                content = [MessageSegment.image(f"file:///{tipfile.absolute()}"), "这里是另外随机两段谱面截图"]
                alltips.append(content)
            else:
                tipfile = getSongLyrics(musicid, pjsk_type=pjsk_type)
                content = [tipfile+"\n以上是另外随机两段歌词"]
                alltips.append(content)
            pjskguess[game_type][event.group_id]['tipfile'] = tipfile

    # 选择任意一种可用字段发送
    if len(alltips) > 0:
        tips = alltips.pop(random.randint(0, len(alltips)-1))
        if isinstance(tips, str):
            await pjsk_guesstip.finish(f'提示：{tips}哦！\n剩余提示次数：({len(alltips)}/{max_tips_count})')
        else:
            for tip in tips:
                if isinstance(tip, str):
                    await pjsk_guesstip.send(f'提示：{tip}哦！\n剩余提示次数：({len(alltips)}/{max_tips_count})')
                else:
                    await pjsk_guesstip.send(tip)
            await pjsk_guesstip.finish()
    # 已到最大提示次数
    else:
        await pjsk_guesstip.finish(f'已经提示过{max_tips_count}次了，不会再提示了！')


@pjsk_guessrank.handle()
async def _(matcher: Matcher, event: GroupMessageEvent, reg_group: Tuple[Any, ...] = RegexGroup()):
    prefix = reg_group[0].strip() if reg_group[0] else 'jp'
    pjsk_type = 0
    if prefix == 'cn':
        pjsk_type = 2
    elif prefix == 'tw':
        pjsk_type = 1
        
    game_type = GUESS_CARD if reg_group[2] == '猜卡面' else GUESS_MUSIC
    diff_dict = {
        '正常': 1, '阴间': 2, '非人类': 3,
        '听歌': 4, '倒放': 5, '谱面': 6,
        '歌词': 7
    }
    if not reg_group[1]:
        guess_diff = None
        text = '合计'
    elif diff_dict.get(reg_group[1]):
        guess_diff = diff_dict[reg_group[1]]
        text = reg_group[1]
    else:
        await pjsk_guessrank.finish(BUG_ERROR)
        return
    if reg_group[2] == '猜谱面':
        guess_diff = 6
    if str(reg_group[3]).isdigit():
        total_count = int(reg_group[3]) if 10 <= int(reg_group[3]) <= 50 else 50
    else:
        total_count = 10
    if game_type == GUESS_CARD and guess_diff is not None and guess_diff > 3:
        await pjsk_guessrank.finish("没有这种类型的排行榜哦！")
    users, ranks = await PjskGuessRank.get_rank(event.group_id, game_type, guess_diff, pjsk_type=pjsk_type)
    if len(users) == 0:
        await pjsk_guessrank.finish("当前类型排行榜尚无人上榜哦")
    _type = '猜曲' if game_type == GUESS_MUSIC else '猜卡面'
    server_text = f"[{prefix.upper()}]" if prefix != 'jp' else ""
    try:
        pic = await init_rank(
            f"{server_text}{text}{_type}排行榜", users, ranks, event.group_id,
            total_count=total_count, limit_count=50, save_key=f"g{event.group_id}_{total_count}_{text}{_type}_{pjsk_type}_pjskguess"
        )
    except:
        await pjsk_guessrank.finish(BUG_ERROR)
    else:
        await pjsk_guessrank.finish(image(b64=pic.pic2bs4()))


async def endgame(
    group_id: int,
    self_id: int,
    game_type: str,
    user_qq: Optional[int] = None,
    gameover: bool = False,
    advanceover: bool = False,
    plugin_name: Optional[str] = None,
    event: Optional[GroupMessageEvent] = None
):
    global pjskguess
    rewards = {
        GUESS_CARD: {
            'basic':[10,30,50],
            'extra':[3,5,10]
        },
        GUESS_MUSIC:{
            'basic':[10,30,50,30,45,60,60],
            'extra':[3,5,10,5,10,15,15]
        }
    }
    try:
        # 判断游戏是否进行中
        if pjskguess[game_type][group_id].get('isgoing', False):
            msgs = []
            file = pjskguess[game_type][group_id]['file']
            endfile = pjskguess[game_type][group_id]['endfile']
            tipfile = pjskguess[game_type][group_id]['tipfile']
            pjsk_type = pjskguess[game_type][group_id].get('pjsk_type', 0)
            # 收到正确回答，提前结束游戏
            if gameover:
                tips = pjskguess[game_type][group_id]['tips']
                qq = pjskguess[game_type][group_id]['userid']
                if tips is None:
                    tipcount = 0
                else:
                    tipcount = max_tips_count - len(tips)
                diff = pjskguess[game_type][group_id]['diff']
                # 针对猜曲
                if game_type == GUESS_MUSIC:
                    musicname = pjskguess[game_type][group_id]['musicname']
                    if diff in [1, 2, 3, 6, 7]:
                        msgs.append(f"正确答案：{musicname}")
                        if endfile.exists():
                            msgs.append(image(endfile))
                        else:
                            msgs.append("（由于资源缺失，结算大图显示失败）")
                    else:
                        msgs.append(f"正确答案：{musicname}")
                        if endfile.exists():
                            msgs.append(record(endfile))
                        else:
                            msgs.append("（由于资源缺失，结算音频载入失败）")
                # 针对猜卡面
                else:
                    cardname = pjskguess[game_type][group_id]['cardname']
                    charaname = pjskguess[game_type][group_id]['charaname']
                    msgs.append(f"正确答案：{cardname} - {charaname}")
                    if endfile.exists():
                        msgs.append(MessageSegment.image(f'file:///{endfile.absolute()}'))
                    else:
                        msgs.append("（由于资源缺失，结算大图显示失败）")
                # 结算金币 按难度决定基础奖励
                rdgold = random.randint(0, rewards[game_type]['extra'][diff - 1])
                gold = rewards[game_type]['basic'][diff - 1] + rdgold
                # 游戏中使用了提示功能，奖励每次扣除25%
                gold = math.ceil(gold - tipcount * gold // 4)
                # 游戏发起者以外的人回答正确，0.9倍率
                if user_qq and user_qq != pjskguess[game_type][group_id]['userid']:
                    qq = user_qq
                    gold = math.ceil(0.9 * gold)
                # 记录进数据库
                addflag = not await PjskGuessRank.check_today_count(qq, group_id)
                if tips is None and addflag:
                    await PjskGuessRank.add_count(qq, group_id, game_type, int(diff), pjsk_type=pjsk_type)
                else:
                    await PjskGuessRank.add_count(qq, group_id, game_type, int(diff), tips_used=True, pjsk_type=pjsk_type)
                if addflag:
                    msgs[0] = at(qq) + f"您猜对了，奖励{gold}金币！\n" + msgs[0]
                    await BagUser.add_gold(qq, group_id, gold)
                else:
                    msgs[0] = at(qq) + f"您猜对了，但是已达今日游戏获取金币上限，并没有奖励！\n" + msgs[0]
                # 时间到自然结束游戏 或 提前结束
            else:
                if not advanceover:
                    # 自然结束：先判断是否是正常到达时间
                    starttime = pjskguess[game_type][group_id]['time']
                    left_time = starttime + guess_time - time.time()
                    # scheduler调度出现严重时间误差，重新调度
                    if left_time > guess_time * 0.05:
                        delta = datetime.timedelta(seconds=left_time)
                        trigger = DateTrigger(run_date=datetime.datetime.now() + delta)
                        scheduler.add_job(
                            func=endgame,
                            trigger=trigger,
                            kwargs={'group_id': group_id, 'game_type': game_type, 'self_id': self_id},
                            # misfire_grace_time=5,
                        )
                        return
                _over = '提前结束' if advanceover else '时间到'
                # 针对猜曲
                if game_type == GUESS_MUSIC:
                    musicname = pjskguess[game_type][group_id]['musicname']
                    if pjskguess[game_type][group_id]['diff'] in [1, 2, 3, 6, 7]:
                        msgs.append(f"{_over}，正确答案：{musicname}")
                        if endfile.exists():
                            msgs.append(image(endfile))
                        else:
                            msgs.append("（由于资源缺失，结算大图显示失败）")
                    else:
                        msgs.append(f"{_over}，正确答案：{musicname}")
                        if endfile.exists():
                            msgs.append(record(endfile))
                        else:
                            msgs.append("（由于资源缺失，结算音频显示失败）")
                # 针对猜卡面
                elif game_type == GUESS_CARD:
                    cardname = pjskguess[game_type][group_id]['cardname']
                    msgs.append(f"{_over}，正确答案：{cardname}")
                    if endfile.exists():
                        msgs.append(image(endfile))
                    else:
                        msgs.append("（由于资源缺失，结算大图显示失败）")
            pjskguess[game_type][group_id].clear()
            # 发送信息
            bot: Bot = get_bot(str(self_id))
            try:
                for msg in msgs:
                    await bot.send_msg(message_type='group', group_id=group_id, message=msg)
            except:
                pass
            if isinstance(file, Path) and file.exists():
                file.unlink()
            if isinstance(endfile, Path) and endfile.exists():
                endfile.unlink()
            if isinstance(tipfile, Path) and tipfile.exists():
                tipfile.unlink()
            if not(plugin_name is None or event is None):
                access_count(plugin_name, event)
                access_cd(plugin_name, event)
    except KeyError:
        pass
    except Exception as e:
        logger.warning(f"pjsk猜曲结算环节出错，Error:{e}")
