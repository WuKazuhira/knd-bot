from datetime import timedelta

from loguru import logger as logger_
from nonebot.log import default_filter, default_format

from config.path_config import LOG_PATH

logger = logger_

logger.add(
    LOG_PATH / '{time:YYYY-MM-DD}.log',
    level='INFO',
    rotation='00:00',
    format=default_format,
    filter=default_filter,
    retention=timedelta(days=30))

logger.add(
    LOG_PATH / 'error_{time:YYYY-MM-DD}.log',
    level='ERROR',
    rotation='00:00',
    format=default_format,
    filter=default_filter,
    retention=timedelta(days=30))
