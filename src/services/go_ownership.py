from __future__ import annotations

import json
import os
import re
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
    return key in _owned()


# 别名/前缀统一归一化到 Go 规范名，避免双回复
_PJSK_ALIAS_TO_CANON: dict[str, str] = {}


def _register_alias(canonical: str, *aliases: str) -> None:
    for trigger in (canonical, *aliases):
        key = trigger.lower()
        previous = _PJSK_ALIAS_TO_CANON.get(key)
        if previous is not None and previous != canonical:
            raise ValueError(f"PJSK alias collision: {trigger!r}")
        _PJSK_ALIAS_TO_CANON[key] = canonical


_register_alias("pjsk b30", "pjskb30", "烧烤b30", "烧烤 b30", "b30")
_register_alias("bind", "绑定")
_register_alias("unbind", "解绑")
_register_alias("给看", "不给看")
_register_alias("查时间")
_register_alias("卡牌一览", "cardbox", "卡面一览", "卡一览")
_register_alias("card")
_register_alias("难度排行", "ap难度排行", "fc难度排行")
_register_alias("findevent", "查活动", "查询活动", "活动图鉴", "活动总览", "活动手册", "活动列表")
_register_alias("findcard", "查卡", "查询卡面")
_register_alias("cardinfo", "cn_cardinfo", "tw_cardinfo")
_register_alias("event")
_register_alias("rk")
_register_alias("逮捕")
_register_alias("pjsk抽卡")
_register_alias("pjskupload", "上传用户信息")
_register_alias("msr", "msmap", "msa", "pjsk mysekai res", "mysekai 资源")
_register_alias("msg", "msgate", "pjsk mysekai gate", "msgall", "msgln", "msgmmj", "msgvbs", "msgws", "msg25")
_register_alias("msm", "mss", "mssong", "pjsk mysekai musicrecord")
_register_alias("烤森材料", "mysekai材料", "pjsk mysekai材料")
_register_alias("msb", "mysekai蓝图", "mysekaiblueprint", "mysekai blueprint", "mysekai 蓝图", "pjsk mysekai blueprint")
_register_alias("msf", "mysekai家具", "家具列表", "mysekaifurniture", "mysekai 家具", "pjsk mysekai fixture", "pjsk mysekai furniture")
_register_alias("msd", "烤森抓包", "烤森抓包数据", "pjsk烤森抓包", "pjsk烤森抓包数据")
_register_alias("msp", "mysekai照片", "mysekaiphoto", "mysekai 照片", "pjsk mysekai photo", "pjsk mysekai picture")
_register_alias("msr订阅", "msr推送订阅", "msr自动推送")
_register_alias("msr取消订阅", "msr推送取消", "msr取消推送")
_register_alias("cnmsr启用")
_register_alias("cnmsr禁用")
_register_alias("cnmsr白名单")
_register_alias("烧烤档案", "profile", "pjskprofile", "个人信息")
_register_alias("上传个人信息背景", "上传个人背景")
_register_alias("清除个人信息背景", "清空个人信息背景", "清除个人背景")
_register_alias("调整个人信息", "设置个人信息")
_register_alias("pjsk进度", "pjskrop", "烧烤进度")
_register_alias("sks", "时速", "sk时速", "日速", "sk日速", "半日速", "sk半日速")
_register_alias("skl", "排名线", "sk排名线", "sk线")
_register_alias("SKAPI切换", "skapi切换", "SK API切换", "sk api切换", "SKAPI切换新", "SKAPI切换旧", "skapi切换新", "skapi切换旧")
_register_alias("sk预测", "活动预测", "skp", "ycx", "ycxall", "lsycx")
_register_alias("ycx曲线", "sk预测曲线", "活动预测曲线")
_register_alias("skme", "sk我的曲线")
_register_alias("cf", "查房", "wlcf")
_register_alias("sk")
_register_alias("csb", "查水表")
_register_alias("wl边框")
_register_alias("wlsk", "wl查房")
_register_alias("wlcsb", "wl查水表")
_register_alias("wlsks", "wl时速", "wlsk时速", "wl日速", "wlsk日速", "wl半日速", "wlsk半日速")
_register_alias("wlskl", "wl排名线", "wlsk排名线", "wlsk线")
_register_alias("订阅sk", "sk订阅", "订阅cnsk", "订阅twsk")
_register_alias("退订sk", "取消订阅sk", "sk取消订阅", "sk退订", "取消订阅cnsk", "取消订阅twsk")
_register_alias("清空sk订阅")
_register_alias("guess")
_register_alias("结束猜曲", "结束猜卡面", "结束猜谱面")
_register_alias("来点提示", "来丶提示", "给点提示", "给丶提示")
_register_alias("pjskinfo", "song", "查曲")
_register_alias("查物量")
_register_alias("谱面预览", "谱面预览1", "谱面预览2", "谱面预览3")
_register_alias("技能预览")
_register_alias("pjskbpm", "bpm", "查曲bpm")
_register_alias("查bpm")
_register_alias("pjskalias", "查别称")
_register_alias("pjskdel")
_register_alias("pjskset")
_register_alias("生成难度csv", "生成难度json")
_register_alias("pjsk更新")
_register_alias("pjsk活动更新")
_register_alias("pjsk数据去重", "pjsk资源去重")
_register_alias("查询uni分布式")
_register_alias("添加uni分布式", "删除uni分布式")
_register_alias("虚拟live", "vlive", "pjsklive列表")
_register_alias("pjsk开启新曲通知", "pjsk新曲通知开启")
_register_alias("pjsk关闭新曲通知", "pjsk新曲通知关闭")
_register_alias("pjsk开启live通知", "pjsk开启Live通知")
_register_alias("pjsk关闭live通知", "pjsk关闭Live通知")
_register_alias("pjsk开启新卡通知", "pjsk新卡通知开启")
_register_alias("pjsk关闭新卡通知", "pjsk新卡通知关闭")
_register_alias("pjsk开启新服装通知", "pjsk新服装通知开启")
_register_alias("pjsk关闭新服装通知", "pjsk新服装通知关闭")
_register_alias("新卡速报", "pjsk新卡速报", "新卡速递", "pjsk新卡速递", "新卡情报", "pjsk新卡情报", "新卡", "pjsk新卡", "leak", "pjskleak")
_register_alias("查服装", "pjsk查服装")
_register_alias("新服装速报", "pjsk新服装速报", "新服装", "pjsk新服装")
_register_alias("pjsk新曲提醒")
_register_alias("pjsk取消新曲提醒")
_register_alias("pjsklive提醒")
_register_alias("pjsk取消live提醒")
_register_alias("pjsk订阅状态")
_register_alias("ycm", "车来", "有车吗", "推车")


