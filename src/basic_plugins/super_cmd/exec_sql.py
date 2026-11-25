import os

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from services.db_context import db
from services.log import logger

__plugin_name__ = "执行sql [Superuser]"
__plugin_type__ = "数据管理"
__plugin_version__ = 0.1
__plugin_usage__ = """
usage：
    执行一段sql语句
    指令：
        exec [sql语句]
""".strip()
__plugin_settings__ = {
    "cmd": ["执行sql"]
}


exec_ = on_command("exec", permission=SUPERUSER, priority=1, block=True)


@exec_.handle()
async def _(arg: Message = CommandArg()):
    if os.getenv("KNDBOT_ENABLE_EXEC_SQL") != "1":
        await exec_.finish("exec SQL 功能默认已禁用，如需启用请设置环境变量 KNDBOT_ENABLE_EXEC_SQL=1")
    sql = arg.extract_plain_text().strip()
    try:
        async with db.transaction():
            await db.status(db.text(sql))
    except Exception as e:
        logger.error(f"执行 sql 语句失败 {type(e)}：{e}")
        await exec_.finish(f"执行 sql 语句失败 {type(e)}：{e}")
    await exec_.finish("执行 sql 语句成功.")
