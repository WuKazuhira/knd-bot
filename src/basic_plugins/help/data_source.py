import ast
import hashlib
import io
import os
import random
from collections import OrderedDict
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
from utils.imageutils import BuildImage as IMG
from utils.imageutils import Text2Image
from utils.utils import get_matcher_plugin, get_matchers

random_bk_path = IMAGE_PATH / "background" / "help" / "simple_help"

background = IMAGE_PATH / "background" / "usage.jpg"

# 单功能帮助图缓存：usage 文本不变则直接复用 base64（进程内，重启失效）
_usage_img_cache: "OrderedDict[str, str]" = OrderedDict()
_USAGE_IMG_CACHE_LIMIT = 256

_PJSK_HELP_TYPE_MARKERS = ("烧烤", "uni移植")


def _is_pjsk_help_type(plugin_type) -> bool:
    """判断插件设置是否属于 PJSK 功能分类。"""
    if isinstance(plugin_type, (list, tuple, set)):
        return any(_is_pjsk_help_type(item) for item in plugin_type)
    return any(marker in str(plugin_type or "") for marker in _PJSK_HELP_TYPE_MARKERS)


def _configured_plugin_name(module: str, settings: dict) -> str:
    """从运行时插件数据或配置命令中取得帮助列表展示名。"""
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
    """读取仅由字面量组成的 AST 值，兼容无插值的 f-string。"""
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
    """提取源码中的 on_command 主命令和 aliases，供单功能帮助匹配。"""
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


@lru_cache(maxsize=1)
def _static_pjsk_help_entries() -> tuple[tuple[str, str, str, tuple[str, ...], int], ...]:
    """从 PJSK 源码提取未加载 matcher 的帮助元数据。"""
    root = PROJECT_ROOT / "src" / "plugins" / "pjsk"
    entries = []
    if not root.is_dir():
        return ()

    for path in sorted(root.glob("*/__init__.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError):
            continue

        values: dict[str, Any] = {}
        for node in tree.body:
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {
                    "__plugin_name__",
                    "__plugin_type__",
                    "__plugin_settings__",
                }:
                    value = _static_ast_value(node.value)
                    if value is not None:
                        values[target.id] = value

        plugin_name = values.get("__plugin_name__")
        plugin_type = values.get("__plugin_type__")
        settings = values.get("__plugin_settings__")
        if not isinstance(plugin_name, str) or not isinstance(plugin_type, str):
            continue
        lowered_name = plugin_name.lower()
        if any(marker in lowered_name for marker in ("[hidden]", "[admin]", "[superuser]")):
            continue
        if not _is_pjsk_help_type(plugin_type):
            continue

        commands = list(_static_command_aliases(tree))
        if isinstance(settings, dict) and isinstance(settings.get("cmd"), (list, tuple)):
            commands.extend(str(command) for command in settings["cmd"])
        commands.extend(part.strip() for part in plugin_name.split("/") if part.strip())
        commands = tuple(dict.fromkeys(commands))
        level = settings.get("level", 5) if isinstance(settings, dict) else 5
        try:
            level = int(level)
        except (TypeError, ValueError):
            level = 5
        entries.append((path.parent.name, plugin_name, plugin_type, commands, level))
    return tuple(entries)


def _static_pjsk_help_module(msg: str) -> Optional[str]:
    """按源码元数据查找 PJSK 命令对应的模块。"""
    value = str(msg or "").strip().lower()
    if not value:
        return None
    for candidate in _help_command_candidates(value):
        candidate = candidate.lower()
        for module, _name, _type, commands, _level in _static_pjsk_help_entries():
            if candidate in {command.lower() for command in commands}:
                return module
    return None


def _append_go_pjsk_help_entries(
    plugins_data: dict[str, list[tuple[str, str, int]]],
    loaded_modules: set[str],
) -> None:
    """补充 Go 模式下未加载 Python matcher 的 PJSK 帮助条目。

    Go 常驻模式刻意不加载 ``plugins.pjsk``，因此不能依赖 matcher 注册结果。
    旧的 ``plugins2settings.yaml`` 只覆盖仍由 Python 加载的插件，已迁移到 Go
    的功能必须从 PJSK 源码元数据补回帮助总览。
    """
    if os.getenv("KNDBOT_PJSK_RUNTIME", "").strip().lower() != "go":
        return

    configured = plugins2settings_manager.get_data()
    for module, settings in configured.items():
        if module in loaded_modules or not isinstance(settings, dict):
            continue
        if not _is_pjsk_help_type(settings.get("plugin_type")):
            continue

        plugin_name = _configured_plugin_name(module, settings)
        lowered_name = plugin_name.lower()
        if any(marker in lowered_name for marker in ("[hidden]", "[admin]", "[superuser]")):
            continue

        plugin_type = settings.get("plugin_type") or "娱乐功能"
        if isinstance(plugin_type, (list, tuple)):
            plugin_type = plugin_type[0] if plugin_type else "娱乐功能"
        plugin_type = str(plugin_type)
        plugin_level = settings.get("level", 5)
        try:
            plugin_level = int(plugin_level)
        except (TypeError, ValueError):
            plugin_level = 5
        plugins_data.setdefault(plugin_type, []).append((module, plugin_name, plugin_level))

    configured_modules = set(configured)
    for module, plugin_name, plugin_type, _commands, plugin_level in _static_pjsk_help_entries():
        if module in loaded_modules or module in configured_modules:
            continue
        plugins_data.setdefault(plugin_type, []).append((module, plugin_name, plugin_level))


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
            # 获取完插件信息，保存插件名数据，便于生成帮助图
            if plugin_type not in _plugins_data.keys():
                _plugins_data[plugin_type] = []
            _plugins_data[plugin_type].append((matcher.plugin_name, plugin_name, plugin_level))
        except AttributeError as e:
            logger.warning(f"获取功能 {matcher.plugin_name}: {plugin_name} 设置失败...e：{e}")
    # Go 模式不加载 plugins.pjsk，但仍从静态配置补齐 PJSK 功能列表。
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
    """按模块名取插件。

    plugins2settings 里存的是 matcher.plugin_name（子插件为短名，如 subscribe），
    而 nonebot.get_plugin 需要完整标识符（父插件:子插件，如 pjsk:subscribe），
    直接查会得到 None，导致 pjsk 等嵌套插件的单功能帮助全部失效。
    """
    plugin = nonebot.plugin.get_plugin(module_name)
    if plugin is not None:
        return plugin
    for plugin_id in nonebot.plugin.get_loaded_plugins():
        pid = getattr(plugin_id, "id_", None) or getattr(plugin_id, "name", "")
        if pid.split(":")[-1] == module_name:
            return plugin_id
    return None


_HELP_PJSK_PREFIXES = ("cnpjsk", "twpjsk", "pjsk")


def _help_command_candidates(msg: str) -> list[str]:
    """生成帮助查询候选词，兼容 PJSK 命令的服别/功能前缀写法。"""
    value = str(msg or "").strip()
    if not value:
        return []
    candidates = [value]
    lowered = value.lower()
    for prefix in _HELP_PJSK_PREFIXES:
        if lowered.startswith(prefix):
            rest = value[len(prefix):].strip()
            if rest and rest not in candidates:
                candidates.append(rest)
    return candidates


def _get_help_module(msg: str):
    for candidate in _help_command_candidates(msg):
        module = plugins2settings_manager.get_plugin_module(candidate)
        if module:
            return module
    return _static_pjsk_help_module(msg)


def _static_plugin_usage(module: str) -> Optional[str]:
    """Go 模式未加载 Python matcher 时，从源码静态读取插件用法。"""
    source_root = PROJECT_ROOT / "src"
    relative = str(module).replace(".", "/")
    candidates = [source_root / f"{relative}.py", source_root / relative / "__init__.py"]
    if "." not in str(module):
        candidates.extend([
            source_root / "plugins" / "pjsk" / str(module) / "__init__.py",
            source_root / "plugins" / str(module) / "__init__.py",
            source_root / "basic_plugins" / str(module) / "__init__.py",
            source_root / "basic_plugins" / f"{module}.py",
        ])
    for path in candidates:
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError):
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            if not any(isinstance(target, ast.Name) and target.id == "__plugin_usage__" for target in targets):
                continue
            value = node.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == "strip"
                and not value.args
                and not value.keywords
            ):
                value = value.func.value
            usage = _static_ast_value(value)
            if isinstance(usage, str) and usage.strip():
                return usage.strip()
    return None


