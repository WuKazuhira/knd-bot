"""AI 海龟汤游戏插件。

业务代码归档自 xxtg666/nonebot-plugin-ai-turtle-soup 1.0.7（MIT）。
"""
from nonebot import get_driver, on_message
from nonebot.adapters import Event
from nonebot.exception import FinishedException
from nonebot.log import logger
from nonebot.plugin import PluginMetadata, inherit_supported_adapters, require
from nonebot.rule import to_me

from .config import Config
from .game_manager import GameManager

require("nonebot_plugin_alconna")
require("nonebot_plugin_uninfo")

__plugin_name__ = "海龟汤"
__plugin_type__ = "娱乐功能"
__plugin_version__ = "1.0.7"
__plugin_usage__ = """
    帮助：knd 开始海龟汤 help；原有命令入口保留兼容。

用法：
  /开始海龟汤 [主题] - 开始新游戏
  @机器人 <问题> - 提出只能用是、否或不重要回答的问题
  @机器人 查看进度/提示/重新计算进度/放弃 - 操作当前游戏
  /海龟汤帮助 - 查看完整说明
""".strip()
__plugin_settings__ = {
    "cmd": ["开始海龟汤", "海龟汤帮助"],
    "default_status": True,
}

__plugin_meta__ = PluginMetadata(
    name="海龟汤游戏",
    description="AI驱动的海龟汤(情境猜谜)游戏",
    usage="""
    /开始海龟汤 [主题] - 开始一局新游戏
    @bot <问题> - 向AI提问(只能用是/否回答的问题)
    @bot 查看进度 - 查看当前游戏进度
    @bot 提示 - 获取游戏提示
    @bot 重新计算进度 - 重新分析并计算游戏进度
    @bot 放弃 - 放弃当前游戏并查看答案
    /海龟汤帮助 - 查看帮助信息
    """,
    type="application",
    homepage="https://github.com/xxtg666/nonebot-plugin-ai-turtle-soup",
    config=Config,
    supported_adapters=inherit_supported_adapters("nonebot_plugin_alconna", "nonebot_plugin_uninfo"),
)

from nonebot_plugin_alconna import Alconna, Args, Arparma, UniMessage, on_alconna
from nonebot_plugin_uninfo import Session, UniSession

game_manager = GameManager()
get_driver().on_shutdown(game_manager.close)

start_game = on_alconna(
    Alconna(
        "开始海龟汤",
        Args["theme", str, ""],
    ),
    priority=5,
    block=True,
    use_cmd_start=True
)

help_cmd = on_alconna(
    Alconna("海龟汤帮助"),
    priority=5,
    block=True,
    use_cmd_start=True
)

at_bot_handler = on_message(rule=to_me(), priority=6, block=True)


@start_game.assign("$main")
async def handle_start_game(result: Arparma, event: Event, session: Session = UniSession()):
    session_id = session.scene.id

    async with game_manager.session_lock(session_id):
        await _start_session_game(session_id, result.query("theme"))


