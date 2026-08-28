"""上游 meme-generator 的增量表情。

只注册「上游有、kndbot 没有」的表情；老的 petpet / memes 指令连同改过的底图
原样保留。哪些算已有由 scripts/gen_meme_exclusions.py 静态算出，见 _exclusions.py。
"""

import math
from typing import Optional

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import GROUP, Bot, MessageEvent, MessageSegment
from nonebot.matcher import Matcher
from nonebot.typing import T_Handler
from nonebot.utils import run_sync

from services.log import logger
from utils.imageutils import BuildImage, Text2Image
from utils.limit_utils import access_cd, access_count
from utils.meme_catalog import render_catalog

from . import _engine
from ._depends import (
    Parsed,
    ParsedArgs,
    fetch_images,
    make_sender_source,
    parse_args,
    regex,
)
from ._exclusions import EXCLUDED_MEME_KEYS

__plugin_name__ = "更多表情包"
__plugin_type__ = "图片类"
__plugin_version__ = 0.1
__plugin_usage__ = """
usage：
    来自 meme-generator 的扩展表情，是原有「头像表情包」「表情包制作」之外新增的部分
    发送 "更多表情" 查看全部支持的指令

    触发方式：指令 + @某人 / qq号 / 自己 / 图片 / 文字
    也可以只发指令：需要图片时用你自己的头像，需要文字时用该表情的默认文案
    示例：
        小丑                 :用自己的头像
        小丑 @某人
        揍 @甲 @乙
        安安说 今天也要加油
        奖状 优秀员工 张三 2026年
""".strip()
__plugin_settings__ = {
    "cmd": ["更多表情", "更多表情包", "扩展表情"],
}
__plugin_cd_limit__ = {"cd": 10, "rst": "别急，[cd]s后再用！"}
__plugin_count_limit__ = {
    "max_count": 20,
    "limit_type": "user",
    "rst": "今天已经玩够了吧，还请明天再继续呢[at]",
}

SPECS = _engine.load_specs(set(EXCLUDED_MEME_KEYS))

if not _engine.MEME_GENERATOR_AVAILABLE:
    logger.warning(f"[meme_extra] meme-generator 不可用，扩展表情未注册：{_engine.IMPORT_ERROR}")
else:
    logger.info(f"[meme_extra] meme-generator {_engine.version()}，注册 {len(SPECS)} 个扩展表情")


help_cmd = on_command(
    "更多表情",
    aliases={"更多表情包", "扩展表情", "扩展表情包"},
    permission=GROUP,
    block=True,
    priority=4,
)


@run_sync
def _render_help() -> Optional[bytes]:
    if not SPECS:
        return None
    entries = [f"{i + 1}. " + "/".join(s.keywords) for i, s in enumerate(SPECS)]
    columns = 4
    per_column = math.ceil(len(entries) / columns)
    chunks = [entries[i:i + per_column] for i in range(0, len(entries), per_column)]

    title = Text2Image.from_text(
        f"扩展表情（共 {len(SPECS)} 个）\n"
        "触发方式：指令 + @某人 / qq号 / 自己 / 图片 / 文字\n"
        "也可只发指令：默认用你的头像或该表情的默认文案",
        30,
        weight="bold",
    ).to_image(padding=(20, 10))

    column_imgs = [
        Text2Image.from_text("\n".join(chunk), 26).to_image(padding=(20, 10))
        for chunk in chunks
    ]
    width = max(title.width, sum(img.width for img in column_imgs))
    height = title.height + max(img.height for img in column_imgs)
    canvas = BuildImage.new("RGBA", (width, height), "white")
    canvas.paste(title, alpha=True)
    x = 0
    for img in column_imgs:
        canvas.paste(img, (x, title.height), alpha=True)
        x += img.width
    return canvas.save_jpg().getvalue()


@help_cmd.handle()
async def _(matcher: Matcher):
    if not SPECS:
        await matcher.finish("扩展表情当前不可用（meme-generator 未就绪）")
    # 和「头像表情包」「表情包列表」共用一张总表。
    try:
        img = await render_catalog()
    except Exception as e:
        logger.warning(f"生成表情包总目录失败，回退到扩展表情列表: {e}")
        img = await _render_help()
    if img:
        await matcher.finish(MessageSegment.image(img))


def _check_counts(spec: _engine.MemeSpec, n_images: int, texts: list[str]) -> tuple[bool, str, list[str]]:
    """校验图片/文字数量，顺带把超额文字合并成一段。"""
    if n_images < spec.min_images or n_images > spec.max_images:
        if spec.min_images == spec.max_images:
            need = f"{spec.min_images} 张图片"
        else:
            need = f"{spec.min_images}~{spec.max_images} 张图片"
        return False, f"需要 {need}，当前给了 {n_images} 张", texts

    # 只能收一段文字时，把空格拆出来的多段合回去，符合「悲报 今天 要 加班」的直觉。
    if len(texts) > spec.max_texts and spec.max_texts == 1:
        texts = [" ".join(texts)]
    if not texts and spec.min_texts > 0 and spec.default_texts:
        texts = list(spec.default_texts)
    if len(texts) < spec.min_texts or len(texts) > spec.max_texts:
        if spec.min_texts == spec.max_texts:
            need = f"{spec.min_texts} 段文字"
        else:
            need = f"{spec.min_texts}~{spec.max_texts} 段文字"
        return False, f"需要 {need}，当前给了 {len(texts)} 段", texts
    return True, "", texts


def _handler(spec: _engine.MemeSpec) -> T_Handler:
    async def handle(matcher: Matcher, bot: Bot, event: MessageEvent,
                     parsed: ParsedArgs = Parsed()):
        sources = list(parsed.sources)
        texts = list(parsed.texts)

        # 裸指令也要能用：没给图就拿发送者头像顶上，没给文字就用表情自带的默认文案。
        if not sources and spec.min_images > 0:
            sources = [make_sender_source(event)] * spec.min_images
        if not texts and spec.min_texts > 0:
            if spec.default_texts:
                texts = list(spec.default_texts)
            else:
                # 少数表情没有默认文案，这时只能提示，否则引擎必定报文字数不足。
                await matcher.finish(
                    f"{'/'.join(spec.keywords)}：需要 {spec.min_texts} 段文字，"
                    f"例如「{spec.keywords[0]} 内容」"
                )

        ok, reason, texts = _check_counts(spec, len(sources), texts)
        if not ok:
            await matcher.finish(f"{'/'.join(spec.keywords)}：{reason}")

        images = await fetch_images(bot, sources)
        if len(images) != len(sources):
            await matcher.finish("图片获取失败了，稍后再试试")

        data, error = await _engine.generate(spec.key, images, texts)
        if data is None:
            await matcher.finish(error or "表情生成失败")

        access_count(matcher.plugin_name, event)
        access_cd(matcher.plugin_name, event)
        await matcher.finish(MessageSegment.image(data))

    return handle


def create_matchers() -> None:
    for spec in SPECS:
        pattern = "|".join(sorted(spec.keywords, key=len, reverse=True))
        # priority 6：排在 petpet / memes（都是 5）之后，
        # 万一静态排除表漏了什么，运行时也由老插件优先接管。
        on_message(
            regex(pattern),
            block=True,
            priority=6,
        ).append_handler(_handler(spec), parameterless=[parse_args()])


if SPECS:
    create_matchers()
