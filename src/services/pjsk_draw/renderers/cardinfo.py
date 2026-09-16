"""查卡详情（cardinfo）出图。

原 plugins/pjsk/_models.py 里 CardInfo.toimg 的实现，连同它专用的
文字转图工具 t2i 一起迁到绘图服务。指令侧只需把 CardInfo 拍平成载荷。
"""

from __future__ import annotations

import asyncio
import datetime
import math
from typing import Optional, Tuple

import pytz
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config.path_config import FONT_PATH
from services.log import logger
from utils.imageutils import union
from utils.pjsk_paths import STATIC_PATH

from ..card import cardlarge, cardthumnail, render_card_thumbnail_tile
from ..context import get_context
from ..primitives import get_pjsk_music_jacket_cached, image_to_jpeg, run_pjsk_thread
from ..registry import register

static_path = STATIC_PATH


class _SubInfo:
    """event / music / gacha 的绘图子集，按属性访问。"""

    def __init__(self, payload: Optional[dict] = None):
        for key, value in (payload or {}).items():
            setattr(self, key, value)

    def __getattr__(self, item):
        # 缺字段时给出中性默认值，避免出图因个别字段缺失整体失败
        if item.endswith('At') or item in ('id', 'musicId', 'eventId'):
            return 0
        return ''


class CardInfoView:
    """查卡详情图需要的卡面数据（原 _models.CardInfo 的绘图子集）。"""

    _FIELDS = (
        'config', 'pjsk_type', 'id', 'characterId', 'costume3dId', 'skillId',
        'unit', 'cardRarityType', 'attr', 'isLimited', 'cardParameters', 'releaseAt',
        'charaName', 'prefix', 'gachaPhrase', 'cardSkillName', 'cardSkillDes', 'assets',
    )

    def __init__(self, payload: Optional[dict] = None):
        payload = payload or {}
        self.config = payload.get('config') or {'event': True, 'music': True, 'gacha': True}
        self.pjsk_type = int(payload.get('pjsk_type') or 0)
        self.id = int(payload.get('id') or 0)
        self.characterId = int(payload.get('characterId') or 0)
        self.costume3dId = int(payload.get('costume3dId') or 0)
        self.skillId = int(payload.get('skillId') or 0)
        self.unit = payload.get('unit') or 'none'
        self.cardRarityType = payload.get('cardRarityType') or ''
        self.attr = payload.get('attr') or ''
        self.isLimited = bool(payload.get('isLimited'))
        self.cardParameters = payload.get('cardParameters') or {}
        self.releaseAt = payload.get('releaseAt') or ''
        self.charaName = payload.get('charaName') or ''
        self.prefix = payload.get('prefix') or ''
        self.gachaPhrase = payload.get('gachaPhrase') or {}
        self.cardSkillName = payload.get('cardSkillName') or {}
        self.cardSkillDes = payload.get('cardSkillDes') or {}
        self.assets = payload.get('assets') or {'card': '', 'costume': {'hair': [], 'head': [], 'body': []}}
        self.event = _SubInfo(payload.get('event'))
        self.music = _SubInfo(payload.get('music'))
        self.gacha = _SubInfo(payload.get('gacha'))

    @classmethod
    def payload_from_card(cls, card) -> dict:
        """把 CardInfo 对象拍平成载荷（供指令侧调用）。"""
        payload = {field: getattr(card, field, None) for field in cls._FIELDS}
        for name in ('event', 'music', 'gacha'):
            sub = getattr(card, name, None)
            payload[name] = dict(vars(sub)) if sub is not None else {}
        return payload


