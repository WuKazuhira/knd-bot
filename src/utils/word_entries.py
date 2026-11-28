from io import BytesIO
import re
from pathlib import Path
from typing import Optional

from asyncpg import PostgresError
import imagehash
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from PIL import Image, UnidentifiedImageError

from services.db_context import db
from services.log import logger
from utils.http_utils import AsyncHttpx
from utils.message_builder import image

MAX_WORD_IMAGE_BYTES = 10 * 1024 * 1024

ANSWER_PLACEHOLDER = re.compile(r"\[(image|at|face):placeholder_\d+\]")
QUESTION_PLACEHOLDER = re.compile(r"\[(image|at|face):[^\]]*\]")


class InvalidWordPattern(ValueError):
    pass


async def validate_word_pattern(pattern: str) -> None:
    if len(pattern) > 256:
        raise InvalidWordPattern("正则表达式最多 256 个字符。")
    try:
        re.compile(pattern)
        async with db.transaction():
            await db.status("SET LOCAL statement_timeout = '500ms'")
            await db.scalar(db.text("SELECT '' ~ :pattern"), pattern=pattern)
    except (re.error, OverflowError, PostgresError, TimeoutError) as exc:
        raise InvalidWordPattern("正则表达式无效或执行超时。") from exc


async def regex_matches(query, *, first: bool = False):
    try:
        async with db.transaction():
            await db.status("SET LOCAL statement_timeout = '500ms'")
            if first:
                return await query.gino.first()
            return await query.gino.all()
    except (PostgresError, TimeoutError) as exc:
        logger.warning(f"查询正则词条失败：{type(exc).__name__}")
        return None


def render_placeholders(
    text: str,
    values: str,
    pattern: re.Pattern,
    cache_dir: Path,
    *,
    mention: bool = False,
) -> Message:
    message = Message()
    start = 0
    # 旧词条后缀可能重号，按顺序配对
    for match, value in zip(pattern.finditer(text), values.split(",")):
        if match.start() > start:
            message.append(MessageSegment.text(text[start:match.start()]))
        kind = match[1]
        if kind == "image":
            segment = image(cache_dir / value)
            if isinstance(segment, MessageSegment):
                message.append(segment)
        elif kind == "face":
            message.append(MessageSegment.face(value))
        elif mention and value.isascii() and value.isdecimal():
            message.append(MessageSegment.at(value))
        else:
            message.append(MessageSegment.text(f"[at:{value}]"))
        start = match.end()
    if start < len(text):
        message.append(MessageSegment.text(text[start:]))
    return message


async def image_hash_from_url(url: str, max_bytes: int = MAX_WORD_IMAGE_BYTES) -> Optional[str]:
    """下载图片并计算平均哈希，限制体积与像素防 OOM。"""
    try:
        resp = await AsyncHttpx.get(url, timeout=15)
        content = resp.content
    except Exception:
        return None
    if not content or len(content) > max_bytes:
        return None
    try:
        with Image.open(BytesIO(content)) as img:
            return str(imagehash.average_hash(img))
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError):
        return None
