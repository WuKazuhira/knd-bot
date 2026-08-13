import asyncio
import io
import json
import os
import re
import uuid
from pathlib import Path
from typing import Optional

import cairosvg
from PIL import Image, ImageDraw, ImageFont

from config.path_config import FONT_PATH
from utils.http_utils import AsyncHttpx

from .._autoask import pjsk_update_manager
from .._config import CHART_PREVIEW_BASE_URL, SERVER_MAP, data_path
from .._map_utils import chart
from .._utils import load_master_data, run_pjsk_thread

# 常量定义
alpha = 125
note_image_width = 35
note_image_height = 18
fix_note_image_width = 2
note_width = note_image_width + fix_note_image_width
delta_unit_width = 120
image_left_right = 60
image_top_bottom = 60
unit_width = note_width * 12
unit_height = int(note_image_height * (64 + 48))
music_info_key_word = {'bpm': r'#BPM01: (\d*)'}
note_sizes = {
    'easy': 2.0,
    'normal': 1.5,
    'hard': 1.25,
    'expert': 1.0,
    'master': 0.875,
    'append': 0.875,  # append 难度使用与 master 相同的缩放比例
}
_GENERATION_TASKS = {}


def _clear_generation_task(key, task):
    if _GENERATION_TASKS.get(key) is task:
        _GENERATION_TASKS.pop(key, None)


async def _run_generation_single_flight(key, factory):
    task = _GENERATION_TASKS.get(key)
    if task is None:
        task = asyncio.create_task(factory())
        _GENERATION_TASKS[key] = task
        task.add_done_callback(lambda completed: _clear_generation_task(key, completed))
    return await asyncio.shield(task)


def _local_file_href(path: Path) -> str:
    return path.resolve().as_uri()


# 本地 moe 谱面预览
async def getmoechart(musicid, difficulty, withSkill=False, pjsk_type: int = 0) -> Optional[Path]:
    await moe2img(musicid, difficulty, withSkill, pjsk_type=pjsk_type)
    fix = '_skill' if withSkill else ''
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    file = data_path / server_name / f'charts/moe/{musicid}/{difficulty}{fix}.jpg'
    return file if file.exists() else None


# 本地 sus 谱面预览（已废弃）
async def getlocalchart(musicid, difficulty, pjsk_type: int = 0) -> Optional[Path]:
    await sus2img(musicid, difficulty, pjsk_type=pjsk_type)
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    if os.path.exists(data_path / server_name / f'charts/sus/{musicid}/{difficulty}.png'):
        return data_path / server_name / f'charts/sus/{musicid}/{difficulty}.png'
    else:
        return None


# skviewer 谱面预览
async def getskvchart(musicid, difficulty, pjsk_type: int = 0) -> Optional[Path]:
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    if os.path.exists(data_path / server_name / f'charts/SekaiViewer/{musicid}/{difficulty}.png'):  # 本地有缓存
        return data_path / server_name / f'charts/SekaiViewer/{musicid}/{difficulty}.png'
    else:  # 本地无缓存
        if await downloadviewerchart(musicid, difficulty, pjsk_type=pjsk_type):  # sekai viewer下载成功
            return data_path / server_name / f'charts/SekaiViewer/{musicid}/{difficulty}.png'
        return None


# sdvx 谱面预览
async def getsdvxchart(musicid, difficulty, pjsk_type: int = 0) -> Optional[Path]:
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    if difficulty in ['master', 'expert', 'append']:
        if os.path.exists(data_path / server_name / f'charts/sdvxInCharts/{musicid}/{difficulty}.png'):  # sdvx.in本地有缓存
            return data_path / server_name / f'charts/sdvxInCharts/{musicid}/{difficulty}.png'
        else:  # 无缓存，尝试下载
            if await downloadsdvxchart(musicid, difficulty, pjsk_type=pjsk_type):  # sdvx下载成功
                return data_path / server_name / f'charts/sdvxInCharts/{musicid}/{difficulty}.png'
            return None
    return None


# 根据get_type决定获取谱面预览的优先顺序
async def getchart(musicid, difficulty, get_type: int = 1, pjsk_type: int = 0) -> Path:
    # get_type: 1:本地moe->skv->sdvx
    # get_type: 2:skv->sdvx->本地moe
    # get_type: 3:sdvx->skv->本地moe
    rst = None
    if get_type == 1:
        try:
            rst = await getmoechart(musicid, difficulty, pjsk_type=pjsk_type)
        except:
            pass
        if not rst:
            try:
                rst = await getskvchart(musicid, difficulty, pjsk_type=pjsk_type)
            except:
                pass
    elif get_type == 2:
        try:
            rst = await getskvchart(musicid, difficulty, pjsk_type=pjsk_type)
        except:
            pass
        if not rst:
            try:
                rst = await getmoechart(musicid, difficulty, pjsk_type=pjsk_type)
            except:
                pass
    elif get_type == 3:
        try:
            rst = await getsdvxchart(musicid, difficulty, pjsk_type=pjsk_type)
        except:
            pass
        if not rst:
            try:
                rst = await getskvchart(musicid, difficulty, pjsk_type=pjsk_type)
            except:
                pass
        if not rst:
            try:
                rst = await getmoechart(musicid, difficulty, pjsk_type=pjsk_type)
            except:
                pass
    return rst