async def _start_session_game(session_id: str, theme: str):
    if game_manager.has_active_game(session_id):
        await UniMessage("当前已有进行中的游戏!请先完成或放弃当前游戏。").finish()

    try:
        if game_manager.config.ats_puzzle_source == "local":
            if not game_manager.local_puzzles:
                await UniMessage(
                    "本地题库为空!\n"
                    "请检查 ATS_LOCAL_PUZZLES_PATH 配置是否正确。"
                ).finish()

            puzzle = game_manager.create_local_game(session_id, keyword=theme)

            if puzzle is None:
                await UniMessage(
                    f"未在本地题库中找到与\"{theme}\"匹配的题目!\n"
                    f"可以尝试其他关键词，或使用 /开始海龟汤 随机选题。"
                ).finish()
        else:
            await UniMessage("正在生成新的海龟汤谜题,请稍候...").send()
            puzzle = await game_manager.create_game(session_id, theme=theme)

        logger.info(f"{session_id} 创建了新的海龟汤游戏: \n{puzzle}")

        message_1 = (
            f"🎮 海龟汤游戏开始!\n\n"
            f"📖 题目: {puzzle['title']}\n\n"
            f"🤔 汤面:\n{puzzle['puzzle_setting']}\n\n"
            f"💡 直接 @我 提问即可,例如: @bot 这个人是故意的吗?\n"
            f"问题应该是可以用\"是\"、\"否\"或\"不重要\"回答的\n"
            f"想要放弃请 @我 说\"放弃\"\n"
            f"需要提示请 @我 说\"提示\"\n\n"
            f"📊 当前进度: 0%"
        )

        await UniMessage(message_1).finish()

        # 生成谜题评分
        # await UniMessage("正在评估谜题质量...").send()
        # rating = await game_manager.rate_puzzle(session_id)

        # message_2 = (
        #     f"⭐ 谜题评分:\n"
        #     f"  综合评分: {rating['scores']['overall']}/10\n"
        #     f"  悬念度: {rating['scores']['suspense']}/10\n"
        #     f"  逻辑性: {rating['scores']['logic']}/10\n"
        #     f"  创意性: {rating['scores']['creativity']}/10\n"
        #     f"  可玩性: {rating['scores']['playability']}/10"
        # )

        # await UniMessage(message_2).finish()
    except FinishedException:
        return

    except Exception as e:
        logger.opt(exception=True).error(f"生成游戏失败: {e}")
        await UniMessage("生成游戏失败，请稍后重试。").finish()


@at_bot_handler.handle()
async def handle_at_bot(event: Event, session: Session = UniSession()):
    session_id = session.scene.id

    msg = await UniMessage.generate(event=event)
    text = msg.extract_plain_text().strip()

    # 命令开头交给命令处理器
    if text.startswith('/') or text.startswith('开始海龟汤') or text.startswith('海龟汤帮助'):
        await at_bot_handler.skip()

    async with game_manager.session_lock(session_id):
        await _handle_session_message(session_id, text)


