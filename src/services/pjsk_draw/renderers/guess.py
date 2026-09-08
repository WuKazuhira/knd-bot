"""猜曲（guess）出图：谱面 / 曲绘 / 卡面截取与歌词标注。

原 plugins/pjsk/guess/_data_source.py 的绘制部分。
题面与答案图由同一次随机裁剪产出，所以这些任务一次返回两张图
（is_tip 提示图只返回一张）。
"""

from __future__ import annotations

import random
from typing import List

from PIL import Image, ImageDraw

from utils.imageutils import encode_image_bytes, text2image
from utils.pjsk_paths import ONDEMAND_PATH

from ..context import get_context
from ..primitives import run_pjsk_thread
from ..registry import register

data_path = ONDEMAND_PATH


def _jpeg(img: Image.Image, quality: int) -> bytes:
    """猜曲截图不加水印，避免遮挡题面。"""
    return encode_image_bytes(img, image_format='JPEG', quality=quality, watermark=False)


def cutChart(musicid: int, is_tip: bool = False, pjsk_type: int = 0) -> List[bytes]:
    """
    裁剪谱面图片
    """
    server_name = get_context().server_name(pjsk_type)
    img = Image.open(data_path / server_name / f'charts/moe/{musicid}/master.jpg')
    row = round((img.size[0] - 93.254) / 280.8)
    # 截取谱面
    if is_tip:
        rannums = random.sample(range(1, row+1), k=2)
        img1 = img.crop((
            int(94 + 280.8 * (rannums[0] - 1)), 48,
            int(94 + 280.8 * (rannums[0] - 1) + 190), img.size[1] - 295
        ))
        img2 = img.crop((
            int(94 + 280.8 * (rannums[1] - 1)), 48,
            int(94 + 280.8 * (rannums[1] - 1) + 190), img.size[1] - 295
        ))
        # 合并谱面图片并保存
        final = Image.new('RGB', (410, img.size[1] - 323), (255, 255, 255))
        final.paste(img2, (10, 10))
        final.paste(img1, (210, 10))
        return [_jpeg(final, quality=60)]
    else:
        rannum = random.randint(2, row - 1)
        newimg = img.crop((
            int(94 + 280.8 * (rannum - 1)), 48, int(94 + 280.8 * (rannum - 1) + 190), img.size[1] - 295)
        )
        ran1 = (0, 0, 190, int(newimg.size[1] / 2) + 20)
        ran2 = (0, int(newimg.size[1] / 2) - 20, 190, newimg.size[1])
        img1 = newimg.crop(ran1)
        img2 = newimg.crop(ran2)
        # 合并谱面图片并保存
        final = Image.new('RGB', (410, int(img.size[1] / 2) - 10), (255, 255, 255))
        final.paste(img2, (10, 0))
        final.paste(img1, (210, -26))
        question = _jpeg(final, quality=60)
        # 标识谱面区域
        ran = int(94 + 280.8 * (rannum - 1)), 48
        size = 190, img.size[1] - 295 - 48
        draw = ImageDraw.Draw(img)
        width = 5
        draw.line(
            [
                (ran[0] - width, ran[1] - width), (ran[0] + size[0] + width, ran[1] - width),
                (ran[0] + size[0] + width, ran[1] + size[1] + width),
                (ran[0] - width, ran[1] + size[1] + width), (ran[0] - width, ran[1] - width)
            ],
            fill='red', width=width
        )
        return [question, _jpeg(img, quality=50)]


def cutJacket(
    asset: str, size: int = 140,
    isbw: bool = False, is_tip: bool = False, pjsk_type: int = 0
) -> List[bytes]:
    """
    裁剪曲绘图片
    """
    server_name = get_context().server_name(pjsk_type)
    img = Image.open(data_path / server_name / f'startapp/music/jacket/{asset}/{asset}.png')
    img = img.convert('RGB')
    ran1 = random.randint(0, img.size[0] - size)
    ran2 = random.randint(0, img.size[1] - size)
    draw = ImageDraw.Draw(img)
    width = 3
    draw.line(
        [
            (ran1-width,ran2-width), (ran1+size+width,ran2-width), (ran1+size+width,ran2+size+width),
            (ran1-width,ran2+size+width), (ran1-width,ran2-width)
        ],
        fill='red', width=width
    )
    if is_tip:
        img = img.crop((ran1, ran2, ran1 + size, ran2 + size))
        if isbw:
            img = img.convert("L")
        return [_jpeg(img, quality=60)]
    else:
        answer = _jpeg(img, quality=50)
        img = img.crop((ran1, ran2, ran1 + size, ran2 + size))
        if isbw:
            img = img.convert("L")
        return [_jpeg(img, quality=60), answer]