def _strip_server_prefix(name: str) -> str:
    for prefix in ("cn", "tw"):
        if name.startswith(prefix):
            rest = name[len(prefix):]
            if rest in _PJSK_ALIAS_TO_CANON:
                return rest
    return name


def pjsk_canonical_command(trigger: str) -> str | None:
    if not trigger:
        return None
    name = _command_body(trigger).lower()
    if name in _PJSK_ALIAS_TO_CANON:
        return _PJSK_ALIAS_TO_CANON[name]
    stripped = _strip_server_prefix(name)
    return _PJSK_ALIAS_TO_CANON.get(stripped)


def pjsk_command_owned_by_go(trigger: str) -> bool:
    canon = pjsk_canonical_command(trigger)
    if canon is None:
        return False
    return canon in _owned()


_COMMAND_STARTS = ("!", "！", "/", "／", ".")


def _command_body(text: str) -> str:
    text = text.strip()
    if text.startswith(_COMMAND_STARTS):
        return text[1:].strip()
    return text


_PJSK_REGEX_BODY_PATTERNS: list[tuple[str, str]] = [
    ("pjsk抽卡", r"^(cn|tw|jp)? *(?:pjsk|sekai) *(反向?)? *(抽卡|十连抽?|[0-9]+连抽?) *([0-9]+)?$"),
    ("pjskset", r"^(cn|tw)?pjskset\s*(.+to.+)$"),
    ("guess", r"^(cn|tw|jp)?\s*(?:pjsk|sekai)\s*(普通|正常|阴间|非人类)?\s*猜卡面\s*([123]?)$"),
    ("guess", r"^(cn|tw|jp)?\s*(?:pjsk|sekai)\s*(普通|正常|阴间|非人类)?\s*(?:猜曲|识曲)\s*([123]?)$"),
    ("guess", r"^(cn|tw|jp)?\s*(?:pjsk|sekai)\s*(听歌|倒放|歌词)(?:\s*(?:猜曲|识曲))?\s*$"),
    ("guess", r"^(cn|tw|jp)?\s*(?:pjsk|sekai)\s*(?:猜曲|识曲)\s*([457])$"),
    ("guess", r"^(cn|tw|jp)?\s*(?:pjsk|sekai)\s*(?:谱面\s*(?:猜曲|识曲)\s*6?|猜谱面|(?:猜曲|识曲)\s*6)\s*$"),
    ("guess", r"^(cn|tw|jp)?\s*(?:pjsk|sekai)\s*(正常|阴间|非人类|听歌|倒放|谱面|歌词)?\s*(猜曲|猜卡面|猜谱面)\s*排(?:(?:行|名)榜?|榜)\s*([0-9]*)$"),
]
_PJSK_REGEX_COMMANDS = [
    (canonical, re.compile(pattern, re.IGNORECASE))
    for canonical, pattern in _PJSK_REGEX_BODY_PATTERNS
]
_PJSK_REGEX_COMMANDS.append(("wlsk", re.compile(r"^(?:cn|tw)?wlsk\s*-?[0-9]", re.IGNORECASE)))


def pjsk_text_owned_by_go(raw_text: str) -> bool:
    if not raw_text:
        return False
    text = _command_body(raw_text)
    for canonical, pattern in _PJSK_REGEX_COMMANDS:
        if canonical in _owned() and pattern.match(text):
            return True
    return False


