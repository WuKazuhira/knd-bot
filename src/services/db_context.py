from gino import Gino

from config.config import address, bind, database, password, port, sql_name, user

from .log import logger

# 全局数据库连接对象
db = Gino()


async def init():
    if not bind and (not user and not password and not address and not port and not database):
        raise ValueError("\n" + "数据库配置未填写")
    i_bind = bind
    if not i_bind:
        i_bind = f"{sql_name}://{user}:{password}@{address}:{port}/{database}"
    try:
        await db.set_bind(i_bind)
        await db.gino.create_all()
        # 尝试自动修复旧表缺失的 pjsk_type 字段
        try:
            await db.status('ALTER TABLE pjsk_guess_rank ADD COLUMN pjsk_type INTEGER DEFAULT 0 NOT NULL;')
            logger.info("Successfully added missing pjsk_type to pjsk_guess_rank")
        except Exception:
            pass
        logger.info(f'Database loaded successfully!')
    except Exception as e:
        raise Exception(f'数据库连接错误.... {type(e)}: {e}')


async def disconnect():
    await db.pop_bind().close()