# 下载sdvx谱面预览
async def downloadsdvxchart(musicid, difficulty, pjsk_type: int = 0) -> bool:
    try:
        timeid = idtotime(musicid, pjsk_type=pjsk_type)
        # append 难度尝试使用 'apd' 作为后缀，如果不存在则回退到 'mst'
        if difficulty == 'append':
            maptype = 'apd'
        elif difficulty == 'master':
            maptype = 'mst'
        else:
            maptype = 'exp'
        try:
            data = await AsyncHttpx.get(f'https://sdvx.in/prsk/obj/data{str(timeid).zfill(3)}{maptype}.png')
        except:
            data = await AsyncHttpx.get(f'https://sdvx.in/prsk/obj/data{str(timeid).zfill(3)}{maptype}.png')
        if data.status_code == 200:  # 下载到了
            bg = await AsyncHttpx.get(f"https://sdvx.in/prsk/bg/{str(timeid).zfill(3)}bg.png")
            bar = await AsyncHttpx.get(f"https://sdvx.in/prsk/bg/{str(timeid).zfill(3)}bar.png")
            bgpic = Image.open(io.BytesIO(bg.content))
            datapic = Image.open(io.BytesIO(data.content))
            barpic = Image.open(io.BytesIO(bar.content))
            r, g, b, mask = datapic.split()
            bgpic.paste(datapic, (0, 0), mask)
            r, g, b, mask = barpic.split()
            bgpic.paste(barpic, (0, 0), mask)
            dirs = data_path / SERVER_MAP.get(pjsk_type, 'jp') / f'charts/sdvxInCharts/{musicid}'
            if not os.path.exists(dirs):
                os.makedirs(dirs)
            r, g, b, mask = bgpic.split()
            final = Image.new('RGB', bgpic.size, (0, 0, 0))
            final.paste(bgpic, (0, 0), mask)
            final.save(dirs / f'{difficulty}.png')
            return True
        else:
            return False
    except:
        return False


# 下载skviewer谱面预览
async def downloadviewerchart(musicid, difficulty, pjsk_type: int = 0) -> bool:
    if not CHART_PREVIEW_BASE_URL:
        return False
    try:
        server_name = SERVER_MAP.get(pjsk_type, 'jp')
        try:
            re = await AsyncHttpx.get(f'{CHART_PREVIEW_BASE_URL}/{str(musicid).zfill(4)}/{difficulty}.png')
        except:
            re = await AsyncHttpx.get(f'{CHART_PREVIEW_BASE_URL}/{str(musicid).zfill(4)}/{difficulty}.png')
        if re.status_code == 200:
            dirs = data_path / server_name / rf'charts/SekaiViewer/{musicid}'
            if not os.path.exists(dirs):
                os.makedirs(dirs)
            if difficulty in ['master', 'append']:
                svg = await AsyncHttpx.get(f'{CHART_PREVIEW_BASE_URL}/{str(musicid).zfill(4)}/{difficulty}.svg')
                i = 0
                while True:
                    i = i + 1
                    if svg.text.count(f'{str(i).zfill(3)}</text>') == 0:
                        break
                row = int((i - 2) / 4)
                pic = Image.open(io.BytesIO(re.content))
                r, g, b, mask = pic.split()
                final = Image.new('RGB', pic.size, (255, 255, 255))
                final.paste(pic, (0, 0), mask)
                final = final.resize((160 * row + 32, 1300))
                final.save(dirs / f'{difficulty}.png')
            else:
                pic = Image.open(io.BytesIO(re.content))
                r, g, b, mask = pic.split()
                final = Image.new('RGB', pic.size, (255, 255, 255))
                final.paste(pic, (0, 0), mask)
                final.save(dirs / f'{difficulty}.png')
            return True
        else:
            return False
    except:
        return False


# sdvx内使用：歌曲id->time
def idtotime(musicid, pjsk_type: int = 0):
    musics = sorted(
        load_master_data('musics.json', pjsk_type),
        key=lambda x: x["publishedAt"],
    )
    for i in range(0, len(musics)):
        if musics[i]['id'] == musicid:
            return i + 1
    return 0


def _generate_sus_sync(score_path: Path, output_path: Path):
    if output_path.exists():
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f'.{output_path.name}.{uuid.uuid4().hex}.tmp')
    try:
        music_info, music_score = read_file(score_path)
        score_image = create_image(music_info, music_score).convert('RGB')
        score_image = score_image.resize(
            (int(score_image.size[0] / 4), int(score_image.size[1] / 4))
        )
        score_image.save(temp_path, format='PNG')
        os.replace(temp_path, output_path)
    finally:
        temp_path.unlink(missing_ok=True)


async def _sus2img_impl(musicid, difficulty, pjsk_type: int):
    p, f = rf'startapp/music/music_score/{str(musicid).zfill(4)}_01', difficulty
    await pjsk_update_manager.get_asset(p, f, pjsk_type=pjsk_type)
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    score_path = data_path / server_name / p / f
    output_path = data_path / server_name / f'charts/sus/{musicid}/{difficulty}.png'
    await run_pjsk_thread(_generate_sus_sync, score_path, output_path)


# 本地谱面预览
async def sus2img(musicid, difficulty, pjsk_type: int = 0):
    key = ('sus', pjsk_type, musicid, difficulty)
    return await _run_generation_single_flight(
        key, lambda: _sus2img_impl(musicid, difficulty, pjsk_type)
    )


def _get_moe_metadata_sync(musicid, difficulty, pjsk_type: int):
    for item in load_master_data('musics.json', pjsk_type):
        if item['id'] == musicid:
            music = item.copy()
            break
    else:
        raise KeyError('没有找到对应歌曲信息')

    playlevel = '?'
    for item in load_master_data('musicDifficulties.json', pjsk_type):
        if item['musicId'] == musicid and item['musicDifficulty'] == difficulty:
            playlevel = item['playLevel']
            break
    return music, playlevel


