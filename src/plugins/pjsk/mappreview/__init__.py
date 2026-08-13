import traceback
from nonebot import on_command, on_keyword
from typing import Tuple
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.matcher import Matcher
from nonebot.params import Command

from utils.http_utils import AsyncHttpx
from utils.message_builder import image
from .._config import BUG_ERROR
from .._song_utils import get_songs_data, save_songs_data, parse_bpm
from .._utils import get_pjsk_type
from ._data_source import getchart, getmoechart
import json

__plugin_name__ = "谱面预览/技能预览"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.1
__plugin_usage__ = f"""
usage：
    查询烧烤谱面信息
    若群内已有unibot请勿开启此bot该功能
    私聊可用，限制每人1分钟只能查询4次
    指令：
        谱面预览 [难度] [曲目]           : 优先获取自动生成的谱面预览
        技能预览 [难度] [曲目]           : 生成带技能生效范围的谱面预览
        谱面预览1 [难度] [曲目]          : 优先获取自动生成的谱面预览
        谱面预览2 [难度] [曲目]          : 优先获取SekaiViewer生成的谱面预览        
        谱面预览3 [难度] [曲目]          : 优先获取SDVX生成的谱面预览 
    注意：
        注意SDVX获取途径只有master、expert难度的谱面，
        而且可能由于保管所谱面id顺序的问题导致早期的谱面预览获取错误（意思是让你少用）
        
        为了节省指令记忆成本，指令中[曲目]、[难度]的出现位置可以在文本'谱面预览'的前后，并且两者的顺序也可以调换
        但如果[曲目]自身含有[难度]的文本导致得不到谱面预览信息时可以反馈
        
        [曲目]可以为歌曲的别称，[难度]可以省略，默认为master难度
        [难度]支持输入的格式：master或ma、expert或ex、hard或hd、normal或nm、easy或ez、append或ap或app或apd
        所有谱面文件首次生成时可能返回很慢，请保持耐心(・_・。)
    数据来源：
        unipjsk.com
        pjsek.ai
        sekai.best
        sdvx.in
""".strip()
__plugin_settings__ = {
    "default_status": False,
    "cmd": ["谱面预览", "技能预览", "烧烤相关", "uni移植"],
}
__plugin_cd_limit__ = {"cd": 60, "count_limit": 4, "rst": "别急，等[cd]秒后再用！", "limit_type": "user"}
__plugin_block_limit__ = {"rst": "别急，还在查！"}

# preview
map_preview = on_command('谱面预览', priority=5, block=True)
cn_map_preview = on_command('cn谱面预览', priority=5, block=True)
tw_map_preview = on_command('tw谱面预览', priority=5, block=True)

skill_preview = on_command('技能预览', priority=5, block=True)
cn_skill_preview = on_command('cn技能预览', priority=5, block=True)
tw_skill_preview = on_command('tw技能预览', priority=5, block=True)


async def preprocess(arg: str, pjsk_type: int = 0):
    diff_dict = {
        'master': 'master', 'ma': 'master',
        'expert': 'expert', 'ex': 'expert',
        'hard': 'hard', 'hd': 'hard',
        'normal': 'normal', 'nm': 'normal',
        'easy': 'easy', 'ez': 'easy',
        'append': 'append', 'ap': 'append', 'app': 'append', 'apd': 'append'
    }
    for each in diff_dict:
        if arg.endswith(each):
            diff = diff_dict[each]
            alias = arg[:-len(each)]
            break
        elif arg.startswith(each):
            diff = diff_dict[each]
            alias = arg[len(each):]
            break
    else:
        diff = 'master'
        alias = arg
    # 首先查询本地数据库有无对应别称id
    data = await get_songs_data(alias, isfuzzy=False, pjsk_type=pjsk_type)
    # 若无结果则访问uniapi
    if data['status'] != 'success':
        # 在本地模糊搜索得到结果
        data = await get_songs_data(alias, isfuzzy=True, pjsk_type=pjsk_type)
        if data['status'] != 'success':
            await map_preview.finish('没有找到你要的歌曲哦')
    return diff, data