def t2i(
    text: str,
    font_size: int = 40,
    font_color: str = "black",
    padding: Optional[Tuple[int, int, int, int]] = (0, 0, 0, 0),
    max_width: Optional[int] = None,
    wrap_type: str = "left",
    line_interval: Optional[int] = None,
) -> Image:
    """
    根据文字生成图片，仅使用思源字体，支持\n换行符的输入
    :param text: 文字内容
    :param font_size: 文字大小
    :param font_color: 文字颜色
    :param padding: 文字边距，参数顺序为上下左右
    :param max_width: 限制的文字宽度，文字超出此宽度自动换行
    :param wrap_type: 换行后文字的对齐方式（左对齐left，居中对齐center，右对齐right）
    :param line_interval: 文字有多行时的行间距，默认为字体大小的1/4
    """
    # 仿照meetwq佬的PIL工具插件imageutils的text2image方法制作的简易版
    # 工具地址(https://github.com/noneplugin/nonebot-plugin-imageutils)
    if wrap_type not in ['left', 'center', 'right']:
        raise TypeError('对齐方式参数错误！')
    lines = text.split('\n')
    if max_width is not None:
        def wrap(line, max_width):
            font = ImageFont.truetype(str(FONT_PATH / 'SourceHanSansCN-Medium.otf'), font_size)
            (_w, _), (_, _) = font.font.getsize(line)
            last_idx = 0
            for idx in range(len(line)):
                (_tmp_w, _), (_, _) = font.font.getsize(line[last_idx: idx+1])
                if _tmp_w > max_width:
                    yield line[last_idx:idx]
                    last_idx = idx
            yield line[last_idx:]
        new_lines = []
        for line in lines:
            l = wrap(line, max_width)
            new_lines.extend(l)
        lines = new_lines
    imgs = []
    width = 0
    height = 0
    line_interval = line_interval if line_interval is not None else font_size//4
    for line in lines:
        font = ImageFont.truetype(str(FONT_PATH / 'SourceHanSansCN-Medium.otf'), font_size)
        (_width, _height), (offset_x, offset_y) = font.font.getsize(line)
        img = Image.new('RGBA', (_width, _height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        draw.text((-offset_x + padding[2], -offset_y + padding[0]), line, font_color, font)
        width = _width if width < _width else width
        height += _height + line_interval
        imgs.append(img)
    height -= line_interval
    size = (width + padding[2] + padding[3], height + padding[0] + padding[1])
    pic = Image.new('RGBA', size, (255, 255, 255, 0))
    _h = 0
    for img in imgs:
        if wrap_type == 'left':
            _w = 0
        elif wrap_type == 'center':
            _w = (width - img.width) // 2
        else:
            _w = width - img.width
        pic.paste(img, (_w, _h), mask=img.split()[-1])
        _h += line_interval + img.height
    return pic


async def _prefetch_detail_assets(self: "CardInfoView"):
    """并行预取查卡详情图所需资源，避免 toimg 绘制时逐个等待下载。"""
    tasks = []

    def add(path: str, file: str):
        tasks.append(get_context().get_asset(path, file, pjsk_type=self.pjsk_type))

    # 卡面缩略图与大图
    if self.assets.get('card'):
        card_files = ['card_normal.png']
        thumb_suffixes = ['normal']
        if self.cardRarityType in ['rarity_3', 'rarity_4']:
            card_files.append('card_after_training.png')
            thumb_suffixes.append('after_training')
        for suffix in thumb_suffixes:
            add('startapp/thumbnail/chara', f'{self.assets["card"]}_{suffix}.png')
        for file in card_files:
            add(f'startapp/character/member/{self.assets["card"]}', file)

    # 衣装缩略图
    for costume_list in self.assets.get('costume', {}).values():
        for asset in costume_list:
            add('startapp/thumbnail/costume', f'{asset}.png')

    # 卡池、活动、歌曲相关图
    if self.gacha.id != 0:
        add(f'startapp/home/banner/banner_gacha{self.gacha.id}', f'banner_gacha{self.gacha.id}.png')
    if self.event.id != 0 and self.event.assetbundleName:
        add(f'ondemand/event_story/{self.event.assetbundleName}/screen_image', 'banner_event_story.png')
    if self.music.id != 0:
        tasks.append(
            get_pjsk_music_jacket_cached(
                self.music.id,
                pjsk_type=self.pjsk_type,
                mode='RGBA',
            )
        )

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

async def compose_cardinfo(self: "CardInfoView") -> Image.Image:
    """
    生成卡面的详细信息图
    """
    await _prefetch_detail_assets(self)
    _tmpcards = [{
        'id': self.id,
        'cardRarityType': self.cardRarityType,
        'assetbundleName': self.assets['card'],
        'attr': self.attr
    }]
    style_color = "#dc6496"  # 作图的主题色
    left_width = 880  # 左侧图的宽度
    left_pad = (30, 30, 40, 40)  # 左侧图的pad
    right_width = 860   # 右侧图的宽度
    right_pad = (65, 75, 50, 50)  # 右侧图的pad
    _l_w = left_width + left_pad[2] + left_pad[3]
    _r_w = right_width + right_pad[2] + right_pad[3]
    server_name = get_context().server_name(self.pjsk_type)

    def missing_asset_placeholder(text: str, size: tuple[int, int], asset_path: str) -> Image.Image:
        logger.warning(f"[{server_name}] 卡面 {self.id} 的{text}资源缺失，已使用占位图: {asset_path}")
        placeholder = Image.new('RGBA', size, (255, 255, 255, 0))
        text_img = t2i(f'{text}资源缺失', font_size=28, font_color='#999999', max_width=max(size[0] - 40, 1))
        placeholder.paste(text_img, ((size[0] - text_img.width) // 2, (size[1] - text_img.height) // 2), mask=text_img.split()[-1])
        return placeholder

    # 生成卡面标题图片title_img
    charaname_img = union(
        [t2i(self.prefix, font_color='white', max_width=int(_r_w/18*13)), t2i(self.charaName, font_color='white')],
        type='row',
        length=0,
        interval=5
    )
    unit_img = Image.open(static_path / f'pics/logo_{self.unit}.png')
    unit_img = unit_img.resize((int(_r_w/18*5), int(_r_w/18*5/unit_img.width*unit_img.height)))
    title_img = union(
        [unit_img, charaname_img],
        type='col',
        length=right_width+40,
        padding=(20,20,30,30),
        interval=35+(right_width-unit_img.width-charaname_img.width)//2,
        align_type='center',
        bk_color=style_color,
        border_type='circle',
        border_radius=_r_w//36
    )
    # 生成卡面详情图片detail_img
    tmp_imgs = []
    # 综合力
    power = sum([self.cardParameters[key] for key in self.cardParameters.keys()])
    tmp_union = union([t2i('综合力'), t2i(str(power))], type='col', length=right_width)
    tmp_imgs.append(tmp_union)
    # 综合力组成
    tmp_paramimgs = []
    tmp_union = union(
        [t2i('演奏'), t2i(str(self.cardParameters.get('param1', 0)))], type='col', length=right_width
    )
    tmp_paramimgs.append(tmp_union)
    tmp_union = union(
        [t2i('技巧'), t2i(str(self.cardParameters.get('param2', 0)))], type='col', length=right_width
    )
    tmp_paramimgs.append(tmp_union)
    tmp_union = union(
        [t2i('耐力'), t2i(str(self.cardParameters.get('param3', 0)))], type='col', length=right_width
    )
    tmp_paramimgs.append(tmp_union)
    tmp_imgs.append(union(tmp_paramimgs, length=0, interval=25, type='row'))
    # 卡面类型
    tmp_union = union(
        [t2i('类型'), t2i('限定' if self.isLimited else '普通')], type='col', length=right_width
    )
    tmp_imgs.append(tmp_union)
    # 技能名
    skillname_img = union(
        [t2i(
            f"{self.cardSkillName[each]}\n({each})", max_width=586, wrap_type='right'
        ) for each in self.cardSkillName.keys()],
        type='row',
        align_type='right',
        length=0,
        interval=10,
    )
    tmp_imgs.append(union(
        [t2i('技能名'), skillname_img], type='col', length=right_width
    ))
    # 技能效果
    skilldes_img = union(
        [t2i(
            f"{self.cardSkillDes[each]}\n({each})",
            max_width=right_width-right_pad[2]-160,
            wrap_type='right'
        ) for each in self.cardSkillDes.keys()],
        type='row',
        align_type='right',
        length=0,
        interval=10
    )
    tmp_imgs.append(union(
        [t2i('技能效果'), skilldes_img], type='col', length=right_width
    ))
    # 招募语
    if len(self.gachaPhrase) > 0:
        gachahrase_img = union(
            [t2i(
                f"{self.gachaPhrase[each]}\n({each})", max_width=586, wrap_type='right'
            ) for each in self.gachaPhrase.keys()],
            type='row',
            align_type='right',
            length=0,
            interval=10
        )
        tmp_imgs.append(union(
            [t2i('招募语'), gachahrase_img], type='col', length=right_width
        ))
    # 发布时间
    server_name = get_context().server_name(self.pjsk_type)
    tmp_union = union([t2i('发布时间'), t2i(f'{self.releaseAt}({server_name.upper()})')], type='col', length=right_width)
    tmp_imgs.append(tmp_union)
    # 卡面缩略图
    if self.cardRarityType in ['rarity_3', 'rarity_4']:
        cardthumnail_pic = union(
            [
                render_card_thumbnail_tile(await cardthumnail(self.id, False, _tmpcards, pjsk_type=self.pjsk_type), size=180),
                render_card_thumbnail_tile(await cardthumnail(self.id, True, _tmpcards, pjsk_type=self.pjsk_type), size=180)
            ], type='col', length=0, interval=30)
    else:
        cardthumnail_pic = render_card_thumbnail_tile(await cardthumnail(self.id, False, pjsk_type=self.pjsk_type), size=180)
    tmp_imgs.append(union([t2i('缩略图'), cardthumnail_pic], type='col', length=right_width))
    # 衣装缩略图
    single_costume_pics = []
    for key in self.assets['costume'].keys():
        for i in self.assets['costume'][key]:
            costume_asset = await get_context().get_asset(
                'startapp/thumbnail/costume', f'{i}.png', pjsk_type=self.pjsk_type
            )
            if costume_asset is None:
                server_name = get_context().server_name(self.pjsk_type)
                logger.warning(
                    f"[{server_name}] 卡面 {self.id} 的衣装缩略图缺失，已跳过: "
                    f"startapp/thumbnail/costume/{i}.png"
                )
                continue
            tmp = costume_asset.resize((180, 180))
            _type = {'hair': '发型', 'head': '发饰', 'body': '服装'}
            single_costume_pics.append(
                union([tmp, t2i(_type[key])], type='row', length=0, interval=10)
            )
    _cnt = math.ceil(len(single_costume_pics) / 2)
    if _cnt > 0:
        costume_pic = union(
            single_costume_pics[0: 2], type='col', length=0, interval=30
        )
        for i in range(_cnt-1):
            tmp_union_pic = union(
                single_costume_pics[i+2: i+4], type='col', length=0, interval=30
            )
            costume_pic = union([costume_pic, tmp_union_pic], type='row', length=0, interval=30)

        tmp_imgs.append(union([t2i('衣装缩略图'), costume_pic], type='col', length=right_width))

    tmp_imgs.append(union([t2i('ID'), t2i(str(self.id))], type='col', length=right_width))

    detail_img = union(
        tmp_imgs,
        type="row",
        interval=43,
        interval_size=3,
        interval_color="#efd6e4",
        padding=right_pad,
        border_size=3,
        border_color="#f0c9dc",
        border_type="circle",
        bk_color='white'
    )

    # 生成卡面大图cardlarge_img
    if self.cardRarityType in ['rarity_3', 'rarity_4']:
        cardlarge_img = union(
            [
                (await cardlarge(self.id, False, _tmpcards, pjsk_type=self.pjsk_type)).resize((_l_w, int(_l_w*0.61))),
                (await cardlarge(self.id, True, _tmpcards, pjsk_type=self.pjsk_type)).resize((_l_w, int(_l_w*0.61))),
            ], type='row', length=0, interval=30)
    else:
        cardlarge_img = (await cardlarge(self.id, False, _tmpcards, pjsk_type=self.pjsk_type)).resize((_l_w, int(_l_w*0.61)))

    # 生成gacha大图gacha_img
    gacha_img = None
    if self.gacha.id != 0:
        banner_path = f'startapp/home/banner/banner_gacha{self.gacha.id}'
        banner_raw = f'banner_gacha{self.gacha.id}.png'
        bannerpic = await get_context().get_asset(
            banner_path, banner_raw,
            pjsk_type=self.pjsk_type
        )
        if bannerpic is None:
            bannerpic = missing_asset_placeholder('卡池横幅', (left_width, 248), f'{banner_path}/{banner_raw}')
        else:
            bannerpic = bannerpic.resize((left_width, int(left_width / bannerpic.width * bannerpic.height)))
        timepic = union(
            [t2i('开始时间：'+self.gacha.startAt, font_size=25),
             t2i('结束时间：'+self.gacha.endAt, font_size=25)],
            type='col',
            length=left_width,
        )
        if (  # 若卡面为限定卡，当卡池也为当期池时，认定池子为限定池
            self.isLimited
            and self.gacha.startAt == self.releaseAt
            and self.gacha.gachaCardRarityRateGroupId != 3
        ):
            gachatype = "期间限定"
        else:
            gachatype = {
                "1": "常规", "3": "fes限定", "4": "生日限定"
            }.get(str(self.gacha.gachaCardRarityRateGroupId), "")
        gachanamepic = union(
            [t2i(self.gacha.name, max_width=left_width), t2i(f"{gachatype}  ID:{self.gacha.id}", font_size=30)],
            type='row',
            length=0,
            interval=10
        )
        gacha_img = union(
            [bannerpic, gachanamepic, timepic],
            type='row',
            padding=left_pad,
            interval=40,
            bk_color='white',
            border_color='#f0c9dc',
            border_size=3,
            border_type='circle'
        )

    # 生成event大图event_img
    event_img = None
    if self.event.id != 0:
        banner_path = f'ondemand/event_story/{self.event.assetbundleName}/screen_image'
        banner_raw = 'banner_event_story.png'
        bannerpic = await get_context().get_asset(
            banner_path, banner_raw,
            pjsk_type=self.pjsk_type
        )
        if bannerpic is None:
            bannerpic = missing_asset_placeholder('活动横幅', (left_width, 248), f'{banner_path}/{banner_raw}')
        else:
            bannerpic = bannerpic.resize((left_width, int(left_width / bannerpic.width * bannerpic.height)))
        eventtype = {"marathon": "马拉松(累积点数)", "cheerful_carnival": "欢乐嘉年华(5v5)"}.get(self.event.eventType, "")
        eventnamepic = union(
            [t2i(self.event.name, max_width=left_width), t2i(f"{eventtype}  ID:{self.event.id}", font_size=30)],
            type='row',
            length=0,
            interval=10
        )
        timepic = union(
            [t2i('开始时间：'+self.event.startAt, font_size=30),
             t2i('结束时间：'+self.event.aggregateAt, font_size=30)],
            type='row',
            length=0,
            interval=40
        )
        bonusechara_pic = []
        if hasattr(self.event, 'bonusechara'):
            for bonusechara in self.event.bonusechara:
                unitcolor = {
                    'piapro': '#000000',
                    'light_sound': '#4455dd',
                    'idol': '#88dd44',
                    'street': '#ee1166',
                    'theme_park': '#ff9900',
                    'school_refusal': '#884499',
                }
                try:
                    # 活动角色边框显示组合色
                    # 这里不是很懂为什么需要经过多次放缩才能让图片锯齿没那么明显，但总之试出来了(ˉ▽ˉ；)...
                    _chr_pic_path = static_path / f'chara/{bonusechara["asset"]}'
                    if not _chr_pic_path.exists():
                        continue
                    _chr_pic = Image.open(_chr_pic_path).resize((110, 110))
                    _bk = Image.new('RGBA', (130, 130), color=unitcolor.get(bonusechara.get('unit'), '#000000'))
                    _bk.paste(_chr_pic, (10, 10), mask=_chr_pic.split()[-1])
                    mask = Image.new("L", _bk.size, 0)
                    ImageDraw.Draw(mask).ellipse((1, 1, _bk.size[0] - 2, _bk.size[1] - 2), 255)
                    mask = mask.filter(ImageFilter.GaussianBlur(0))
                    _bk.putalpha(mask)
                    bonusechara_pic.append(_bk.resize((65, 65)).copy())
                except:
                    continue

        charapic = union(bonusechara_pic, type='col', length=0, interval=10)

        try:
            attrpic_path = static_path / f'chara/icon_attribute_{self.event.bonuseattr}.png'
            if not attrpic_path.exists():
                raise FileNotFoundError()
            attrpic = Image.open(attrpic_path).resize((60, 60))
        except:
            attrpic = Image.new('RGBA', (60, 60), (255, 255, 255, 0))

        _ = union([attrpic, charapic], type='row', interval=10, align_type='right')
        _ = union([timepic, _], type='col', interval=60, length=left_width)
        event_img = union(
            [bannerpic, eventnamepic, _],
            padding=left_pad,
            interval=40,
            type='row',
            bk_color='white',
            border_type='circle',
            border_size=3,
            border_color='#a19d9e'
        )

    # 生成music大图music_img
    music_img = None
    if self.music.id != 0:
        # 图、名称、时间
        jacketpic = await get_pjsk_music_jacket_cached(
            self.music.id,
            pjsk_type=self.pjsk_type,
            mode='RGBA',
            size=(280, 280),
        )
        if jacketpic is None:
            jacket_name = f'jacket_s_{str(self.music.id).zfill(3)}'
            jacketpic = missing_asset_placeholder(
                '歌曲封面',
                (280, 280),
                f'startapp/music/jacket/{jacket_name}/{jacket_name}.png',
            )

        musicnamepic = t2i(self.music.title, font_size=50, max_width=left_width)
        timepic = t2i('上线时间：' + datetime.datetime.fromtimestamp(
            self.music.publishedAt / 1000, pytz.timezone('Asia/Shanghai')
        ).strftime('%Y/%m/%d %H:%M:%S'))
        _m_w = left_width - 280 - left_pad[2]
        authorpic = union(
            [t2i(f'作词： {self.music.lyricist}', font_size=40, max_width=_m_w),
            t2i(f'作曲： {self.music.composer}', font_size=40, max_width=_m_w),
            t2i(f'编曲： {self.music.arranger}', font_size=40, max_width=_m_w)],
            type='row',
            length=0,
            interval=5,
            align_type='left'
        )
        music_img = union(
            [union(
                [jacketpic, authorpic],
                type='col',
                interval=50,
                length=left_width
            ), union(
                [musicnamepic, t2i(f"ID:{self.music.id}", font_size=30)],
                type='row',
                interval=10,
            ), timepic],
            type='row',
            interval=40,
            padding=left_pad,
            bk_color='white',
            border_type='circle',
            border_size=3,
            border_color='#a19d9e'
        )

    _interval = 60
    left_imgs = [cardlarge_img]
    right_imgs = [title_img, detail_img]
    # gacha图放在左边
    if gacha_img:
        _k = '当期卡池' if self.releaseAt == self.gacha.startAt else '初次可得卡池'
        _t = t2i(_k,font_size=50,font_color='white')
        _i = Image.new('RGBA', (_l_w, 70))
        ImageDraw.Draw(_i).rounded_rectangle((0, 0, _i.width, _i.height), 25, style_color)
        _i.paste(_t,((_l_w-50*len(_k))//2, 10),mask=_t.split()[-1])
        left_imgs.append(_i.copy())
        left_imgs.append(gacha_img)
    # event图放在左边
    if event_img:
        _t = t2i('活动', font_size=50, font_color='white')
        _i = Image.new('RGBA', (_l_w, 70))
        _d = ImageDraw.Draw(_i)
        _d.rounded_rectangle((0, 0, _i.width, _i.height), 25, style_color)
        _i.paste(_t, ((_l_w-100)//2, 10), mask=_t.split()[-1])
        left_imgs.append(_i.copy())
        left_imgs.append(event_img)
    # music图根据左右侧图长度差距决定放在哪边
    if music_img:
        _i = Image.new('RGBA', (_l_w, 70))
        ImageDraw.Draw(_i).rounded_rectangle((0, 0, _i.width, _i.height), 25, style_color)
        _t = t2i('歌曲', font_size=50, font_color='white')
        _i.paste(_t, ((_l_w-100)//2, 10), mask=_t.split()[-1])
        if (
            sum(i.height for i in left_imgs) + _interval * (len(right_imgs)-1) >
            sum(i.height for i in right_imgs) + _interval * (len(left_imgs)-1) + 80
        ):
            right_imgs.append(_i.copy())
            right_imgs.append(music_img)
        else:
            left_imgs.append(_i.copy())
            left_imgs.append(music_img)
    # 合成左侧图
    left_img = union(
        left_imgs,
        type='row',
        interval=_interval,
        length=_l_w,
        align_type='left',
    )
    # 合成右侧图
    right_img = union(right_imgs, type='row', interval=_interval, align_type='left')
    # 生成最终的info_img
    # info_pad留白，用于自行留下水印
    info_pad = (60, 180)
    info_width = int(sum([left_img.width, right_img.width]) + info_pad[0])
    info_height = int(max([left_img.height, right_img.height]))
    info_img = Image.open(static_path / 'pics/cardinfo.png').resize((info_width+info_pad[0]*2, info_height+info_pad[1]*2))
    info_img.paste(left_img, info_pad, mask=left_img.split()[-1])
    info_img.paste(right_img, (left_img.width + info_pad[0]*2, info_pad[1]), mask=right_img.split()[-1])

    badge_img = Image.open(static_path / 'pics/cardinfo_badge.png')
    badge_img = badge_img.resize((right_img.width//2, int(badge_img.height/badge_img.width*right_img.width//2)))
    info_img.paste(badge_img, (info_pad[0], int(info_pad[1]/3*2 - badge_img.height)), mask=badge_img.split()[-1])
    # watermark_img = t2i('DESIGNED by KNDBOT in California', font_size=35, font_color=style_color)
    # info_img.paste(
    #     watermark_img,
    #     (info_img.width-watermark_img.width-info_pad[0], info_img.height-watermark_img.height-info_pad[1]//6),
    #     mask=watermark_img.split()[-1]
    # )
    return info_img


@register("cardinfo")
async def render_cardinfo(payload: dict) -> bytes:
    """查卡详情图。载荷：CardInfoView 字段（见 payload_from_card）。"""
    pic = await compose_cardinfo(CardInfoView(payload))
    # 原实现是 pic.convert('RGB').save(file, quality=85)，保持不加水印。
    return await run_pjsk_thread(image_to_jpeg, pic.convert('RGB'), quality=85, watermark=False)
