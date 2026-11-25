import ast
import hashlib
import io
import os
import random
from collections import OrderedDict
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import nonebot
from nonebot_plugin_htmlrender import html_to_pic, template_to_html
from PIL import Image

from config.path_config import IMAGE_PATH, PROJECT_ROOT
from manager import (
    admin_manager,
    group_manager,
    plugins2settings_manager,
    plugins_manager,
    super_manager,
)
from services.log import logger
from utils.help_index import (
    HelpEntry,
    category_names,
    command_candidates,
    entry_usage,
    find_entries,
    normalize,
    static_entries,
)
from utils.imageutils import BuildImage as IMG
from utils.imageutils import Text2Image
from utils.utils import get_matcher_plugin, get_matchers

random_bk_path = IMAGE_PATH / "background" / "help" / "simple_help"

background = IMAGE_PATH / "background" / "usage.jpg"

# 帮助图内存缓存
_usage_img_cache: "OrderedDict[str, str]" = OrderedDict()
_USAGE_IMG_CACHE_LIMIT = 256

_PJSK_HELP_TYPE_MARKERS = ("烧烤", "uni移植")


def _is_pjsk_help_type(plugin_type) -> bool:
    if isinstance(plugin_type, (list, tuple, set)):
        return any(_is_pjsk_help_type(item) for item in plugin_type)
    return any(marker in str(plugin_type or "") for marker in _PJSK_HELP_TYPE_MARKERS)


def _configured_plugin_name(module: str, settings: dict) -> str:
    manager_data = plugins_manager.get(module) or {}
    name = manager_data.get("plugin_name")
    if name:
        return str(name)
    commands = settings.get("cmd") or []
    if isinstance(commands, (list, tuple)):
        for command in commands:
            if str(command) not in _PJSK_HELP_TYPE_MARKERS:
                return str(command)
    return module