async def _handle_session_message(session_id: str, text: str):
    if not game_manager.has_active_game(session_id):
        await at_bot_handler.skip()

    game = game_manager.get_game(session_id)
    if not text:
        await UniMessage("请输入你的问题、\"查看进度\"、\"提示\"、\"重新计算进度\"或\"放弃\"").finish()

    if text in ["查看进度", "进度", "当前进度", "看进度", "查询进度"]:
        puzzle = game['puzzle']

        message = (
            f"📈 当前游戏进度\n\n"
            f"📖 题目: {puzzle['title']}\n\n"
            f"🤔 汤面:\n{puzzle['puzzle_setting']}\n\n"
            f"📊 进度: {game['percent']}%\n"
            f"🔢 已提问: {len(game['history'])}/{game_manager.config.ats_max_questions}次\n\n"
        )

        if game['history']:
            message += "❓ 最近的问答:\n"
            recent_history = game['history'][-3:]
            for i, qa in enumerate(recent_history, 1):
                message += f"{i}. Q: {qa['question']}\n   A: {qa['answer']}\n"

        await UniMessage(message).finish()

    if text in ["提示", "给个提示", "来个提示", "要提示"]:
        hint_result = game_manager.get_next_hint(session_id)

        if hint_result is None:
            await UniMessage("暂无可用的提示信息。").finish()
        elif hint_result['finished']:
            await UniMessage(f"所有提示已展示完毕!\n共 {hint_result['total']} 条提示。").finish()
        else:
            message = (
                f"💡 提示 [{hint_result['current']}/{hint_result['total']}]:\n"
                f"{hint_result['hint']}"
            )
            await UniMessage(message).finish()

    if text in ["重新计算进度", "重算进度", "重新计算", "重新评估", "更新进度"]:
        await UniMessage("正在重新计算进度...").send()

        try:
            new_percent = await game_manager.recalculate_progress(session_id)

            message = (
                f"🔄 进度已重新计算\n\n"
                f"📊 新的进度: {new_percent}%\n"
                f"🔢 已提问: {len(game['history'])}/{game_manager.config.ats_max_questions}次"
            )

            await UniMessage(message).finish()
        except FinishedException:
            return
        except Exception as e:
            logger.opt(exception=True).error(f"重新计算进度失败: {e}")
            await UniMessage("重新计算进度失败，请稍后重试。").finish()

    if text in ["放弃", "我放弃", "不玩了", "公布答案", "看答案", "放弃游戏"]:
        game_manager.end_game(session_id)
        supplementary_info = "\n".join(f"• {info}" for info in game['puzzle']['supplementary_info'])
        message = (
            f"😔 游戏结束\n\n"
            f"📖 题目: {game['puzzle']['title']}\n\n"
            f"🔢 总提问数: {len(game['history'])}\n\n"
            f"📖 正确答案:\n{game['puzzle']['solution']}\n\n"
            f"💡 全部附加信息:\n{supplementary_info}\n\n"
            f"💡 再接再厉!使用 /开始海龟汤 开始新游戏"
        )
        await UniMessage(message).finish()

    question = text

    if len(game['history']) >= game_manager.config.ats_max_questions:
        await UniMessage(
            f"已达到最大提问次数({game_manager.config.ats_max_questions}次)!\n"
            f"@我 说\"放弃\"可以查看答案。"
        ).finish()

    try:
        result = await game_manager.process_question(session_id, question)

        message = f"❓ 你的问题: {question}\n"
        message += f"💬 回答: {result['reply']}\n"
        message += f"📊 进度: {result['percent']}%\n"
        message += f"🔢 已提问: {len(game['history'])}/{game_manager.config.ats_max_questions}次"

        if result['percent'] >= 100:
            supplementary_info = "\n".join([f"• {info}" for info in game['puzzle']['supplementary_info']])

            game_manager.end_game(session_id)

            message += f"\n\n📖 完整答案:\n{game['puzzle']['solution']}"
            message += f"\n\n💡 全部附加信息:\n{supplementary_info}"
            message += "\n\n🎉 使用 /开始海龟汤 开始新游戏"

        await UniMessage(message).finish()
    except FinishedException:
        return

    except Exception as e:
        logger.opt(exception=True).error(f"处理海龟汤提问失败: {e}")
        await UniMessage("处理问题失败，请稍后重试。").finish()


@help_cmd.assign("$main")
async def handle_help():
    help_text = """
🐢 海龟汤游戏说明

海龟汤(Situation Puzzle)是一种情境猜谜游戏。
系统会给出一个离奇的情境描述(汤面),
你需要通过提问来推理出完整的故事(汤底)。

📜 游戏规则:
1. 只能提出可以用"是"、"否"或"不重要"回答的问题
2. 根据AI的回答逐步推理出真相
3. 进度达到100%表示猜对或放弃

🎮 使用方式:
/开始海龟汤 [主题] - 开始新游戏,可选指定主题
  (AI出题时主题用于生成谜题;
   本地题库时主题作为关键词筛选题目)
@bot <问题> - 提出问题(例如: @bot 这是故意的吗?)
@bot 查看进度 - 查看当前游戏状态
@bot 提示 - 获取一条提示
@bot 重新计算进度 - 重新分析并计算游戏进度
@bot 放弃 - 放弃游戏并查看答案
/海龟汤帮助 - 显示此帮助

💡 提问技巧:
• 先确认基本事实(人物、地点、时间等)
• 排除法缩小可能性
• 关注异常和矛盾之处
• 注意细节和隐含信息

📝 示例对话:
玩家: /开始海龟汤
机器人: [显示题目和汤面]
玩家: @bot 这个人是故意的吗?
机器人: 不是
玩家: @bot 他的身体有什么特殊情况吗?
机器人: 是
玩家: @bot 查看进度
机器人: [显示当前进度]
...

祝你玩得开心! 🎉
"""
    await UniMessage(help_text.strip()).finish()
