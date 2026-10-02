"""帮助专用的只读源码索引；不导入业务插件，不回写持久设置。"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

HELP_PATTERN = r"(?is)^.*(?:怎么用|帮助|help)\s*$"
PJSK_CATEGORY = "烧烤相关&uni移植"


def normalize(value: str) -> str:
    return " ".join(str(value).split()).casefold()


def parse_help_request(
    message: str,
    command_starts: Iterable[str] = ("/", "／", "!", "！"),
    nicknames: Iterable[str] = ("knd",),
) -> str | None:
    """None 表示非帮助；空字符串表示总览。起始符/昵称沿用当前驱动配置。"""
    value = message.strip()
    for prefix in sorted({str(p) for p in command_starts if p}, key=lambda p: (-len(p), p)):
        if value.startswith(prefix):
            value = value[len(prefix):].lstrip()
            break
    match = re.fullmatch(r"(.*?)(?:怎么用|帮助|help)\s*", value, re.I | re.S)
    if not match:
        return None
    query = normalize(match.group(1))
    # knd 是统一入口；其它昵称只来自配置。最长匹配且要求边界，不能截断功能词。
    names = {normalize(name) for name in nicknames if name} | {"knd"}
    for name in sorted(names, key=lambda p: (-len(p), p)):
        if query == name:
            return ""
        if query.startswith(name) and query[len(name):len(name) + 1] in {" ", ",", "，", ":", "："}:
            return query[len(name):].lstrip(" ,，:：")
    return query


def delegate_world_bloom_help(message: str, go_owned: bool, command_starts: Iterable[str] = ()) -> bool:
    """仅直达 Go 的 WL 帮助让出；统一入口/昵称入口仍由 Python 回答。"""
    if not go_owned:
        return False
    value = message.strip()
    prefixes = {str(p) for p in command_starts if p} | {"!", "！", "/", "／", "."}
    for prefix in sorted(prefixes, key=lambda p: (-len(p), p)):
        if value.startswith(prefix):
            value = value[len(prefix):].lstrip()
            break
    return re.fullmatch(r"(?:cn|tw)?wl边框\s*(?:help|帮助)\s*", value, re.I) is not None


def command_candidates(value: str) -> list[str]:
    value = normalize(value)
    if not value:
        return []
    result = [value]
    # 仅 PJSK 索引项使用这些候选，避免把全站 cn/tw 开头的名称截断。
    for prefix in ("cnpjsk", "twpjsk", "jppjsk", "pjsk", "cn", "tw", "jp"):
        if value.startswith(prefix) and (rest := value[len(prefix):].strip()):
            if rest not in result:
                result.append(rest)
    return result


def category_names(value: Any) -> tuple[str, ...]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result = set()
    for item in values:
        name = normalize(item or "娱乐功能")
        result.add(name)
        result.update(part.strip() for part in name.split("&") if part.strip())
        if "烧烤" in name or "uni移植" in name:
            result.update((PJSK_CATEGORY, "烧烤相关", "uni移植", "烧烤"))
    return tuple(sorted(result))


def literal(node: ast.AST, values: dict[str, Any] | None = None) -> Any:
    """安全读取常量及常量引用，绝不 eval/import。未知字典项不丢弃其余可读项。"""
    values = values or {}
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return values.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts = [literal(part, values) for part in node.values]
        return "".join(parts) if all(isinstance(part, str) for part in parts) else None
    if isinstance(node, ast.FormattedValue) and node.conversion == -1 and node.format_spec is None:
        value = literal(node.value, values)
        return str(value) if isinstance(value, (str, int, float)) else None
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        result = [value for part in node.elts if (value := literal(part, values)) is not None]
        return tuple(result) if isinstance(node, ast.Tuple) else result
    if isinstance(node, ast.Dict):
        return {key: literal(v, values) for k, v in zip(node.keys, node.values) if k is not None and (key := literal(k, values)) is not None}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = literal(node.left, values), literal(node.right, values)
        if isinstance(left, (str, list)) and type(left) is type(right):
            return left + right
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "strip" and not node.args and not node.keywords:
        value = literal(node.func.value, values)
        return value.strip() if isinstance(value, str) else None
    return None


def source_values(tree: ast.AST) -> dict[str, Any]:
    values = {}
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        for target in targets:
            if isinstance(target, ast.Name):
                values[target.id] = literal(node.value, values)
    return values


@dataclass
class HelpEntry:
    module: str
    name: str
    category: Any = "娱乐功能"
    settings: dict = field(default_factory=dict)
    usage: str = ""
    super_usage: str = ""
    command_usage: dict = field(default_factory=dict)
    aliases: set[str] = field(default_factory=set)
    restricted_aliases: set[str] = field(default_factory=set)
    # 正则只用于定位已有功能，不把每一种参数输入虚构为独立别名。
    patterns: list[tuple[str, int]] = field(default_factory=list)

    @property
    def permission(self) -> int:
        name = self.name.casefold()
        return 2 if "[superuser]" in name else 1 if "[admin]" in name else 0

    @property
    def hidden(self) -> bool:
        return "[hidden]" in self.name.casefold()

    @property
    def pjsk(self) -> bool:
        return self.module.startswith("plugins.pjsk.") or "烧烤相关" in category_names(self.category)

    def visible(self, user_type: int) -> bool:
        return (not self.hidden or self.module == "basic_plugins.invite_manager") and user_type >= self.permission


def add_tree_aliases(entry: HelpEntry, tree: ast.AST, values: dict) -> None:
    # logos 等通过 commands 字典生成 matcher；仅识别显式 aliases 注册结构。
    registry = values.get("commands")
    if isinstance(registry, dict):
        for command, details in registry.items():
            if isinstance(command, str) and isinstance(details, dict) and "aliases" in details:
                entry.aliases.add(normalize(command))
                entry.aliases.update(normalize(alias) for alias in (details.get("aliases") or []) if isinstance(alias, str))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func.id if isinstance(node.func, ast.Name) else ""
        aliases = []
        restricted = False
        if function == "on_command" and node.args:
            command = literal(node.args[0], values)
            if isinstance(command, str):
                aliases.append(command)
            elif isinstance(command, tuple) and all(isinstance(part, str) for part in command):
                aliases.append(" ".join(command))
            for keyword in node.keywords:
                if keyword.arg == "aliases":
                    found = literal(keyword.value, values)
                    if isinstance(found, (list, tuple, set)):
                        aliases.extend(found)
                elif keyword.arg == "permission":
                    # SUPERUSER | GROUP 并非超管独占。
                    restricted = isinstance(keyword.value, ast.Name) and keyword.value.id == "SUPERUSER"
        elif function == "on_regex" and node.args:
            pattern = literal(node.args[0], values)
            restricted = any(keyword.arg == "permission" and isinstance(keyword.value, ast.Name) and keyword.value.id == "SUPERUSER" for keyword in node.keywords)
            if isinstance(pattern, str):
                entry.patterns.append((pattern, 2 if restricted else 0))
        elif function in {"Command", "Meme", "GifMeme"}:
            position = 1 if function == "Command" else 2
            if len(node.args) > position:
                found = literal(node.args[position], values)
                if isinstance(found, (list, tuple, set)):
                    aliases.extend(found)
            if len(node.args) > position + 1:
                pattern = literal(node.args[position + 1], values)
                if isinstance(pattern, str):
                    entry.patterns.append((pattern, 0))
        for alias in aliases:
            if isinstance(alias, tuple) and all(isinstance(part, str) for part in alias):
                alias = " ".join(alias)
            if isinstance(alias, str):
                entry.aliases.add(normalize(alias))
                if restricted:
                    entry.restricted_aliases.add(normalize(alias))


@lru_cache(maxsize=2)
def static_entries(source_root: Path) -> tuple[HelpEntry, ...]:
    trees = {}
    for folder in ("plugins", "basic_plugins"):
        for path in sorted((source_root / folder).rglob("*.py")):
            # Python 同名包优先于旧 .py；避免把已遮蔽的历史入口列为有效命令。
            if path.name != "__init__.py" and (path.with_suffix("") / "__init__.py").is_file():
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, UnicodeError, SyntaxError):
                continue
            trees[path] = (tree, source_values(tree))
    owners = {}
    for path, (tree, values) in trees.items():
        name = values.get("__plugin_name__")
        if not isinstance(name, str):
            continue
        module = ".".join(path.relative_to(source_root).with_suffix("").parts)
        if path.name == "__init__.py":
            module = module.removesuffix(".__init__")
        settings = values.get("__plugin_settings__") or values.get("__plugin_settings") or {}
        command_usage = values.get("__plugin_command_usage__")
        usage = values.get("__plugin_usage__")
        super_usage = values.get("__plugin_superuser_usage__")
        entry = HelpEntry(
            module=module,
            name=name,
            category=values.get("__plugin_type__") or "娱乐功能",
            settings=settings if isinstance(settings, dict) else {},
            usage=usage if isinstance(usage, str) else "",
            super_usage=super_usage if isinstance(super_usage, str) else "",
            command_usage=command_usage if isinstance(command_usage, dict) else {},
        )
        entry.aliases.update(normalize(alias) for alias in (entry.settings.get("cmd") or []) if isinstance(alias, str))
        entry.aliases.update(normalize(alias) for alias in re.sub(r"\[(?:Admin|Superuser|Hidden)\]", "", name, flags=re.I).split("/") if alias.strip())
        entry.aliases.update(normalize(alias) for alias in entry.command_usage)
        entry.aliases.update(normalize(alias) for alias in (values.get("__plugin_help_aliases__") or []) if isinstance(alias, str))
        owners[path] = entry
    for path, (tree, values) in trees.items():
        owner = owners.get(path)
        if owner is None:
            for parent in path.parents:
                owner = owners.get(parent / "__init__.py")
                if owner or parent == source_root:
                    break
        if owner:
            add_tree_aliases(owner, tree, values)
    return tuple(owners.values())


def find_entries(entries: Iterable[HelpEntry], query: str, user_type: int) -> tuple[bool, list[HelpEntry]]:
    """类别优先，别名歧义返回列表，不随机取第一个。"""
    entries = list(entries)
    query = normalize(query)
    categories = [e for e in entries if query in category_names(e.category)]
    if categories:
        return True, [e for e in categories if e.visible(user_type)]
    matches = []
    for entry in entries:
        candidates = command_candidates(query) if entry.pjsk else [query]
        if entry.visible(user_type) and any(c in entry.aliases and (user_type == 2 or c not in entry.restricted_aliases) for c in candidates):
            matches.append(entry)
    if matches:
        return False, matches
    # 字面量入口优先；仅完整命中既有正则时定位其模块，避免宽泛前缀吞词。
    for entry in entries:
        if not entry.visible(user_type):
            continue
        for pattern, required_type in entry.patterns:
            regex = _compiled_help_pattern(pattern)
            if user_type >= required_type and regex is not None and regex.fullmatch(query):
                matches.append(entry)
                break
    return False, matches


@lru_cache(maxsize=512)
def _compiled_help_pattern(pattern: str):
    try:
        return re.compile(pattern, re.I)
    except re.error:
        return None


def entry_usage(entry: HelpEntry, query: str, user_type: int) -> str:
    candidates = command_candidates(query) if entry.pjsk else [normalize(query)]
    usage_map = {normalize(k): v for k, v in entry.command_usage.items()}
    result = next((usage_map[c] for c in candidates if isinstance(usage_map.get(c), str) and usage_map[c].strip()), entry.usage)
    if not result:
        aliases = sorted(a for a in entry.aliases if a not in category_names(entry.category) and (user_type == 2 or a not in entry.restricted_aliases))
        result = entry.name + "\n\n帮助用法暂未提供参数说明。可查询的入口：\n" + "\n".join(aliases)
    if user_type == 2 and entry.super_usage and entry.super_usage not in result:
        result += "\n\n==========超管额外命令==========\n" + entry.super_usage
    return result
