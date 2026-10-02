from nonebot import on_command
from nonebot.adapters.onebot.v11 import GROUP

from utils.message_builder import image

from .data_source import admin_help_image, create_help_image

__plugin_name__ = '管理帮助 [Admin]'
__plugin_version__ = 0.1
__plugin_type__ = "管理功能"
__plugin_usage__ = """
管理员帮助：管理员帮助 / 群管帮助 / 管理帮助
    群内查看管理功能列表；具体说明可发送 knd <管理指令> help。
    具体命令仍受原管理员权限等级与群设置限制，查看帮助不会授予权限。
""".strip()
__plugin_settings__ = {
    "admin_level": 1,
}

admin_help = on_command(
    "管理员帮助",
    aliases={"群管帮助", "管理帮助"},
    permission=GROUP,
    priority=1,
    block=True
)

if admin_help_image.exists():
    admin_help_image.unlink()


@admin_help.handle()
async def _():
    if not admin_help_image.exists():
        await create_help_image()
    await admin_help.send(image('admin_help_img.png'))