@map_preview.handle()
@cn_map_preview.handle()
@tw_map_preview.handle()
async def _(matcher: Matcher, event: MessageEvent, cmd: Tuple[str, ...] = Command()):
    msg = event.get_plaintext().strip()
    pjsk_type = get_pjsk_type(cmd[0])

    try:
        idx = msg.find('谱面预览')
        _type = {'1': 1, '2': 2, '3': 3}.get(msg[idx + 4], 0) if idx != -1 and idx + 4 < len(msg) else 0
    except IndexError:
        _type = 0
    
    # 提取参数
    if _type == 0:
        arg = msg.split('谱面预览')[-1].strip()
    else:
        arg = msg.split(f'谱面预览{_type}')[-1].strip()

    if not arg:
        await matcher.finish("使用方法：谱面预览 [难度] [曲名]")
    
    diff, data = await preprocess(arg, pjsk_type=pjsk_type)
    if data['match'] < 0.6 or data['musicId'] == 0:
        await matcher.finish("没有找到你说的歌曲哦，可能是没有此称呼或者匹配度过低")
    else:
        text = data['title'] + ' ' + diff.upper() + '\n' + '匹配度: ' + str(round(data['match'], 4))
        try:
            dir = await getchart(data['musicId'], diff, get_type=_type or 1, pjsk_type=pjsk_type)
        except:
            await matcher.finish(BUG_ERROR)
            return
        else:
            if dir:
                bpm = await parse_bpm(data['musicId'], pjsk_type=pjsk_type)
                bpmtext = ''
                for bpms in bpm[1]:
                    bpmtext += ' - ' + str(bpms['bpm']).replace('.0', '')
                if 'SekaiViewer' in str(dir):
                    text += '\nBPM: ' + bpmtext[3:] + '\n谱面图片来自Sekai Viewer'
                elif 'sdvxInCharts' in str(dir):
                    text += '\nBPM: ' + bpmtext[3:] + '\n谱面图片来自プロセカ谱面保管所，若谱面显示错误请尝试使用其它预览源'
                else:
                    text += '\nBPM: ' + bpmtext[3:] + '\n谱面图片来自ぷろせかもえ！'
                await matcher.finish(text + image(dir))
            else:
                await matcher.finish(text + "\n暂无谱面图片 请等待更新")


@skill_preview.handle()
@cn_skill_preview.handle()
@tw_skill_preview.handle()
async def _(matcher: Matcher, event: MessageEvent, cmd: Tuple[str, ...] = Command()):
    msg = event.get_plaintext().strip()
    pjsk_type = get_pjsk_type(cmd[0])

    arg = msg.split('技能预览')[-1].strip()
    if not arg:
        await matcher.finish("使用方法：技能预览 [难度] [曲名]")
    diff, data = await preprocess(arg, pjsk_type=pjsk_type)
    if data['match'] < 0.6 or data['musicId'] == 0:
        await matcher.finish("没有找到你说的歌曲哦，可能是没有此称呼或者匹配度过低")
    else:
        text = data['title'] + ' ' + diff.upper() + '\n' + '匹配度: ' + str(round(data['match'], 4))
        try:
            dir = await getmoechart(data['musicId'], diff, True, pjsk_type=pjsk_type)
        except:
            traceback.print_exc()
            await matcher.finish(BUG_ERROR)
            return
        else:
            if dir:
                bpm = await parse_bpm(data['musicId'], pjsk_type=pjsk_type)
                bpmtext = ''
                for bpms in bpm[1]:
                    bpmtext += ' - ' + str(bpms['bpm']).replace('.0', '')
                text += '\nBPM: ' + bpmtext[3:] + '\n谱面图片来自ぷろせかもえ！'
                await matcher.finish(text + image(dir))
            else:
                await matcher.finish(text + "\n暂无谱面图片 请等待更新")
