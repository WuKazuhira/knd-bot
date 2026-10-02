from io import BytesIO
from typing import Union

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import GROUP, MessageEvent, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import Depends
from nonebot.typing import T_Handler

from services.log import logger
from utils.limit_utils import access_cd, access_count
from utils.meme_catalog import render_catalog

from .data_source import commands
from ._bonds import USAGE as BONDS_USAGE
from .depends import regex, split_msg
from .utils import Command, help_image, help_pic_image

__plugin_name__ = "头像表情包/图片操作"
__plugin_type__ = "图片类"
__plugin_version__ = 0.1
__plugin_usage__ = """
    帮助：knd 头像表情包 help；原有命令入口保留兼容。

usage：
    触发方式：指令 + @任何人/qq号/自己/图片
    使用 "@任何人/qq号/自己" 将以目标qq的头像作为图片，也可以手动自带图片
    请发送 "头像表情包" "改图指令" 查看所有支持的指令

    大部分指令可以不需要文字，但必须有图片，其中一些可以携带参数的指令如下：
        摸 ?圆 [图片]                             : 不带参数"圆"时，默认图片为正方形
        对称 ?[上/下/左/右] [图片]          : 不带位置参数时，默认以"左边"对称
        典中典 [文字] [图片]                   : 必须自带文案
        我朋友?[昵称]说 *[文字] [图片]   : 文字可有多段，使用"空格"分开
        羁绊牌 @A ?[颜色] @B ?[颜色] ?[花牌/羽牌/普牌] ?[文字]
                                                    : 用两人头像做 PJSK 羁绊牌；颜色省略则随机，牌型省略为花牌
        羁绊牌 [角色缩写] [角色缩写] ?[花牌/羽牌/普牌] ?[文字]
                                                    : 用两个游戏角色的小人做羁绊牌，背景颜色跟随角色固有色
                                                    : 可发送“knd 羁绊牌 help”查看用法（旧“羁绊牌 help”兼容），“羁绊牌 颜色”查看全部可用颜色编号
        
        可以携带多张图片的指令:
            我永远喜欢、远离
            
        带文字时会使用指定文案的指令:
            万能表情、玩游戏、采访、阿尼亚喜欢、安全感、看图标、看扁、不文明、一起
            
        带文字时会使用指定文字作为昵称的指令:
            小天使、兑换券、问问、交个朋友、关注
    示例：
        结婚申请 @任何人
        膜 qq号
        垃圾桶 自己
        出警 [图片] ?[图片]
        羁绊牌 @小明 5 @小红 12 羽牌
        羁绊牌 mnr hrk 大海般的颜色
        改图水平翻转 [图片]
        图片旋转 90 [图片]    :图片逆时针旋转90°
""".strip()
__plugin_command_usage__ = {"羁绊牌": BONDS_USAGE}
__plugin_settings__ = {
    'cmd': ['头像表情包', '图片操作', '改图指令', '改图', '羁绊牌', '图片操作指令', '图片', '万能表情', '空白表情', '摸', '摸摸', '摸头', 'rua', '亲', '亲亲', '贴', '贴贴', '蹭', '蹭蹭', '咖波蹭', '顶', '玩', '拍', '撕', '怒撕', '丢', '扔', '抛', '掷', '爬', '精神支柱', '一直', '加载中', '转', '小天使', '不要靠近', '一样', '滚', '玩游戏', '膜', '膜拜', '吃', '可莉吃', '啃', '胡桃啃', '警察', '出警', '出示证件', '证件', '问问', '搓', '墙纸', '国旗', '交个朋友', '继续干活', '打工人', '完美', '关注', '我朋友说', '我朋友', '这像画吗', '震惊', '兑换券', '听音乐', '典中典', '黑白草图', '哈哈镜', '永远爱你', '对称', '安全感', '我永远喜欢', '采访', '打拳', '群青', '捣', '捶', '需要', '你可能需要', '捂脸', '敲', '垃圾', '垃圾桶', '为什么@我', '像样的亲亲', '啾啾', '吸', '嗦', '锤', '紧贴', '紧紧贴着', '注意力涣散', '阿尼亚喜欢', '想什么', '远离', '结婚申请', '结婚登记', '小画家', '复读', '防诱拐', '字符画', '这是我的老婆', '胡桃平板', '胡桃放大', '讲课', '敲黑板', '上瘾', '毒瘾发作', '手枪', '高血压', '看书', '遇到困难请拨打', '迷惑', '打穿', '打穿屏幕', '击剑', '抱大腿', '唐可可举牌', '无响应', '抱紧', '看扁', '看图标', '舰长', '急急国王', '不文明', '一起', '波纹', '诈尸', '秽土转生', '卡比锤', '卡比重锤', '木鱼', '凯露指', '踢球', '砸', '波奇手稿', '坐得住', '坐的住', '偷学', '恍惚', '恐龙', '小恐龙', '挠头', '鼓掌', '追列车', '追火车', '万花筒', '万花镜', '加班', '头像公式', '等价无穷小', '土豆', '打印', '亚文化取名机', '亚名', '拍头', '怎么说话的', '奶茶']
}
__plugin_cd_limit__ = {"cd": 10, "rst": "别急，[cd]s后再用！",}
__plugin_count_limit__ = {
    "max_count": 20,
    "limit_type": "user",
    "rst": "今天已经玩够了吧，还请明天再继续呢[at]",
}

help_cmd = on_command("头像表情包", aliases={"图片操作指令", "改图指令"}, permission=GROUP, block=True, priority=4)


@help_cmd.handle()
async def _(event: MessageEvent):
    if event.get_plaintext().strip() in ["图片操作指令", "改图指令"]:
        img = await help_pic_image()
    else:
        # 表情分散在 petpet / memes / meme_extra 三个插件里，统一出一张总表，
        # 免得群友要记三条帮助指令。总表拿不到时退回本插件自己的列表。
        try:
            img = await render_catalog()
        except Exception as e:
            logger.warning(f"生成表情包总目录失败，回退到本插件列表: {e}")
            img = await help_image(commands[1:])
    if img:
        await help_cmd.finish(MessageSegment.image(img))


def create_matchers():
    def handler(command: Command) -> T_Handler:
        async def handle(
            matcher: Matcher, event: MessageEvent, res: Union[None, str, BytesIO] = Depends(command.func)
        ):
            if res is None:
                await matcher.finish()
            if isinstance(res, str):
                await matcher.finish(res)
            else:
                access_count(matcher.plugin_name, event)
                access_cd(matcher.plugin_name, event)
                await matcher.finish(MessageSegment.image(res))
        return handle

    for command in commands:
        on_message(
            regex(command.pattern, command.arg_type),
            block=True,
            priority=5,
        ).append_handler(handler(command), parameterless=[split_msg()])


create_matchers()