def _static_ast_value(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                return None
            parts.append(value.value)
        return "".join(parts)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [_static_ast_value(value) for value in node.elts]
        return values if all(value is not None for value in values) else None
    if isinstance(node, ast.Dict):
        result = {}
        for key, value in zip(node.keys, node.values):
            key_value = _static_ast_value(key)
            value_value = _static_ast_value(value)
            if key_value is None or value_value is None:
                return None
            result[key_value] = value_value
        return result
    return None


def _static_command_aliases(tree: ast.AST) -> tuple[str, ...]:
    commands = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "on_command" or not node.args:
            continue
        command = _static_ast_value(node.args[0])
        if isinstance(command, str):
            commands.append(command)
        for keyword in node.keywords:
            if keyword.arg != "aliases":
                continue
            aliases = _static_ast_value(keyword.value)
            if isinstance(aliases, (list, tuple, set)):
                commands.extend(str(alias) for alias in aliases)
    return tuple(dict.fromkeys(commands))


def _append_go_pjsk_help_entries(
    plugins_data: dict[str, list[tuple[str, str, int]]],
    loaded_modules: set[str],
) -> None:
    # Go 模式没加载 plugins.pjsk，从静态索引补齐
    if os.getenv("KNDBOT_PJSK_RUNTIME", "").strip().lower() != "go":
        return

    configured = plugins2settings_manager.get_data()
    for entry in static_entries(PROJECT_ROOT / "src"):
        module = entry.module.rsplit(".", 1)[-1]
        if module in loaded_modules or entry.module in loaded_modules or not entry.pjsk or not entry.visible(0):
            continue
        settings = configured.get(module) or configured.get(entry.module) or {}
        level = settings.get("level", entry.settings.get("level", 5))
        try:
            level = int(level)
        except (ValueError, TypeError):
            level = 5
        plugin_type = entry.category
        if isinstance(plugin_type, (list, tuple)):
            plugin_type = plugin_type[0] if plugin_type else "娱乐功能"
        plugins_data.setdefault(str(plugin_type), []).append((module, entry.name, level))


async def create_help_img(
    group_id: Optional[int], simple_help_image: Path
):
    """
    异步 生成帮助图片
    :param group_id: 群号
    :param simple_help_image: 简易帮助图片路径
    """
    _plugins_data = {}
    _tmp = []
    # 插件分类
    for matcher in get_matchers():
        if matcher.plugin_name in _tmp:
            continue
        _tmp.append(matcher.plugin_name)
        plugin_name = None
        _plugin = get_matcher_plugin(matcher)
        if not _plugin:
            continue
        _module = _plugin.module
        _tmp.extend([_module.__name__, _module.__name__.rsplit(".", 1)[-1]])
        try:
            # 只为通用插件生成帮助图
            plugin_name = _module.__getattribute__("__plugin_name__")
            if (
                "[hidden]" in plugin_name.lower()
                or "[admin]" in plugin_name.lower()
                or "[superuser]" in plugin_name.lower()
                or plugin_name == "帮助"
            ):
                continue
            # 获取插件类型
            plugin_type = "娱乐功能"
            plugin_level = 5
            if plugins2settings_manager.get(matcher.plugin_name):
                plugin_level = plugins2settings_manager[matcher.plugin_name].get("level", 5)
                if plugins2settings_manager[matcher.plugin_name].get("plugin_type"):
                    plugin_type = plugins2settings_manager.get_plugin_data(matcher.plugin_name)["plugin_type"]
                    if isinstance(plugin_type, list):
                        plugin_type = plugin_type[0]
            else:
                try:
                    plugin_level = _module.__getattribute__("__plugin_settings__").get('level', 5)
                except:
                    pass
                try:
                    plugin_type = _module.__getattribute__("__plugin_type__")
                except AttributeError:
                    pass
            # 分类以源码为准
            plugin_type = getattr(_module, "__plugin_type__", plugin_type)
            if isinstance(plugin_type, (list, tuple)):
                plugin_type = plugin_type[0] if plugin_type else "娱乐功能"
            # 获取完插件信息，保存插件名数据，便于生成帮助图
            if plugin_type not in _plugins_data.keys():
                _plugins_data[plugin_type] = []
            _plugins_data[plugin_type].append((matcher.plugin_name, plugin_name, plugin_level))
        except AttributeError as e:
            logger.warning(f"获取功能 {matcher.plugin_name}: {plugin_name} 设置失败...e：{e}")
    _append_go_pjsk_help_entries(_plugins_data, set(_tmp))
    # 获取完所有的插件帮助信息，开始生成帮助图片
    types = list(_plugins_data.keys())

    types.sort(key=lambda x: len(_plugins_data[x]), reverse=True)
    # 开始生成简易帮助
    simple_help_tuple_dic = {}
    for type_ in types:
        simple_help_tuple_dic[type_] = []
        for i, k in enumerate(sorted(_plugins_data[type_])):
            # 超管禁用flag, True表示禁用
            flag = True
            if plugins_manager.get_plugin_status(k[0], "all"):
                flag = False
            if group_id:
                flag = (
                    flag or not plugins_manager.get_plugin_status(k[0], "group")
                    or not group_manager.get_plugin_status(k[0], group_id, True)
                    or group_manager.get_group_level(group_id) < k[2]
                )
            _pre_num = '1' if flag else '0'
            # 群聊禁用
            _post_num = '0' if group_id and group_manager.get_plugin_status(k[0], group_id) else '1'
            # (群功能状态|全局禁用状态, 序号.功能名)
            simple_help_tuple_dic[type_].append([_pre_num+_post_num, f"{i+1}.{k[1]}"])
    # 简易帮助图片合成
    random_bk = random.choice(os.listdir(random_bk_path))
    template_path = str(Path(__file__).parent / "templates")
    html = await template_to_html(
        template_path=template_path,
        template_name="help.html",
        bk=random_bk,
        pgs_dic=simple_help_tuple_dic,
    )
    pic = await html_to_pic(
        html=html,
        template_path=f"file://{template_path}/help.html",
        wait=0,
    )
    Image.open(io.BytesIO(pic)).save(simple_help_image)


def _resolve_plugin(module_name: str):
    # 嵌套子插件在 nonebot 里带父包前缀（如 pjsk:subscribe），直接查短名会找不到
    plugin = nonebot.plugin.get_plugin(module_name)
    if plugin is not None:
        return plugin
    for plugin_id in nonebot.plugin.get_loaded_plugins():
        pid = str(getattr(plugin_id, "id_", None) or getattr(plugin_id, "name", ""))
        module = getattr(plugin_id, "module", None)
        module_name_from_path = getattr(module, "__name__", "").rsplit(".", 1)[-1]
        candidates = {pid, pid.split(":")[-1], pid.rsplit(".", 1)[-1], module_name_from_path}
        if module_name in candidates:
            return plugin_id
    return None


_HELP_PJSK_PREFIXES = ("cnpjsk", "twpjsk", "pjsk")


def _help_command_candidates(msg: str) -> list[str]:
    return command_candidates(msg)


def _loaded_command_usage_module(command: str) -> Optional[str]:
    for plugin in nonebot.plugin.get_loaded_plugins():
        module = getattr(plugin, "module", None)
        usage_map = getattr(module, "__plugin_command_usage__", {})
        if not isinstance(usage_map, dict) or command not in usage_map:
            continue
        identifier = getattr(module, "__name__", "").rsplit(".", 1)[-1] if module is not None else ""
        if not identifier:
            identifier = getattr(plugin, "id_", None) or getattr(plugin, "name", None)
        if identifier:
            return str(identifier).split(":")[-1].rsplit(".", 1)[-1]
    return None


def _get_help_module(msg: str):
    is_category, entries = find_entries(get_help_entries(), msg, 2)
    if not is_category and len(entries) == 1:
        return entries[0].module.rsplit(".", 1)[-1]
    return None


def _configured_plugin_usage(module: str) -> Optional[str]:
    settings = plugins2settings_manager.get_plugin_data(module)
    if not isinstance(settings, dict):
        return None
    commands = [
        str(command)
        for command in (settings.get("cmd") or [])
        if str(command) not in _PJSK_HELP_TYPE_MARKERS
    ]
    if not commands:
        return None
    name = _configured_plugin_name(module, settings)
    return f"{name}\n\n可用指令：\n" + "\n".join(f"    {command}" for command in commands)


def get_help_entries() -> list[HelpEntry]:
    entries = deepcopy(list(static_entries(PROJECT_ROOT / "src")))
    by_module = {entry.module: entry for entry in entries}
    for plugin in nonebot.plugin.get_loaded_plugins():
        module = getattr(plugin, "module", None)
        module_name = getattr(module, "__name__", "")
        name = getattr(module, "__plugin_name__", None)
        if not module_name or not isinstance(name, str):
            continue
        entry = by_module.get(module_name)
        if entry is None:
            entry = HelpEntry(module_name, name)
            entries.append(entry)
            by_module[module_name] = entry
        entry.name = name
        entry.category = getattr(module, "__plugin_type__", entry.category)
        for attribute, target in (("__plugin_usage__", "usage"), ("__plugin_superuser_usage__", "super_usage")):
            value = getattr(module, attribute, None)
            if isinstance(value, str):
                setattr(entry, target, value)
        usage_map = getattr(module, "__plugin_command_usage__", {})
        if isinstance(usage_map, dict):
            entry.command_usage.update(usage_map)
            entry.aliases.update(normalize(alias) for alias in usage_map)
        settings = getattr(module, "__plugin_settings__", {})
        if isinstance(settings, dict):
            entry.aliases.update(normalize(alias) for alias in (settings.get("cmd") or []) if isinstance(alias, str))
        entry.aliases.update(normalize(alias) for alias in (getattr(module, "__plugin_help_aliases__", ()) or ()) if isinstance(alias, str))
        # 表情类靠 keywords 动态注册
        for attribute in ("commands", "memes", "SPECS"):
            registry = getattr(module, attribute, ()) or ()
            if isinstance(registry, dict):
                for command, details in registry.items():
                    if isinstance(command, str) and isinstance(details, dict) and "aliases" in details:
                        entry.aliases.add(normalize(command))
                        entry.aliases.update(normalize(alias) for alias in (details.get("aliases") or []) if isinstance(alias, str))
            else:
                for spec in registry:
                    entry.aliases.update(normalize(alias) for alias in getattr(spec, "keywords", ()) if isinstance(alias, str))
    for manager in (plugins2settings_manager, admin_manager, super_manager):
        for module_name, settings in manager.get_data().items():
            if not isinstance(settings, dict):
                continue
            matches = [entry for entry in entries if entry.module == module_name or entry.module.rsplit(".", 1)[-1] == module_name]
            if len(matches) == 1:
                matches[0].aliases.update(normalize(alias) for alias in (settings.get("cmd") or []) if isinstance(alias, str))
    return entries


def get_plugin_help_text(msg: str, user_type: int = 0) -> Optional[str]:
    is_category, entries = find_entries(get_help_entries(), msg, user_type)
    if not entries:
        return None
    if is_category or len(entries) > 1:
        lines = [f"{normalize(msg)}：功能列表", "发送 knd <指令> help 查看具体说明。"]
        for entry in sorted(entries, key=lambda item: (item.name, item.module)):
            aliases = sorted(alias for alias in entry.aliases if alias not in category_names(entry.category) and (user_type == 2 or alias not in entry.restricted_aliases))
            lines.append(f"• {entry.name}：{' / '.join(aliases[:8])}")
        return "\n".join(lines)
    return entry_usage(entries[0], msg, user_type)


def get_plugin_help(msg: str, user_type: int = 0) -> Optional[str]:
    text = get_plugin_help_text(msg, user_type)
    return _get_result_by_usage(text) if text else None


def _get_result_by_usage(usage):
    """
    通过usage获取功能的帮助图片
    :param usage: 帮助信息
    """
    key = hashlib.blake2b(usage.encode('utf-8'), digest_size=16).hexdigest()
    cached = _usage_img_cache.get(key)
    if cached is not None:
        _usage_img_cache.move_to_end(key)
        return cached
    fontname = "SourceHanSansCN-Regular.otf"
    textimg = Text2Image.from_text(
        usage,
        fontsize=24,
        fontname=fontname,
        ischeckchar=False
    ).to_image()
    width, height = textimg.size
    bk = IMG.open(IMAGE_PATH / "background" / "usage.jpg")
    scale = bk.width / bk.height
    width, height = max(int(height * scale), width) * 1.15, max(int(width / scale), height) * 1.2
    bk = bk.resize((int(width), int(height)))
    chara_size = int(0.15*width) if int(0.15*width) < height else height
    w_pos = int(width-0.95*chara_size)
    h_pos = int(height-chara_size)
    chara_bk = IMG.open(IMAGE_PATH / "background" / "knd.png").resize((chara_size, chara_size))
    bk.paste(chara_bk, (w_pos, h_pos), alpha=True)
    bk.paste(textimg, (int(width * 0.05), 0), alpha=True, center_type="by_height")
    result = bk.pic2bs4()
    _usage_img_cache[key] = result
    _usage_img_cache.move_to_end(key)
    while len(_usage_img_cache) > _USAGE_IMG_CACHE_LIMIT:
        _usage_img_cache.popitem(last=False)
    return result