def _configured_plugin_usage(module: str) -> Optional[str]:
    """没有静态用法时，用运行时静态配置生成最小帮助文本。"""
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


def get_plugin_help(msg: str, user_type: int = 0) -> Optional[str]:
    """
    获取功能的帮助信息
    :param msg: 功能cmd
    :param user_type: 标识用户类型，0为一般用户，1为管理员，2为超管
    """
    result = ""
    # 获取普通插件帮助说明
    normal_module = _get_help_module(msg)
    if normal_module:
        _plugin = _resolve_plugin(normal_module)
        if _plugin:
            _module = _plugin.module
            try:
                result = _module.__getattribute__("__plugin_usage__")
            except AttributeError:
                result = ""
            if user_type == 2:
                try:
                    if extra := _module.__getattribute__("__plugin_superuser_usage__"):
                        result += "\n{:=^70s}\n".format("超管额外命令") if result else ""
                        result += extra
                except AttributeError:
                    pass
        else:
            # Go PJSK 模式不加载 Python matcher，仍从源码/静态配置提供帮助。
            result = _static_plugin_usage(normal_module) or _configured_plugin_usage(normal_module) or ""
    # 获取管理插件帮助说明
    if user_type > 0:
        admin_module = admin_manager.get_plugin_module(msg)
        if admin_module:
            _plugin = _resolve_plugin(admin_module)
            if not _plugin:
                return None
            _module = _plugin.module
            try:
                result = _module.__getattribute__("__plugin_usage__")
            except AttributeError:
                result = ""
            if user_type == 2:
                try:
                    if extra := _module.__getattribute__("__plugin_superuser_usage__"):
                        result += "\n{:=^70s}\n".format("超管额外命令") if result else ""
                        result += extra
                except AttributeError:
                    pass
    # 获取超管帮助说明
    if user_type == 2:
        superuser_module = super_manager.get_plugin_module(msg)
        if superuser_module:
            _plugin = _resolve_plugin(superuser_module)
            if not _plugin:
                return None
            _module = _plugin.module
            try:
                result = _module.__getattribute__("__plugin_usage__")
            except AttributeError:
                result = ""
    if result:
        return _get_result_by_usage(result)
    return None


def _get_result_by_usage(usage):
    """
    通过usage获取功能的帮助图片（带进程内缓存）
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