def cutCard(
    asset: str, rarityType: str,
    size: int = 250,
    isbw: bool = False, is_tip: bool = False, pjsk_type: int = 0
) -> List[bytes]:
    """
    裁剪卡面
    """
    server_name = get_context().server_name(pjsk_type)
    if rarityType == 'rarity_birthday':
        path = data_path / server_name / f'startapp/character/member/{asset}/card_normal.png'
    else:
        if random.randint(0, 1) == 1:
            path = data_path / server_name / f'startapp/character/member/{asset}/card_after_training.png'
        else:
            path = data_path / server_name / f'startapp/character/member/{asset}/card_normal.png'
    img = Image.open(path)
    img = img.convert('RGB')
    ran1 = random.randint(0, img.size[0] - size)
    ran2 = random.randint(0, img.size[1] - size)
    draw = ImageDraw.Draw(img)
    width = 3
    draw.line(
        [
            (ran1 - width, ran2 - width), (ran1 + size + width, ran2 - width),
            (ran1 + size + width, ran2 + size + width),
            (ran1 - width, ran2 + size + width), (ran1 - width, ran2 - width)
        ],
        fill='red', width=width
    )
    if is_tip:
        img = img.crop((ran1, ran2, ran1 + size, ran2 + size))
        if isbw:
            img = img.convert("L")
        return [_jpeg(img, quality=60)]
    else:
        answer = _jpeg(img, quality=50)
        img = img.crop((ran1, ran2, ran1 + size, ran2 + size))
        if isbw:
            img = img.convert("L")
        return [_jpeg(img, quality=60), answer]


def cutLyrics(lines: List[str], line_num: int, asset: str, pjsk_type: int = 0) -> bytes:
    """裁切歌词：把选中的两行标红，并拼上曲绘。

    选行和读取歌词属于出题逻辑，留在指令侧；这里只负责画。
    """
    server_name = get_context().server_name(pjsk_type)
    _start_index = max(line_num - 6, 0)
    end_lines = lines[_start_index: line_num+6]
    img = text2image('\n'.join(end_lines), fontsize=25)
    img = img.convert('RGB')
    draw = ImageDraw.Draw(img)
    line_width = 3
    x10, x11 = 10, 25*len(lines[line_num]) + 10
    x20, x21 = 10, 25*len(lines[line_num+1]) + 10
    y10 = y11 = (img.height-20)*(line_num+1 if _start_index == 0 else 7)//len(end_lines) + 10
    y20 = y21 = (img.height-20)*(line_num+2 if _start_index == 0 else 8)//len(end_lines) + 10
    draw.line(
        [
            (x10 - line_width, y10 - line_width), (x11 + line_width, y11 - line_width),
        ],
        fill='red', width=line_width
    )
    draw.line(
        [
            (x20 - line_width, y20 - line_width), (x21 + line_width, y21 - line_width),
        ],
        fill='red', width=line_width
    )
    jacket = Image.open(data_path / server_name / f'startapp/music/jacket/{asset}/{asset}.png').resize((370, 370))
    max_w = max(jacket.width, img.width)
    size = (max_w + 20, jacket.height+img.height + 30)
    bk = Image.new("RGB",size=size, color='white')
    bk.paste(jacket, (10 + (max_w-jacket.width)//2, 10))
    bk.paste(img, (10 + (max_w-img.width)//2, 20 + jacket.height))
    return _jpeg(bk, quality=50)


@register("guess_chart")
async def render_guess_chart(payload: dict) -> List[bytes]:
    """谱面截取。载荷：music_id、is_tip、pjsk_type。

    谱面底图缺失时先生成（moe 谱面图也归绘图服务管）。
    """
    music_id = int(payload["music_id"])
    pjsk_type = int(payload.get("pjsk_type", 0))
    chart_path = data_path / get_context().server_name(pjsk_type) / f'charts/moe/{music_id}' / 'master.jpg'
    if not chart_path.exists():
        from .mappreview import moe2img

        await moe2img(music_id, 'master', pjsk_type=pjsk_type)
    return await run_pjsk_thread(
        cutChart,
        music_id,
        bool(payload.get("is_tip")),
        pjsk_type,
    )


@register("guess_jacket")
async def render_guess_jacket(payload: dict) -> List[bytes]:
    """曲绘截取。载荷：asset、size、is_bw、is_tip、pjsk_type。"""
    return await run_pjsk_thread(
        cutJacket,
        payload["asset"],
        int(payload.get("size", 140)),
        bool(payload.get("is_bw")),
        bool(payload.get("is_tip")),
        int(payload.get("pjsk_type", 0)),
    )


@register("guess_card")
async def render_guess_card(payload: dict) -> List[bytes]:
    """卡面截取。载荷：asset、rarity_type、size、is_bw、is_tip、pjsk_type。"""
    return await run_pjsk_thread(
        cutCard,
        payload["asset"],
        payload.get("rarity_type") or '',
        int(payload.get("size", 250)),
        bool(payload.get("is_bw")),
        bool(payload.get("is_tip")),
        int(payload.get("pjsk_type", 0)),
    )


@register("guess_lyrics")
async def render_guess_lyrics(payload: dict) -> bytes:
    """歌词题图。载荷：lines、line_num、asset、pjsk_type。"""
    return await run_pjsk_thread(
        cutLyrics,
        list(payload.get("lines") or []),
        int(payload.get("line_num", 0)),
        payload["asset"],
        int(payload.get("pjsk_type", 0)),
    )