def _generate_moe_sync(
    musicid,
    difficulty,
    with_skill,
    pjsk_type: int,
    score_path: Path,
    jacket_path: Path,
    music: dict,
    playlevel,
    output_path: Path,
):
    if output_path.exists():
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(score_path, 'r', encoding='utf-8') as score_file:
        lines = score_file.read().splitlines()

    if music['composer'] == music['arranger']:
        artist = music['composer']
    elif music['composer'] in music['arranger'] or music['composer'] == '-':
        artist = music['arranger']
    elif music['arranger'] in music['composer'] or music['arranger'] == '-':
        artist = music['composer']
    else:
        artist = f"{music['composer']} / {music['arranger']}"

    chart_args = {
        'title': music['title'],
        'artist': artist,
        'difficulty': difficulty,
        'playlevel': playlevel,
        'jacket': _local_file_href(jacket_path),
    }
    if not with_skill:
        sus = chart.SUS(
            lines,
            note_size=note_sizes[difficulty],
            note_host=str(data_path.absolute() / 'pics/notes'),
            **chart_args,
        )
    else:
        music_meta = None
        music_meta_path = data_path / 'realtime' / 'music_metas.json'
        if music_meta_path.exists():
            with open(music_meta_path, encoding='utf-8') as meta_file:
                for item in json.load(meta_file):
                    if item['music_id'] == musicid and item['difficulty'] == difficulty:
                        music_meta = item
                        break
        sus = chart.SUSwithskill(
            lines,
            note_size=note_sizes[difficulty],
            note_host=str(data_path.absolute() / 'pics/notes'),
            **chart_args,
            meta=music_meta,
        )

    css_dir = Path(__file__).parent / 'css'
    with open(css_dir / 'sus.css', encoding='utf-8') as css_file:
        style_sheet = css_file.read()
    with open(css_dir / 'master.css', encoding='utf-8') as css_file:
        style_sheet += '\n' + css_file.read()

    temp_id = uuid.uuid4().hex
    svg_path = output_path.with_name(f'.{output_path.stem}.{temp_id}.tmp.svg')
    png_path = output_path.with_name(f'.{output_path.stem}.{temp_id}.tmp.png')
    jpg_path = output_path.with_name(f'.{output_path.stem}.{temp_id}.tmp.jpg')
    try:
        sus.export(str(svg_path), style_sheet=style_sheet)
        cairosvg.svg2png(
            url=str(svg_path),
            write_to=str(png_path),
            scale=1.3,
            unsafe=True,
        )
        with Image.open(png_path) as image:
            image.save(jpg_path, quality=60)
        os.replace(jpg_path, output_path)
    finally:
        svg_path.unlink(missing_ok=True)
        png_path.unlink(missing_ok=True)
        jpg_path.unlink(missing_ok=True)


async def _moe2img_impl(musicid, difficulty, with_skill, pjsk_type: int):
    fix = '_skill' if with_skill else ''
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    output_path = data_path / server_name / f'charts/moe/{musicid}/{difficulty}{fix}.jpg'
    if output_path.exists():
        return

    p, f = rf'startapp/music/music_score/{str(musicid).zfill(4)}_01', difficulty
    await pjsk_update_manager.get_asset(p, f, pjsk_type=pjsk_type)
    music, playlevel = await run_pjsk_thread(
        _get_moe_metadata_sync, musicid, difficulty, pjsk_type
    )
    jacketdir = f'startapp/music/jacket/{music["assetbundleName"]}'
    jacketfile = f'{music["assetbundleName"]}.png'
    await pjsk_update_manager.get_asset(jacketdir, jacketfile, pjsk_type=pjsk_type)

    await run_pjsk_thread(
        _generate_moe_sync,
        musicid,
        difficulty,
        with_skill,
        pjsk_type,
        data_path / server_name / p / f,
        data_path / server_name / jacketdir / jacketfile,
        music,
        playlevel,
        output_path,
    )


# 本地谱面预览
async def moe2img(musicid, difficulty, withSkill=False, pjsk_type: int = 0):
    key = ('moe', pjsk_type, musicid, difficulty, bool(withSkill))
    return await _run_generation_single_flight(
        key, lambda: _moe2img_impl(musicid, difficulty, withSkill, pjsk_type)
    )



def read_file(file_name):
    file_object = open(file_name, 'r')

    music_info = {}
    music_score = []

    read_step = 0
    try:
        for line in file_object:
            if read_step == 0:
                # get music info
                for key_word in music_info_key_word:
                    info = re.match(music_info_key_word[key_word], line)
                    if info:
                        music_info[key_word] = info.group(1)

            # get music score
            if line == '#MEASUREHS 00\n':
                read_step += 1
                continue

            if read_step == 1:
                info = re.match(r'#(\d\d\d)(\d)([0-9a-f]):(\w*)', line)

                if info:
                    music_score.append({
                        'unitid': info.group(1),
                        'type': info.group(2),
                        'row': info.group(3),
                        'list': info.group(4),
                    })
                    continue

                info = re.match(r'#(\d\d\d)(\d)([0-9a-f])(\d):(\w*)', line)
                if info:
                    music_score.append({
                        'unitid': info.group(1),
                        'type': info.group(2),
                        'row': info.group(3),
                        'longid': info.group(4),
                        'list': info.group(5),
                    })
                    continue

    finally:
        for key_word in music_info_key_word:
            if key_word not in music_info:
                music_info[key_word] = 'No info'

        file_object.close()

    music_score.sort(key=lambda x: x['unitid'])

    return music_info, music_score


