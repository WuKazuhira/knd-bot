import asyncio
import json
import os
import stat
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from nonebot import get_driver
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.matcher import Matcher
from nonebot.message import run_postprocessor
from nonebot.typing import T_State

from config.path_config import DATA_PATH
from manager import plugins2settings_manager
from services import logger
from utils.utils import scheduler

__plugin_name__ = "功能调用统计 [Hidden]"
__plugin_version__ = 0.1

statistics_group_file = DATA_PATH / "statistics" / "_prefix_count.json"
statistics_user_file = DATA_PATH / "statistics" / "_prefix_user_count.json"
_STATISTICS_FLUSH_INTERVAL = 1.0
_statistics_dirty = False
_statistics_flush_lock = asyncio.Lock()


def _mark_statistics_dirty() -> None:
    global _statistics_dirty
    _statistics_dirty = True


def _write_text_atomically(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        original_stat = path.stat()
    except FileNotFoundError:
        original_stat = None

    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(content)

        if original_stat is None:
            os.chmod(temp_path, 0o644)
        else:
            os.chmod(temp_path, stat.S_IMODE(original_stat.st_mode))
            temp_stat = temp_path.stat()
            if (temp_stat.st_uid, temp_stat.st_gid) != (original_stat.st_uid, original_stat.st_gid):
                os.chown(temp_path, original_stat.st_uid, original_stat.st_gid)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _persist_statistics(group_payload: str, user_payload: str) -> None:
    _write_text_atomically(statistics_group_file, group_payload)
    _write_text_atomically(statistics_user_file, user_payload)


async def _flush_statistics() -> None:
    global _statistics_dirty
    async with _statistics_flush_lock:
        if not _statistics_dirty:
            return
        try:
            group_payload = json.dumps(_prefix_count_dict, indent=4, ensure_ascii=False)
            user_payload = json.dumps(_prefix_user_count_dict, indent=4, ensure_ascii=False)
            _statistics_dirty = False
            await asyncio.to_thread(_persist_statistics, group_payload, user_payload)
        except Exception:
            _statistics_dirty = True
            logger.exception("功能调用统计文件写入失败，将在下个周期重试")


@scheduler.scheduled_job(
    "interval",
    seconds=_STATISTICS_FLUSH_INTERVAL,
    id="statistics-json-flush",
    max_instances=1,
    coalesce=True,
)
async def _flush_statistics_periodically() -> None:
    await _flush_statistics()


get_driver().on_shutdown(_flush_statistics)

try:

    with open(statistics_group_file, "r", encoding="utf8") as f:
        _prefix_count_dict = json.load(f)
except (FileNotFoundError, ValueError):
    _prefix_count_dict = {
        "total_statistics": {
            "total": {},
        },
        "day_statistics": {
            "total": {},
        },
        "week_statistics": {
            "total": {},
        },
        "month_statistics": {
            "total": {},
        },
        "start_time": str(datetime.now().date()),
        "day_index": 0,
    }

try:
    with open(statistics_user_file, "r", encoding="utf8") as f:
        _prefix_user_count_dict = json.load(f)
except (FileNotFoundError, ValueError):
    _prefix_user_count_dict = {
        "total_statistics": {
            "total": {},
        },
        "day_statistics": {
            "total": {},
        },
        "week_statistics": {
            "total": {},
        },
        "month_statistics": {
            "total": {},
        },
        "start_time": str(datetime.now().date()),
        "day_index": 0,
    }


# 以前版本转换
if _prefix_count_dict.get("day_index") is None:
    tmp = _prefix_count_dict.copy()
    _prefix_count_dict = {
        "total_statistics": tmp["total_statistics"],
        "day_statistics": {
            "total": {},
        },
        "week_statistics": {
            "total": {},
        },
        "month_statistics": {
            "total": {},
        },
        "start_time": tmp["start_time"],
        "day_index": 0,
    }


# 添加命令次数
@run_postprocessor
async def _(
    matcher: Matcher,
    exception: Optional[Exception],
    bot: Bot,
    event: GroupMessageEvent,
    state: T_State,
):
    global _prefix_count_dict
    if (
        matcher.type == "message"
        and matcher.priority not in [1, 9]
        and matcher.plugin_name not in ["update_info", "statistics_handle"]
    ):
        module = matcher.plugin_name
        day_index = _prefix_count_dict["day_index"]
        try:
            group_id = str(event.group_id)
        except AttributeError:
            group_id = "total"
        user_id = str(event.user_id)
        plugin_name = plugins2settings_manager.get_plugin_data(module)
        if plugin_name and plugin_name.get('cmd'):
            plugin_name = plugin_name.get('cmd')[0]
            check_exists_key(group_id, user_id, plugin_name)
            for data in [_prefix_count_dict, _prefix_user_count_dict]:
                data["total_statistics"]["total"][plugin_name] += 1
                data["day_statistics"]["total"][plugin_name] += 1
                data["week_statistics"]["total"][plugin_name] += 1
                data["month_statistics"]["total"][plugin_name] += 1
            # print(_prefix_count_dict)
            if group_id != "total":
                for data in [_prefix_count_dict, _prefix_user_count_dict]:
                    if data == _prefix_count_dict:
                        key = group_id
                    else:
                        key = user_id
                    data["total_statistics"][key][plugin_name] += 1
                    data["day_statistics"][key][plugin_name] += 1
                    data["week_statistics"][key][str(day_index % 7)][
                        plugin_name
                    ] += 1
                    data["month_statistics"][key][str(day_index % 30)][
                        plugin_name
                    ] += 1
            _mark_statistics_dirty()


def check_exists_key(group_id: str, user_id: str, plugin_name: str):
    global _prefix_count_dict, _prefix_user_count_dict
    for data in [_prefix_count_dict, _prefix_user_count_dict]:
        if data == _prefix_count_dict:
            key = group_id
        else:
            key = user_id
        if not data["total_statistics"]["total"].get(plugin_name):
            data["total_statistics"]["total"][plugin_name] = 0
        if not data["day_statistics"]["total"].get(plugin_name):
            data["day_statistics"]["total"][plugin_name] = 0
        if not data["week_statistics"]["total"].get(plugin_name):
            data["week_statistics"]["total"][plugin_name] = 0
        if not data["month_statistics"]["total"].get(plugin_name):
            data["month_statistics"]["total"][plugin_name] = 0
        
        if not data["total_statistics"].get(key):
            data["total_statistics"][key] = {}
        if not data["total_statistics"][key].get(plugin_name):
            data["total_statistics"][key][plugin_name] = 0
        if not data["day_statistics"].get(key):
            data["day_statistics"][key] = {}
        if not data["day_statistics"][key].get(plugin_name):
            data["day_statistics"][key][plugin_name] = 0

        if key != 'total':
            if not data["week_statistics"].get(key):
                data["week_statistics"][key] = {}
            if data["week_statistics"][key].get("0") is None:
                for i in range(7):
                    data["week_statistics"][key][str(i)] = {}
            if data["week_statistics"][key]["0"].get(plugin_name) is None:
                for i in range(7):
                    data["week_statistics"][key][str(i)][plugin_name] = 0
    
            if not data["month_statistics"].get(key):
                data["month_statistics"][key] = {}
            if data["month_statistics"][key].get("0") is None:
                for i in range(30):
                    data["month_statistics"][key][str(i)] = {}
            if data["month_statistics"][key]["0"].get(plugin_name) is None:
                for i in range(30):
                    data["month_statistics"][key][str(i)][plugin_name] = 0


# 天
@scheduler.scheduled_job(
    "cron",
    hour=0,
    minute=1,
)
async def _():
    for data in [_prefix_count_dict, _prefix_user_count_dict]:
        data["day_index"] += 1
        for x in data["day_statistics"].keys():
            for key in data["day_statistics"][x].keys():
                try:
                    data["day_statistics"][x][key] = 0
                except KeyError:
                    pass
        for type_ in ["week_statistics", "month_statistics"]:
            index = str(
                data["day_index"] % 7
                if type_ == "week_statistics"
                else data["day_index"] % 30
            )
            for x in data[type_].keys():
                try:
                    for key in data[type_][x][index].keys():
                        data[type_][x][index][key] = 0
                except KeyError:
                    pass
    _mark_statistics_dirty()
    logger.info(f"[定时任务]:功能调用次数已更新，等待批量落盘")
        

