"""消息解析：把「指令 + @/qq号/自己/图片/文字」拆成图片源和文本。

沿用 petpet 那套交互习惯，但产出的是裸 bytes（meme-generator 要的），
不是 BuildImage。为避免插件间的导入顺序问题，头像下载在本模块内自足实现。
"""

from __future__ import annotations

import hashlib
import re
import shlex
from dataclasses import dataclass, field
from typing import Optional

from nonebot import get_driver
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
    unescape,
)
from nonebot.params import Depends
from nonebot.rule import Rule
from nonebot.typing import T_State

from services.log import logger
from utils.http_utils import AsyncHttpx

REGEX_ARG = "MEME_EXTRA_REGEX_ARG"
PARSED = "MEME_EXTRA_PARSED"

# q1.qlogo.cn 对不存在的号会返回这张默认灰头像，命中就退一档尺寸重取。
_DEFAULT_AVATAR_MD5 = "acef72340ac0e914090bd35799f5594e"


@dataclass
class ImageSource:
    """一个待取的图片：要么是 QQ 头像，要么是消息里的图片 URL。"""

    qq: str = ""
    url: str = ""
    group: str = ""
    name: str = ""


@dataclass
class ParsedArgs:
    sources: list[ImageSource] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    sender_qq: str = ""
    sender_group: str = ""


def is_qq(text: str) -> bool:
    return text.isdigit() and 11 >= len(text) >= 5


def regex(pattern: str) -> Rule:
    """匹配「命令前缀 + 关键词」，把剩下的消息段留给参数解析。"""

    def checker(event: MessageEvent, state: T_State) -> bool:
        msg = event.get_message()
        if not msg:
            return False
        first = msg[0]
        if not first.is_text():
            return False

        seg_text = str(first).lstrip()
        start = "|".join(get_driver().config.command_start)
        matched = re.match(rf"(?:{start})(?:{pattern})", seg_text, re.IGNORECASE)
        if not matched:
            return False
        # 关键词必须整词命中，否则「摸鱼」会被「摸」抢走。
        rest = seg_text[matched.end():]
        if rest and not rest[0].isspace() and not rest.startswith(("[", "@")):
            return False

        new_msg = msg.copy()
        if rest.strip():
            new_msg[0].data["text"] = rest.lstrip()
        else:
            new_msg.pop(0)
        state[REGEX_ARG] = new_msg
        return True

    return Rule(checker)


def parse_args():
    """把剩余消息拆成图片源 + 文本，存进 state。"""

    def dependency(event: MessageEvent, state: T_State):
        msg: Message = state.get(REGEX_ARG, Message())
        group = str(event.group_id) if isinstance(event, GroupMessageEvent) else ""

        parsed = ParsedArgs(sender_qq=str(event.user_id), sender_group=group)

        # 回复里的图片也算一张输入
        if event.reply:
            for seg in event.reply.message["image"]:
                url = str(seg.data.get("url", ""))
                if url:
                    parsed.sources.append(ImageSource(url=url))

        for seg in msg:
            if seg.type == "at":
                qq = str(seg.data.get("qq", ""))
                if qq:
                    parsed.sources.append(ImageSource(qq=qq, group=group))
            elif seg.type == "image":
                url = str(seg.data.get("url", ""))
                if url:
                    parsed.sources.append(ImageSource(url=url))
            elif seg.type == "text":
                raw = str(seg)
                try:
                    tokens = shlex.split(raw)
                except ValueError:
                    tokens = raw.split()
                for token in tokens:
                    if is_qq(token):
                        parsed.sources.append(ImageSource(qq=token, group=group))
                    elif token == "自己":
                        parsed.sources.append(
                            ImageSource(qq=str(event.user_id), group=group)
                        )
                    else:
                        text = unescape(token).strip()
                        if text:
                            parsed.texts.append(text)

        state[PARSED] = parsed

    return Depends(dependency)


def Parsed():
    async def dependency(state: T_State) -> ParsedArgs:
        return state.get(PARSED) or ParsedArgs()

    return Depends(dependency)


async def _download(url: str) -> Optional[bytes]:
    try:
        resp = await AsyncHttpx.get(url, timeout=20)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        logger.warning(f"[meme_extra] 下载图片失败 {url}: {e}")
        return None


async def download_avatar(qq: str) -> Optional[bytes]:
    data = await _download(f"http://q1.qlogo.cn/g?b=qq&nk={qq}&s=640")
    if data and hashlib.md5(data).hexdigest() == _DEFAULT_AVATAR_MD5:
        data = await _download(f"http://q1.qlogo.cn/g?b=qq&nk={qq}&s=100")
    return data


async def resolve_name(bot: Bot, source: ImageSource) -> str:
    """取群名片/昵称，部分表情会把它画进图里。"""
    if not source.qq:
        return ""
    try:
        if source.group:
            info = await bot.get_group_member_info(
                group_id=int(source.group), user_id=int(source.qq)
            )
            return info.get("card") or info.get("nickname") or source.qq
        info = await bot.get_stranger_info(user_id=int(source.qq))
        return info.get("nickname") or source.qq
    except Exception:
        return source.qq


async def fetch_images(bot: Bot, sources: list[ImageSource]) -> list[tuple[str, bytes]]:
    """按顺序取回图片；任意一张失败就返回空，交由上层提示。"""
    images: list[tuple[str, bytes]] = []
    for source in sources:
        if source.qq:
            data = await download_avatar(source.qq)
            name = await resolve_name(bot, source)
        else:
            data = await _download(source.url)
            name = ""
        if not data:
            return []
        images.append((name or "unknown", data))
    return images


def make_sender_source(event: MessageEvent) -> ImageSource:
    group = str(event.group_id) if isinstance(event, GroupMessageEvent) else ""
    return ImageSource(qq=str(event.user_id), group=group)