def create_image(music_info, music_score):
    # type 1
    note_info = {
        1: 'normal',
        2: 'crtcl',
        3: 'long_among',
        4: 'skill',
    }
    # type 5 1 flick up 2 long among unvisible 3 flick left 4 flick right 5,6 long start
    # type 3 1 long start 2 long end 3 long among 5 long among unvisible
    #####  read resource  #####
    note_normal = Image.open(data_path / "pics/notes/notes_normal.png")
    note_crtcl = Image.open(data_path / "pics/notes/notes_crtcl.png")
    note_long = Image.open(data_path / "pics/notes/notes_long.png")
    note_long_among = Image.open(data_path / "pics/notes/notes_long_among.png")
    note_long_among_crtcl = Image.open(data_path / "pics/notes/notes_long_among_crtcl.png")
    note_long_among_unvisible = Image.open(data_path / "pics/notes/note_long_among_unvisible.png")
    note_long_among_unvisible_crtcl = Image.open(data_path / "pics/notes/note_long_among_unvisible_crtcl.png")
    note_flick = Image.open(data_path / "pics/notes/notes_flick.png")
    note_flick_arrow = {
        1: [],
        3: [],
        4: []
    }
    note_flick_arrow_crtcl = {
        1: [],
        3: [],
        4: []
    }
    for i in range(1, 7):
        note_flick_arrow[1].append(Image.open(data_path / f"pics/notes/notes_flick_arrow_{i:02d}.png"))
        note_flick_arrow[3].append(Image.open(data_path / f"pics/notes/notes_flick_arrow_{i:02d}_diagonal.png"))
        note_flick_arrow[4].append(
            Image.open(data_path / f"pics/notes/notes_flick_arrow_{i:02d}_diagonal.png").transpose(Image.FLIP_LEFT_RIGHT))
        note_flick_arrow_crtcl[1].append(Image.open(data_path / f"pics/notes/notes_flick_arrow_crtcl_{i:02d}.png"))
        note_flick_arrow_crtcl[3].append(Image.open(data_path / f"pics/notes/notes_flick_arrow_crtcl_{i:02d}_diagonal.png"))
        note_flick_arrow_crtcl[4].append(
            Image.open(data_path / f"pics/notes/notes_flick_arrow_crtcl_{i:02d}_diagonal.png").transpose(Image.FLIP_LEFT_RIGHT))

    total_unit = int(music_score[-1]['unitid'])
    music_info['combo'] = 0

    # create image
    image_width = (int(total_unit / 4) + 1) * (unit_width + delta_unit_width)
    image_height = 4 * unit_height + image_top_bottom * 2
    music_score_image = Image.new('RGB', (image_width, image_height), (0, 0, 0, 100))

    # draw score lines
    draw = ImageDraw.Draw(music_score_image, 'RGBA')
    font = ImageFont.truetype(font=str(FONT_PATH / "SourceHanSansCN-Regular.otf"), size=50)
    big_line_list = []
    for i in range((int(total_unit / 4) + 1) * 2):
        x = image_left_right + i * (unit_width + delta_unit_width)
        # top left
        big_line_list.append((x, -10))
        # bottom left
        big_line_list.append((x, image_height + 10))
        # bottom right
        big_line_list.append((x + unit_width, image_height + 10))
        # top right
        big_line_list.append((x + unit_width, -10))

        for j in range(5):
            small_line_list = []
            # small line i top
            small_line_list.append((x + (j + 1) * note_width * 2, -10))
            # small line i bottom
            small_line_list.append((x + (j + 1) * note_width * 2, image_height + 10))
            draw.line(small_line_list, fill=(255, 255, 255), width=1)

    draw.line(big_line_list, fill=(137, 207, 240), width=5)

    # drow unit lines and text
    for i in range(total_unit + 2):
        x = image_left_right + int(i / 4) * (unit_width + delta_unit_width)
        y = image_height - (i % 4) * unit_height - image_top_bottom
        line_list = [(x, y), (x + unit_width, y)]
        draw.line(line_list, fill=(255, 255, 255), width=2)
        line_list = [(x, y - unit_height), (x + unit_width, y - unit_height)]
        draw.line(line_list, fill=(255, 255, 255), width=2)

        text_size = font.getsize(str(i))
        draw.text((x - text_size[0] - 5, y - 16), str(i), font=font, fill=(255, 255, 255))

        if i % 4 == 0:
            draw.text((x - text_size[0] - unit_width - delta_unit_width - 5, image_top_bottom - 16), str(i), font=font, fill=(255, 255, 255))

    polygon_list = {
        0: [],
        1: [],
        'result': [0, 0]
    }

    last_unitid = 0

    # draw notes
    notes_info_list = {
        'normal': {},
        'flick': {},
        'long': {},
        'long_among': {},
        'long_among_unvisible': {},
    }
    long_tmp_info = {
        0: {},
        1: {},
    }
    for u in range(len(music_score) + 1):
        if u != len(music_score):
            unitid = int(music_score[u]['unitid'])
            score_note_type = int(music_score[u]['type'])
            row = int(music_score[u]['row'], 16)
            list = re.findall('.{2}', music_score[u]['list'])

        if unitid == 0:
            last_unitid = unitid

        if last_unitid != unitid or u == len(music_score):
            # draw combo
            if u == len(music_score):
                tempunitid = unitid + 1
            else:
                tempunitid = unitid

            for long_id in [0, 1]:
                while polygon_list['result'][long_id] > 0:
                    # sort polygon list
                    polygon_list[long_id].sort(key=lambda x: x['unitid'] * 10000 + x['row_location'])

                    polygon_note_list = polygon_list[long_id]
                    if polygon_note_list[0]['image'] == note_crtcl:
                        color = (255, 241, 0, alpha)
                    else:
                        color = (60, 210, 160, alpha)

                    # draw polygon
                    while len(polygon_list[long_id]) > 1:
                        start_note = polygon_note_list[0]
                        end_note = polygon_note_list[1]

                        # check new row
                        if start_note['location'][1] > end_note['location'][1]:
                            # check new unit first
                            if start_note['row_location'] == 0 and start_note['unitid'] % 4 == 0:
                                slide_list = [
                                    (start_note['location'][0] - unit_width - delta_unit_width + fix_note_image_width,
                                     image_height - start_note['location'][1]),
                                    (end_note['location'][0] - unit_width - delta_unit_width + fix_note_image_width,
                                     end_note['location'][1] - image_height + image_top_bottom * 2),
                                    (end_note['location'][0] + end_note['size'][0] - unit_width - delta_unit_width - fix_note_image_width,
                                     end_note['location'][1] - image_height + image_top_bottom * 2),
                                    (start_note['location'][0] + start_note['size'][0] - unit_width - delta_unit_width - fix_note_image_width,
                                     image_height - start_note['location'][1])
                                ]
                                draw.polygon(slide_list, fill=color)

                                # polygon_paste_list.append([note_long, (start_note[0] - unit_width - delta_unit_width, image_height - start_note[1] - int(note_image_height / 2)), note_long])

                            # not new row
                            slide_list = [
                                (start_note['location'][0] + fix_note_image_width, start_note['location'][1] + int(start_note['size'][1] / 2)),
                                (end_note['location'][0] + fix_note_image_width, end_note['location'][1] + int(end_note['size'][1] / 2)),
                                (end_note['location'][0] + end_note['size'][0] - fix_note_image_width, end_note['location'][1] + int(end_note['size'][1] / 2)),
                                (start_note['location'][0] + start_note['size'][0] - fix_note_image_width, start_note['location'][1] + int(start_note['size'][1] / 2))
                            ]
                        else:
                            # draw bottom
                            slide_list = [
                                (start_note['location'][0] + unit_width + delta_unit_width + fix_note_image_width,
                                 image_height + start_note['location'][1] - image_top_bottom * 2 + int(start_note['size'][1] / 2)),
                                (end_note['location'][0] + fix_note_image_width,
                                 end_note['location'][1] + int(end_note['size'][1] / 2)),
                                (end_note['location'][0] + end_note['size'][0] - fix_note_image_width,
                                 end_note['location'][1] + int(end_note['size'][1] / 2)),
                                (start_note['location'][0] + start_note['size'][0] + unit_width + delta_unit_width - fix_note_image_width,
                                 image_height + start_note['location'][1] - image_top_bottom * 2 + int(start_note['size'][1] / 2))
                            ]
                            draw.polygon(slide_list, fill=color)

                            # polygon_paste_list.append([note_long, (end_note[0] - unit_width - delta_unit_width, image_top_bottom * 2 - image_height + end_note[1] - int(note_image_height / 2)), note_long])

                            # new row
                            slide_list = [
                                (start_note['location'][0] + fix_note_image_width,
                                 start_note['location'][1] + int(start_note['size'][1] / 2)),
                                (end_note['location'][0] - unit_width - delta_unit_width + fix_note_image_width,
                                 image_top_bottom * 2 - image_height + end_note['location'][1] + int(end_note['size'][1] / 2)),
                                (end_note['location'][0] + end_note['size'][0] - unit_width - delta_unit_width - fix_note_image_width,
                                 image_top_bottom * 2 - image_height + end_note['location'][1] + int(end_note['size'][1] / 2)),
                                (start_note['location'][0] + start_note['size'][0] - fix_note_image_width, start_note['location'][1] + int(start_note['size'][1] / 2))
                            ]

                        draw.polygon(slide_list, fill=color)

                        del polygon_list[long_id][0]

                        # check end
                        if 'end' in end_note:
                            break

                    # clear polygon list
                    polygon_list['result'][long_id] -= 1
                    del polygon_list[long_id][0]

        if u == len(music_score):
            break

        if score_note_type == 1:
            note_in_unit_location_index = -1

            for i in range(len(list)):
                note = list[i]
                note_type = int(note[0], 16)
                note_length = int(note[1], 16)

                note_in_unit_location_index += 1
                if note_type == 0 and note_length == 0:
                    continue
                elif note_type in note_info:
                    music_info['combo'] += 1

                    # get paste point
                    unit_location = (image_left_right + int(unitid / 4) * (unit_width + delta_unit_width), image_height - unitid % 4 * unit_height - image_top_bottom)

                    x, y, note_in_unit_location = get_location(note_length, unit_location, note_in_unit_location_index, row, len(list))

                    # get paste image and paste mask
                    if note_info[note_type] == 'normal':
                        paste_type = 'normal'
                        paste_image = note_normal
                    elif note_info[note_type] == 'crtcl':
                        paste_type = 'normal'
                        paste_image = note_crtcl
                    elif note_info[note_type] == 'long_among':
                        paste_type = 'long_among'
                        paste_image = note_long_among
                    elif note_info[note_type] == 'skill':
                        x = image_left_right + int(tempunitid / 4) * (unit_width + delta_unit_width)
                        y = image_height - (tempunitid % 4) * unit_height - image_top_bottom
                        draw.text((x - font.getsize('Skill')[0] - 5, y - 62), 'Skill', font=font, fill=(255, 255, 255))

                        if tempunitid % 4 == 0:
                            draw.text((x - font.getsize('Skill')[0] - unit_width - delta_unit_width - 5, image_top_bottom - 48), 'Skill', font=font, fill=(255, 255, 255))
                        continue
                    else:
                        continue

                    # paste note image
                    if paste_image:
                        note_image_localtion = (x, y)
                        # check new row
                        note_image_localtion2 = None
                        if unitid % 4 == 0 and note_in_unit_location_index == 0:
                            note_image_localtion2 = (x - unit_width - delta_unit_width, image_height - y - note_image_height)

                        notes_info_list[paste_type]['%d_%d_%d' % (unitid, row, note_in_unit_location)] = {
                            'image': paste_image,
                            'size': ((note_image_width + fix_note_image_width * 2) * note_length, note_image_height),
                            'length': note_length,
                            'location': note_image_localtion,
                            'location2': note_image_localtion2,
                            'unitid': unitid,
                            'row': row,
                            'row_location': note_in_unit_location,
                        }
        elif score_note_type == 5:
            # special note change
            # 1 flick
            # 5 long

            note_in_unit_location_index = -1

            for i in range(len(list)):
                special_note = list[i]
                special_note_type = int(special_note[0], 16)
                special_note_length = int(special_note[1], 16)

                note_in_unit_location_index += 1
                if special_note_type == 0 and special_note_length == 0:
                    continue

                # get paste point
                unit_location = (image_left_right + int(unitid / 4) * (unit_width + delta_unit_width), image_height - unitid % 4 * unit_height - image_top_bottom)
                x, y, note_in_unit_location = get_location(special_note_length, unit_location, note_in_unit_location_index, row, len(list))

                id = '%d_%d_%d' % (unitid, row, note_in_unit_location)
                if id not in notes_info_list['normal']:
                    note_image_localtion = (x, y)
                    # check new row
                    note_image_localtion2 = None
                    if unitid % 4 == 0 and note_in_unit_location_index == 0:
                        note_image_localtion2 = (x - unit_width - delta_unit_width, image_height - y - note_image_height)

                    notes_info_list['normal'][id] = {
                        'image': note_normal,
                        'size': ((note_image_width + fix_note_image_width * 2) * special_note_length, note_image_height),
                        'length': special_note_length,
                        'location': note_image_localtion,
                        'location2': note_image_localtion2,
                        'unitid': unitid,
                        'row': row,
                        'row_location': note_in_unit_location,
                    }

                note = notes_info_list['normal'][id]

                if special_note_type in [1, 3, 4]:
                    # flick 3 left 4 right
                    if note['image'] == note_crtcl:
                        paste_image = note['image']
                    else:
                        paste_image = note_flick

                    notes_info_list['flick'][id] = {
                        'image': paste_image,
                        'arrowtype': special_note_type,
                        'size': ((note_image_width + fix_note_image_width * 2) * special_note_length, note_image_height),
                        'length': special_note_length,
                        'location': note['location'],
                        'location2': note['location2'],
                        'unitid': unitid,
                        'row': row,
                        'row_location': note_in_unit_location,
                    }
                elif special_note_type == 2:
                    # unvisible long among
                    # unknow paste image type now defined in score_note_type 3 note_type 5
                    paste_image = note_long_among_unvisible

                    notes_info_list['long_among_unvisible'][id] = {
                        'image': paste_image,
                        'size': ((note_image_width + fix_note_image_width * 2) * special_note_length, note_image_height),
                        'length': special_note_length,
                        'location': note['location'],
                        'location2': note['location2'],
                        'unitid': unitid,
                        'row': row,
                        'row_location': note_in_unit_location,
                    }
                elif special_note_type == 5 or special_note_type == 6:
                    # long
                    if note['image'] == note_crtcl:
                        paste_image = note['image']
                    else:
                        paste_image = note_long

                    notes_info_list['long'][id] = {
                        'image': paste_image,
                        'size': ((note_image_width + fix_note_image_width * 2) * special_note_length, note_image_height),
                        'location': note['location'],
                        'location2': note['location2'],
                        'unitid': unitid,
                        'row': row,
                        'row_location': note_in_unit_location,
                    }
                elif special_note_type == 6:
                    # unkonw
                    pass
                else:
                    break
                del notes_info_list['normal'][id]
                continue
        elif score_note_type == 3:
            # long note info
            # 1 start 2 end 5 unvisible 3 among
            long_id = int(music_score[u]['longid'], 16)

            note_in_unit_location_index = -1

            for i in range(len(list)):
                long_note = list[i]
                long_note_type = int(long_note[0], 16)
                long_note_length = int(long_note[1], 16)

                note_in_unit_location_index += 1

                if long_note_type == 0 and long_note_length == 0:
                    continue

                # get paste point
                unit_location = (image_left_right + int(unitid / 4) * (unit_width + delta_unit_width), image_height - unitid % 4 * unit_height - image_top_bottom)
                x, y, note_in_unit_location = get_location(long_note_length, unit_location, note_in_unit_location_index, row, len(list))
                note_image_localtion = (x, y)

                # check new row
                note_image_localtion2 = None
                if unitid % 4 == 0 and note_in_unit_location_index == 0:
                    note_image_localtion2 = (x - unit_width - delta_unit_width, image_height - y - note_image_height)

                id = '%d_%d_%d' % (unitid, row, note_in_unit_location)
                if long_note_type == 1:
                    paste_image = note_long
                    if id in notes_info_list['long_among_unvisible']:
                        # paste_image = long_tmp_info[long_id]['image']
                        notes_info_list['long'][id] = {
                            'image': paste_image,
                            'size': ((note_image_width + fix_note_image_width * 2) * long_note_length, note_image_height),
                            'location': note_image_localtion,
                            'location2': note_image_localtion2,
                            'unitid': unitid,
                            'row': row,
                            'row_location': note_in_unit_location,
                        }
                        del notes_info_list['long_among_unvisible'][id]
                    elif id in notes_info_list['long']:
                        paste_image = notes_info_list['long'][id]['image']
                    elif id in notes_info_list['normal']:
                        paste_image = notes_info_list['normal'][id]['image']
                        notes_info_list['long'][id] = {
                            'image': paste_image,
                            'size': ((note_image_width + fix_note_image_width * 2) * long_note_length, note_image_height),
                            'location': note_image_localtion,
                            'location2': note_image_localtion2,
                            'unitid': unitid,
                            'row': row,
                            'row_location': note_in_unit_location,
                        }
                        del notes_info_list['normal'][id]
                    else:
                        notes_info_list['long'][id] = {
                            'image': paste_image,
                            'size': ((note_image_width + fix_note_image_width * 2) * long_note_length, note_image_height),
                            'location': note_image_localtion,
                            'location2': note_image_localtion2,
                            'unitid': unitid,
                            'row': row,
                            'row_location': note_in_unit_location,
                        }
                    # add temp info
                    long_tmp_info[long_id]['image'] = paste_image
                    # add polygon info
                    polygon_list[long_id].append(notes_info_list['long'][id])
                elif long_note_type == 2:
                    if id in notes_info_list['flick']:
                        if long_tmp_info[long_id]['image'] == note_crtcl:
                            paste_image = note_crtcl
                            notes_info_list['flick'][id]['image'] = paste_image

                        notes_info_list['flick'][id]['end'] = True

                        # add polygon info
                        polygon_list['result'][long_id] += 1
                        polygon_list[long_id].append(notes_info_list['flick'][id])
                        continue
                    elif id in notes_info_list['long']:
                        pass
                    else:
                        paste_image = long_tmp_info[long_id]['image']

                        notes_info_list['long'][id] = {
                            'image': paste_image,
                            'size': ((note_image_width + fix_note_image_width * 2) * long_note_length, note_image_height),
                            'location': note_image_localtion,
                            'location2': note_image_localtion2,
                            'unitid': unitid,
                            'row': row,
                            'row_location': note_in_unit_location,
                        }

                    notes_info_list['long'][id]['end'] = True

                    # add polygon info
                    polygon_list['result'][long_id] += 1
                    polygon_list[long_id].append(notes_info_list['long'][id])
                elif long_note_type == 3:
                    if long_tmp_info[long_id]['image'] == note_crtcl:
                        paste_image = note_long_among_crtcl
                    else:
                        paste_image = note_long_among

                    if id in notes_info_list['long']:
                        notes_info_list['long_among'][id] = {
                            'image': paste_image,
                            'size': ((note_image_width + fix_note_image_width * 2) * long_note_length, note_image_height),
                            'length': long_note_length,
                            'location': note_image_localtion,
                            'location2': note_image_localtion2,
                            'unitid': unitid,
                            'row': row,
                            'row_location': note_in_unit_location,
                        }
                        del notes_info_list['long'][id]
                    elif id in notes_info_list['long_among']:
                        if long_tmp_info[long_id]['image'] == note_crtcl:
                            notes_info_list['long_among'][id]['image'] = note_long_among_crtcl
                        else:
                            notes_info_list['long_among'][id]['image'] = note_long_among
                    elif id in notes_info_list['long_among_unvisible']:
                        notes_info_list['long_among'][id] = {
                            'image': paste_image,
                            'size': ((note_image_width + fix_note_image_width * 2) * long_note_length, note_image_height),
                            'length': long_note_length,
                            'location': note_image_localtion,
                            'location2': note_image_localtion2,
                            'unitid': unitid,
                            'row': row,
                            'row_location': note_in_unit_location,
                        }
                        del notes_info_list['long_among_unvisible'][id]
                    else:
                        notes_info_list['long_among'][id] = {
                            'image': paste_image,
                            'size': ((note_image_width + fix_note_image_width * 2) * long_note_length, note_image_height),
                            'length': long_note_length,
                            'location': note_image_localtion,
                            'location2': note_image_localtion2,
                            'unitid': unitid,
                            'row': row,
                            'row_location': note_in_unit_location,
                        }

                    # add polygon info
                    polygon_list[long_id].append(notes_info_list['long_among'][id])
                elif long_note_type == 5:
                    if long_tmp_info[long_id]['image'] == note_crtcl:
                        paste_image = note_long_among_unvisible_crtcl
                    else:
                        paste_image = note_long_among_unvisible

                    if id in notes_info_list['long_among_unvisible']:
                        if long_tmp_info[long_id]['image'] == note_crtcl:
                            notes_info_list['long_among_unvisible'][id]['image'] = note_long_among_unvisible_crtcl
                        else:
                            notes_info_list['long_among_unvisible'][id]['image'] = note_long_among_unvisible
                    elif id in notes_info_list['long']:
                        notes_info_list['long_among_unvisible'][id] = {
                            'image': paste_image,
                            'size': ((note_image_width + fix_note_image_width * 2) * long_note_length, note_image_height),
                            'length': long_note_length,
                            'location': note_image_localtion,
                            'location2': note_image_localtion2,
                            'unitid': unitid,
                            'row': row,
                            'row_location': note_in_unit_location,
                        }
                        del notes_info_list['long'][id]
                    else:
                        x, y, note_in_unit_location = get_location(long_note_length, unit_location, note_in_unit_location_index, row, len(list), type=35)
                        note_image_localtion = (x, y)

                        # check new row
                        note_image_localtion2 = None
                        if unitid % 4 == 0 and note_in_unit_location_index == 0:
                            note_image_localtion2 = (x - unit_width - delta_unit_width, image_height - y - note_image_height)

                        notes_info_list['long_among_unvisible'][id] = {
                            'image': paste_image,
                            'size': ((note_image_width + fix_note_image_width * 2) * long_note_length, note_image_height),
                            'length': long_note_length,
                            'location': note_image_localtion,
                            'location2': note_image_localtion2,
                            'unitid': unitid,
                            'row': row,
                            'row_location': note_in_unit_location,
                        }
                    # add polygon info
                    polygon_list[long_id].append(notes_info_list['long_among_unvisible'][id])
                else:
                    break

        last_unitid = unitid

    # paste note
    for id, note in notes_info_list['normal'].items():
        paste_image = note['image'].resize(note['size'], Image.Resampling.LANCZOS)
        music_score_image.paste(paste_image, note['location'], paste_image)

        if note['location2'] is not None:
            music_score_image.paste(paste_image, note['location2'], paste_image)

    # paste flick
    for id, flick in notes_info_list['flick'].items():
        if flick['image'] == note_crtcl:
            arrow_image = note_flick_arrow_crtcl[flick['arrowtype']][int(flick['length'] / 2) - 1]
        else:
            arrow_image = note_flick_arrow[flick['arrowtype']][int(flick['length'] / 2) - 1]

        paste_image = flick['image'].resize(flick['size'], Image.Resampling.LANCZOS)
        paste_arrow_image = arrow_image.resize((int(arrow_image.size[0] / 4), int(arrow_image.size[1] / 4)), Image.Resampling.LANCZOS)

        music_score_image.paste(paste_image, flick['location'], paste_image)
        music_score_image.paste(paste_arrow_image,
                                (int(flick['location'][0] + (paste_image.size[0] - paste_arrow_image.size[0]) / 2),
                                 int(flick['location'][1] - paste_arrow_image.size[1] * 0.8)),
                                paste_arrow_image)

        if flick['location2'] is not None:
            music_score_image.paste(paste_image, flick['location2'], paste_image)
            music_score_image.paste(paste_arrow_image,
                                    (int(flick['location2'][0] + (paste_image.size[0] - paste_arrow_image.size[0]) / 2),
                                     int(flick['location2'][1] - paste_arrow_image.size[1] * 0.8)),
                                    paste_arrow_image)

    # paste long
    for id, long in notes_info_list['long'].items():
        paste_image = long['image'].resize(long['size'], Image.Resampling.LANCZOS)
        music_score_image.paste(paste_image, long['location'], paste_image)

        if long['location2'] is not None:
            music_score_image.paste(paste_image, long['location2'], paste_image)

    # paste long among
    for id, long_among in notes_info_list['long_among'].items():
        base_image = note_long.resize(long_among['size'], Image.Resampling.LANCZOS)
        paste_image = long_among['image'].resize(
            (int(long_among['image'].size[0] / 10 * long_among['length']),
             int(long_among['image'].size[1] / 10 * long_among['length']))
            , Image.Resampling.LANCZOS)
        music_score_image.paste(paste_image,
                                (long_among['location'][0] + int((base_image.size[0] - paste_image.size[0]) / 2),
                                 long_among['location'][1] + int((base_image.size[1] - paste_image.size[1]) / 2)),
                                paste_image)

        if long_among['location2'] is not None:
            music_score_image.paste(paste_image,
                                    (long_among['location2'][0] + int((base_image.size[0] - paste_image.size[0]) / 2),
                                     long_among['location2'][1] + int((base_image.size[1] - paste_image.size[1]) / 2)),
                                    paste_image)

    # paste long among unvisible
    for id, long_among_unvisible in notes_info_list['long_among_unvisible'].items():
        paste_image = long_among_unvisible['image'].resize(long_among_unvisible['size'], Image.Resampling.LANCZOS)
        music_score_image.paste(paste_image, long_among_unvisible['location'], paste_image)

        if long_among_unvisible['location2'] is not None:
            music_score_image.paste(paste_image, long_among_unvisible['location2'], paste_image)

    return music_score_image


def get_location(note_length, unit_location, note_in_unit_location_index, row, length, type=None):
    note_in_unit_location = int(unit_height / length * note_in_unit_location_index)

    if type == 35:
        x = unit_location[0] + (row - 2) * (note_image_width + fix_note_image_width)
    else:
        if note_length >= 6:
            x = unit_location[0] + (row - 2) * (note_image_width + fix_note_image_width)
        elif note_length % 2 == 0:
            if note_length == 2:
                x = unit_location[0] + (row - note_length) * (note_image_width + fix_note_image_width)
            else:
                x = unit_location[0] + (row - int(note_length / 2)) * (note_image_width + fix_note_image_width) - fix_note_image_width
        else:
            x = unit_location[0] + (int(row - note_length / 2)) * (note_image_width + fix_note_image_width) - fix_note_image_width
    y = unit_location[1] - note_in_unit_location - int(note_image_height / 2)

    return x, y, note_in_unit_location
