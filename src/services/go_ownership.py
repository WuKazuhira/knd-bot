"""Go 迁移所有权兼容层。

当前部署中 remote/sk 指令仍由 Python 插件处理；当 Go 服务接管某项
功能时，可通过环境变量列出对应 ownership key，使 Python matcher 安静退出。
"""

from __future__ import annotations

import json
import os
from functools import lru_cache


@lru_cache(maxsize=1)
def _owned() -> frozenset[str]:
    raw = (os.getenv("KND_GO_OWNED_COMMANDS") or "").strip()
    if not raw:
        return frozenset()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = [item.strip() for item in raw.split(",") if item.strip()]
    if not isinstance(data, list):
        return frozenset()
    return frozenset(str(item).strip() for item in data if str(item).strip())


def go_owns(key: str) -> bool:
    """返回指定功能是否由 Go 接管；未配置时默认由 Python 处理。"""
    return key in _owned()


# pjsk 指令别名 → 规范名映射，镜像 go-pjsk-bot 各 r.Register(name, aliases)。
# KND_GO_OWNED_COMMANDS 里运维填写的是规范名（与 Go 一致）；用户可能输入别名或带
# cn/tw 前缀，这里统一归一化到规范名后再查 owned 集合，避免别名绕过导致双回复。
_PJSK_ALIAS_TO_CANON: dict[str, str] = {}


def _register_alias(canonical: str, *aliases: str) -> None:
    _PJSK_ALIAS_TO_CANON[canonical] = canonical
    for a in aliases:
        _PJSK_ALIAS_TO_CANON[a] = canonical


# 与 go-pjsk-bot internal/pjsk/*.go 的 Register 调用保持一致。
_register_alias("pjsk b30", "pjskb30", "烧烤b30", "烧烤 b30", "b30")
_register_alias("bind", "绑定")
_register_alias("unbind", "解绑")
_register_alias("给看", "不给看")
_register_alias("查时间")
_register_alias("卡牌一览", "cardbox", "卡面一览", "卡一览")
_register_alias("挑战组卡", "挑战配队", "挑战卡组")
_register_alias("难度排行", "ap难度排行", "fc难度排行")
_register_alias("findevent", "查活动", "查询活动", "活动图鉴", "活动总览", "活动手册", "活动列表")
_register_alias("findcard", "查卡", "查询卡面")
_register_alias("cardinfo")
_register_alias("event")
_register_alias("rk")
_register_alias("逮捕")
_register_alias("pjsk抽卡")
_register_alias("msr", "msmap", "msa")
_register_alias("msg", "msgate")
_register_alias("msm", "mss", "mssong")
_register_alias("烤森材料", "mysekai材料")
_register_alias("msb", "mysekai蓝图", "mysekaiblueprint")
_register_alias("msf", "mysekai家具", "家具列表", "mysekaifurniture")
_register_alias("msd", "烤森抓包", "烤森抓包数据", "pjsk烤森抓包")
_register_alias("msp", "mysekai照片", "mysekaiphoto")
_register_alias("msr订阅", "msr推送订阅", "msr自动推送")
_register_alias("msr取消订阅", "msr推送取消", "msr取消推送")
_register_alias("cnmsr启用")
_register_alias("cnmsr禁用")
_register_alias("cnmsr白名单")
_register_alias("烧烤档案", "profile", "pjskprofile", "个人信息")
_register_alias("清除个人信息背景", "清空个人信息背景", "清除个人背景")
_register_alias("调整个人信息", "设置个人信息")
_register_alias("打歌分数", "分数配置", "pjsk分数")
_register_alias("设置打歌分数", "修改打歌分数", "设置分数")
_register_alias("pjsk进度", "pjskrop", "烧烤进度")
_register_alias("sks", "时速", "sk时速", "日速", "sk日速", "半日速", "sk半日速")
_register_alias("skl", "排名线", "sk排名线", "sk线")
_register_alias("sk预测", "活动预测", "skp")
_register_alias("ycx曲线", "sk预测曲线", "活动预测曲线")
_register_alias("cf", "查房")
_register_alias("sk")
_register_alias("csb", "查水表")
_register_alias("wlsk", "wl查房")
_register_alias("wlcsb", "wl查水表")
_register_alias("wlsks", "wl时速", "wlsk时速", "wl日速", "wlsk日速", "wl半日速", "wlsk半日速")
_register_alias("wlskl", "wl排名线", "wlsk排名线", "wlsk线")
_register_alias("订阅sk", "sk订阅")
_register_alias("退订sk", "取消订阅sk", "sk取消订阅", "sk退订")
_register_alias("清空sk订阅")
_register_alias("pjskinfo", "song", "查曲")
_register_alias("查物量")
_register_alias("pjskalias", "查别称")
_register_alias("pjskdel")
_register_alias("pjskset")
_register_alias("虚拟live", "vlive", "pjsklive列表")
_register_alias("pjsk开启新曲通知", "pjsk新曲通知开启")
_register_alias("pjsk关闭新曲通知", "pjsk新曲通知关闭")
_register_alias("pjsk开启live通知", "pjsk开启Live通知")
_register_alias("pjsk关闭live通知", "pjsk关闭Live通知")
_register_alias("pjsk新曲提醒")
_register_alias("pjsk取消新曲提醒")
_register_alias("pjsklive提醒")
_register_alias("pjsk取消live提醒")
_register_alias("pjsk订阅状态")
_register_alias("ycm", "车来", "有车吗", "推车")


def _strip_server_prefix(name: str) -> str:
    """去掉 cn/tw 服务器前缀（若去掉后剩余部分是已知命令）。"""
    for prefix in ("cn", "tw"):
        if name.startswith(prefix):
            rest = name[len(prefix):]
            if rest in _PJSK_ALIAS_TO_CANON:
                return rest
    return name


def pjsk_canonical_command(trigger: str) -> str | None:
    """把用户触发的 pjsk 命令名（含别名/cn·tw 前缀）归一化到 Go 规范名。

    无法识别为已迁移命令时返回 None（该命令仍由 Python 处理）。
    """
    if not trigger:
        return None
    name = trigger.strip()
    if name in _PJSK_ALIAS_TO_CANON:
        return _PJSK_ALIAS_TO_CANON[name]
    stripped = _strip_server_prefix(name)
    return _PJSK_ALIAS_TO_CANON.get(stripped)


def pjsk_command_owned_by_go(trigger: str) -> bool:
    """判断某个 pjsk 触发命令是否已由 Go 接管（用于 Python matcher 退场）。

    仅当命令能归一化到规范名、且该规范名在 KND_GO_OWNED_COMMANDS 中时返回 True。
    未配置 owned 列表或命令无法识别时返回 False（默认由 Python 处理，绝不误吞）。
    """
    canon = pjsk_canonical_command(trigger)
    if canon is None:
        return False
    return canon in _owned()

