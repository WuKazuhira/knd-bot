"""cnskme：远程自动打歌账号的时间-排名曲线。

风格对齐 ycx 曲线（_sk_gradient_bg / _sk_title_panel / _sk_panel / _sk_chip）。
与 ycx 的区别是 Y 轴画排名且反向——排名数字越小越靠上。
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

from .._remote_sql import LiveRecord

# 曲线配色，与 ycx 的 real_colors 同源。WL 图改用角色印象色，这里是兜底。
CURVE_COLORS = [
    (210, 45, 95), (35, 115, 210), (45, 150, 85), (220, 125, 35),
    (120, 75, 200), (30, 160, 170), (200, 70, 175), (95, 95, 95),
]

DEFAULT_CHARA_COLOR = (136, 136, 136)


def parse_color(code: Optional[str]) -> Tuple[int, int, int]:
    """'#33aaee' -> (51, 170, 238)。"""
    if not code:
        return DEFAULT_CHARA_COLOR
    text = str(code).lstrip("#")
    if len(text) != 6:
        return DEFAULT_CHARA_COLOR
    try:
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return DEFAULT_CHARA_COLOR


def darken(color: Tuple[int, int, int], factor: float = 0.62) -> Tuple[int, int, int]:
    """印象色多为浅色，画线前压暗保证在白底上可读。"""
    return tuple(max(0, min(255, int(c * factor))) for c in color)  # type: ignore[return-value]


def lighten(color: Tuple[int, int, int], factor: float = 0.16) -> Tuple[int, int, int]:
    """按比例混白，用于区块底色。"""
    return tuple(
        max(0, min(255, int(c + (255 - c) * (1 - factor)))) for c in color
    )  # type: ignore[return-value]


def _fmt_point(value: Optional[int]) -> str:
    if value is None:
        return "-"
    if value >= 10000:
        return f"{value / 10000:.1f}万"
    return str(value)


def _fmt_rank(value: Optional[int]) -> str:
    return f"#{value}" if value else "-"


def rank_points(records: List[LiveRecord], rank_attr: str) -> List[Tuple[int, int]]:
    """取 (时间, 排名) 序列，丢掉非正排名。

    排名最小是 1；出现 <=1 的值说明解析异常，画进去会把曲线拉出画布。
    """
    points = []
    for record in records:
        value = getattr(record, rank_attr, None)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
            points.append((record.ts, value))
    return points


def compute_metrics(records: List[LiveRecord], point_attr: str, rank_attr: str) -> dict:
    """算当前排名/分数/近1h时速/近1h周回/累计周回。"""
    usable = [r for r in records if getattr(r, point_attr) is not None]
    result = {
        "rank": None,
        "point": None,
        "speed": None,
        "rounds_1h": 0,
        "rounds_total": len(records),
        "rank_delta": None,
    }
    if not usable:
        return result

    latest = usable[-1]
    result["point"] = getattr(latest, point_attr)
    result["rank"] = getattr(latest, rank_attr)

    now = int(time.time())
    hour_ago = now - 3600
    result["rounds_1h"] = sum(1 for r in records if r.ts >= hour_ago)

    # 时速：取近 1 小时窗口内最早的一点做差
    window = [r for r in usable if r.ts >= latest.ts - 3600]
    if len(window) >= 2:
        first = window[0]
        span = latest.ts - first.ts
        if span > 0:
            delta = getattr(latest, point_attr) - getattr(first, point_attr)
            result["speed"] = delta * 3600 / span / 10000

    ranked = [r for r in usable if getattr(r, rank_attr) is not None]
    if len(ranked) >= 2:
        window_ranked = [r for r in ranked if r.ts >= latest.ts - 3600]
        if len(window_ranked) >= 2:
            # 排名减小是上升，取正值表示上升了多少位
            result["rank_delta"] = getattr(window_ranked[0], rank_attr) - getattr(
                window_ranked[-1], rank_attr
            )
    return result


class CurveCanvas:
    """排名曲线画布。Y 轴为排名且反向。

    绘制辅助函数由调用方注入（sk/__init__.py 里已有一套），避免循环导入。
    """

    def __init__(
        self,
        width: int,
        height: int,
        helpers: dict,
        fonts: dict,
        margins: Tuple[int, int, int, int] = (100, 330, 80, 105),
    ):
        self.W = width
        self.H = height
        self.helpers = helpers
        self.f_title = fonts["title"]
        self.f_label = fonts["label"]
        self.f_small = fonts["small"]
        self.M_L, self.M_R, self.M_T, self.M_B = margins
        self.plot_w = width - self.M_L - self.M_R
        self.plot_h = height - self.M_T - self.M_B
        self.img = helpers["gradient_bg"](width, height)
        self.d = ImageDraw.Draw(self.img)
        self._label_boxes: List[Tuple[int, int, int, int]] = []
        self._pending_labels: List[Tuple[int, int, str, tuple]] = []
        self.start_ts = 0
        self.end_ts = 1
        self.min_rank = 1
        self.max_rank = 100

    def fit_text(self, text: str, font, max_width: int) -> str:
        return self.helpers["fit_text"](self.d, text, font, max_width)

    def set_ranges(self, start_ts: int, end_ts: int, ranks: List[int]) -> None:
        self.start_ts = start_ts
        self.end_ts = end_ts if end_ts > start_ts else start_ts + 3600
        if ranks:
            lo, hi = min(ranks), max(ranks)
            span = max(1, hi - lo)
            pad = max(1, int(span * 0.08))
            self.min_rank = max(1, lo - pad)
            self.max_rank = hi + pad
        else:
            self.min_rank, self.max_rank = 1, 100
        # 排名波动很小时把跨度撑开，否则 5 等分刻度会取整成重复标签
        if self.max_rank - self.min_rank < 5:
            center = (self.max_rank + self.min_rank) / 2
            self.min_rank = max(1, int(center - 2.5))
            self.max_rank = self.min_rank + 5

    def sy(self, rank: int) -> int:
        """排名越小越靠上，所以不做反转（min_rank 映射到顶部）。

        夹在绘图区内，避免异常值把曲线画到画布外面去。
        """
        ratio = (rank - self.min_rank) / (self.max_rank - self.min_rank)
        ratio = min(max(ratio, 0.0), 1.0)
        return int(self.M_T + ratio * self.plot_h)

    def sx(self, ts: int) -> int:
        ratio = (ts - self.start_ts) / (self.end_ts - self.start_ts)
        ratio = min(max(ratio, 0.0), 1.0)
        return int(self.M_L + ratio * self.plot_w)

    def draw_title(self, title: str, subtitle: str) -> None:
        self.helpers["title_panel"](self.img, title, self.f_title, subtitle, self.f_small, pad=18, height=54)
        self.d = ImageDraw.Draw(self.img)

    def draw_chapter_bands(self, bands: List[dict]) -> None:
        """按角色章节切分时间轴：底色区块 + 分割线 + 顶部头像。

        bands 里每项需要 chapter_no / start / end / color / chara_id / active。
        必须在 draw_frame 之后、画曲线之前调用。
        """
        if not bands:
            return

        top = self.M_T
        bottom = self.M_T + self.plot_h
        icon_loader = self.helpers.get("wl_icon")
        icon_size = 30

        overlay = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)

        for band in bands:
            x0 = self.sx(max(band["start"], self.start_ts))
            x1 = self.sx(min(band["end"], self.end_ts))
            if x1 <= x0:
                continue
            x0 = max(x0, self.M_L)
            x1 = min(x1, self.M_L + self.plot_w)
            color = band.get("color") or DEFAULT_CHARA_COLOR
            # 印象色淡淡铺一层，区分区块又不压过曲线
            alpha = 58 if band.get("active") else 34
            od.rectangle([(x0, top), (x1, bottom)], fill=(*color, alpha))

        # 背景是 RGB，用 paste + alpha 掩膜合成（alpha_composite 要求两边都是 RGBA）
        self.img.paste(overlay, (0, 0), overlay.split()[3])
        self.d = ImageDraw.Draw(self.img)

        # 分割线画在区块边界上
        boundaries = []
        for band in bands:
            for edge in (band["start"], band["end"]):
                if self.start_ts < edge < self.end_ts:
                    boundaries.append(edge)
        for edge in sorted(set(boundaries)):
            x = self.sx(edge)
            for y in range(top, bottom, 8):
                self.d.line([(x, y), (x, min(y + 4, bottom))], fill=(140, 120, 140), width=2)

        # 顶部角色头像与章节名
        for band in bands:
            x0 = self.sx(max(band["start"], self.start_ts))
            x1 = self.sx(min(band["end"], self.end_ts))
            if x1 <= x0:
                continue
            x0 = max(x0, self.M_L)
            x1 = min(x1, self.M_L + self.plot_w)
            mid = (x0 + x1) // 2
            color = band.get("color") or DEFAULT_CHARA_COLOR
            deep = darken(color, 0.55)
            band_w = x1 - x0

            icon = None
            if icon_loader and band_w >= icon_size + 8:
                icon = icon_loader(int(band.get("chara_id") or 0), icon_size)

            label = f"第{band['chapter_no']}章"
            if band.get("active"):
                label += "·进行中"
            label_w = int(self.d.textlength(label, font=self.f_small))

            if icon is not None:
                icon_x = mid - icon_size // 2
                icon_y = top + 6
                # 头像底下垫一圈印象色描边，强化归属
                self.d.ellipse(
                    [(icon_x - 3, icon_y - 3), (icon_x + icon_size + 3, icon_y + icon_size + 3)],
                    fill=(255, 255, 255), outline=deep, width=2,
                )
                self.img.paste(icon, (icon_x, icon_y), icon.split()[3])
                self.d = ImageDraw.Draw(self.img)
                # 占位登记，避免曲线端点标签压到头像
                self._label_boxes.append(
                    (icon_x - 3, icon_y - 3, icon_x + icon_size + 3, icon_y + icon_size + 3)
                )
                text_y = icon_y + icon_size + 12
            else:
                text_y = top + 14

            if band_w >= label_w + 10:
                box = (mid - label_w // 2 - 5, text_y - 8, mid + label_w // 2 + 5, text_y + 9)
                self.d.rounded_rectangle(
                    [(box[0], box[1]), (box[2], box[3])],
                    radius=6, fill=(255, 255, 255), outline=deep, width=1,
                )
                self.d.text((mid, text_y), label, font=self.f_small, fill=deep, anchor="mm")
                self._label_boxes.append(box)

    def draw_frame(self, remain_text: str) -> None:
        self.helpers["panel"](
            self.img,
            (self.M_L - 12, self.M_T - 12, self.M_L + self.plot_w + 12, self.M_T + self.plot_h + 12),
            radius=24,
            fill=(255, 255, 255, 222),
            outline=(255, 255, 255, 230),
        )
        self.d = ImageDraw.Draw(self.img)
        self.d.rounded_rectangle(
            [(self.M_L, self.M_T), (self.M_L + self.plot_w, self.M_T + self.plot_h)],
            radius=18, fill=(255, 255, 255), outline=(235, 210, 226), width=1,
        )

        # Y 轴：排名刻度，顶部为最好排名
        seen_ranks = set()
        for i in range(6):
            y = self.M_T + int(self.plot_h * i / 5)
            rank = int(round(self.min_rank + (self.max_rank - self.min_rank) * i / 5))
            self.d.line([(self.M_L, y), (self.M_L + self.plot_w, y)], fill=(240, 220, 230), width=1)
            if rank in seen_ranks:
                continue
            seen_ranks.add(rank)
            self.d.text((self.M_L - 10, y), f"#{rank}", font=self.f_small,
                        fill=(100, 80, 95), anchor="rm")

        duration = max(1, self.end_ts - self.start_ts)
        whole_days = int(duration // 86400)
        for day in range(whole_days + 1):
            ts = self.start_ts + day * 86400
            if ts > self.end_ts:
                break
            x = self.sx(ts)
            self.d.line([(x, self.M_T), (x, self.M_T + self.plot_h)], fill=(245, 230, 238), width=1)
            label = "0天" if day == 0 else f"第{day}天"
            self.d.text((x, self.M_T + self.plot_h + 18), label, font=self.f_small,
                        fill=(100, 80, 95), anchor="mm")
        if self.end_ts > self.start_ts + whole_days * 86400:
            x = self.sx(self.end_ts)
            self.d.line([(x, self.M_T), (x, self.M_T + self.plot_h)], fill=(245, 230, 238), width=1)
            self.d.text((x, self.M_T + self.plot_h + 18), "结束", font=self.f_small,
                        fill=(100, 80, 95), anchor="mm")

        now_ts = min(max(int(time.time()), self.start_ts), self.end_ts)
        now_x = self.sx(now_ts)
        self.d.line([(now_x, self.M_T), (now_x, self.M_T + self.plot_h)], fill=(170, 110, 140), width=2)
        self.d.text((now_x + 4, self.M_T + 8), "当前", font=self.f_small, fill=(150, 80, 115), anchor="la")

        self.d.text((self.M_L + self.plot_w // 2, self.H - 45),
                    f"活动经过时间（剩余 {remain_text}）", font=self.f_label, fill=(80, 60, 75), anchor="mm")
        self.d.text((30, self.M_T + self.plot_h // 2), "排名", font=self.f_label,
                    fill=(80, 60, 75), anchor="mm")

    def draw_series(self, points: List[Tuple[int, int]], color: tuple, label: str, width: int = 3) -> None:
        pts = [(self.sx(ts), self.sy(rank)) for ts, rank in points
               if self.start_ts <= ts <= self.end_ts]
        if len(pts) >= 2:
            self.d.line(pts, fill=color, width=width, joint="curve")
        if not pts:
            return
        x, y = pts[-1]
        self.d.ellipse([(x - 4, y - 4), (x + 4, y + 4)], fill=color)
        # 标签延后统一绘制，免得后画的曲线压在先画的标签上
        self._pending_labels.append((x, y, label, color))

    def flush_labels(self) -> None:
        for x, y, label, color in self._pending_labels:
            self._value_label(x, y, label, color)
        self._pending_labels.clear()

    def _value_label(self, x: int, y: int, text: str, color: tuple) -> None:
        plot_left = self.M_L + 2
        plot_right = self.M_L + self.plot_w - 4
        label = text
        tw = int(self.d.textlength(label, font=self.f_small))
        max_w = plot_right - plot_left - 10
        if tw > max_w:
            label = self.fit_text(text, self.f_small, max_w)
            tw = int(self.d.textlength(label, font=self.f_small))
        tx = x + 6
        if tx + tw + 5 > plot_right:
            tx = x - tw - 10
        tx = min(max(tx, plot_left), plot_right - tw - 5)

        top_limit = self.M_T + 2
        bottom_limit = self.M_T + self.plot_h - 18
        ty = min(max(y - 11, top_limit), bottom_limit)
        ty = self._free_slot(tx, ty, tw, top_limit, bottom_limit)

        self._label_boxes.append((tx - 3, ty - 1, tx + tw + 5, ty + 16))
        self.d.rounded_rectangle([(tx - 3, ty - 1), (tx + tw + 5, ty + 16)],
                                 radius=4, fill=(255, 255, 255), outline=color, width=1)
        self.d.text((tx, ty + 7), label, font=self.f_small, fill=color, anchor="lm")

    def _free_slot(self, tx: int, ty: int, tw: int, top: int, bottom: int) -> int:
        """多条曲线端点靠近时错开标签，避免叠字。"""
        def overlaps(candidate: int) -> bool:
            box = (tx - 3, candidate - 1, tx + tw + 5, candidate + 16)
            for other in self._label_boxes:
                if box[0] < other[2] and other[0] < box[2] and box[1] < other[3] and other[1] < box[3]:
                    return True
            return False

        if not overlaps(ty):
            return ty
        for step in range(1, 14):
            for candidate in (ty + step * 19, ty - step * 19):
                if top <= candidate <= bottom and not overlaps(candidate):
                    return candidate
        return ty

    def sidebar(self) -> Tuple[int, int, int]:
        """铺侧栏底板，返回 (x, y, 可用宽度)。"""
        panel_x = self.M_L + self.plot_w + 24
        panel_right = self.W - 24
        self.helpers["panel"](
            self.img,
            (panel_x - 12, self.M_T - 12, panel_right, self.M_T + self.plot_h + 12),
            radius=22, fill=(255, 255, 255, 205), outline=(255, 255, 255, 230),
        )
        self.d = ImageDraw.Draw(self.img)
        return panel_x, self.M_T, panel_right - panel_x

    def footer(self, text: str) -> None:
        self.helpers["chip"](
            self.d, (24, self.H - 34, self.W - 24, self.H - 8),
            self.fit_text(text, self.f_small, self.W - 72), self.f_small,
            fill=(255, 255, 255), outline=(255, 255, 255), text_fill=(120, 80, 100), anchor="lm",
        )


def compose_total_curve(
    region: str,
    event_id: int,
    event_name: str,
    records: List[LiveRecord],
    time_range: Tuple[int, int],
    remain_text: str,
    helpers: dict,
    fonts: dict,
) -> Image.Image:
    """总榜时间-排名曲线。"""
    canvas = CurveCanvas(1180, 700, helpers, fonts)
    points = rank_points(records, "event_rank")
    canvas.set_ranges(time_range[0], time_range[1], [rank for _, rank in points])

    canvas.draw_title(f"【{region.upper()}-{event_id}】{event_name}  个人总榜曲线", "cnskme")
    canvas.draw_frame(remain_text)

    metrics = compute_metrics(records, "event_point", "event_rank")
    color = CURVE_COLORS[0]
    if points:
        canvas.draw_series(points, color, f"{_fmt_rank(points[-1][1])}", width=3)
    canvas.flush_labels()

    lx, ly, panel_w = canvas.sidebar()
    d = canvas.d
    helpers["chip"](d, (lx, ly - 3, lx + 96, ly + 23), "当前状态", canvas.f_label,
                    fill=(255, 246, 251), text_fill=(60, 45, 60))
    ly += 34

    rows = [
        ("总榜排名", _fmt_rank(metrics["rank"])),
        ("活动分数", _fmt_point(metrics["point"])),
        ("时速", f"{metrics['speed']:.1f}万/h" if metrics["speed"] is not None else "-"),
        ("近1h周回", f"{metrics['rounds_1h']} 次"),
        ("累计周回", f"{metrics['rounds_total']} 次"),
        ("剩余", remain_text),
    ]
    if metrics["rank_delta"] is not None:
        delta = metrics["rank_delta"]
        arrow = f"↑{delta}" if delta > 0 else (f"↓{abs(delta)}" if delta < 0 else "持平")
        rows.insert(2, ("近1h排名", arrow))

    for name, value in rows:
        if ly > canvas.H - 90:
            break
        d.text((lx, ly), canvas.fit_text(name, canvas.f_small, panel_w), font=canvas.f_small,
               fill=(120, 95, 115), anchor="la")
        d.text((lx + panel_w, ly), canvas.fit_text(str(value), canvas.f_label, panel_w - 70),
               font=canvas.f_label, fill=(60, 45, 60), anchor="ra")
        ly += 30

    if points:
        first_ts = points[0][0]
        span_h = max(0.0, (points[-1][0] - first_ts) / 3600)
        d.text((lx, ly + 6), canvas.fit_text(f"记录跨度 {span_h:.1f} 小时", canvas.f_small, panel_w),
               font=canvas.f_small, fill=(120, 95, 115), anchor="la")

    canvas.footer(f"共 {len(records)} 条记录；排名轴向上为更高排名")
    return canvas.img


def compose_wl_curve(
    region: str,
    event_id: int,
    event_name: str,
    chapter_records: Dict[int, List[LiveRecord]],
    chapter_meta: Dict[int, dict],
    time_range: Tuple[int, int],
    remain_text: str,
    helpers: dict,
    fonts: dict,
) -> Image.Image:
    """WL 各章节分榜排名曲线。"""
    canvas = CurveCanvas(1180, 760, helpers, fonts)

    all_ranks: List[int] = []
    series: Dict[int, List[Tuple[int, int]]] = {}
    for chapter_no, records in chapter_records.items():
        pts = rank_points(records, "wl_chapter_rank")
        if pts:
            series[chapter_no] = pts
            all_ranks.extend(rank for _, rank in pts)
    canvas.set_ranges(time_range[0], time_range[1], all_ranks)

    canvas.draw_title(f"【{region.upper()}-{event_id}】{event_name}  个人WL分榜曲线", "cnskme WL")
    canvas.draw_frame(remain_text)

    chapter_order = sorted(chapter_records.keys())

    # 角色印象色：底色用原色（淡铺），曲线用压暗版保证白底可读
    base_color_of: Dict[int, Tuple[int, int, int]] = {}
    for idx, chapter_no in enumerate(chapter_order):
        meta = chapter_meta.get(chapter_no) or {}
        raw = meta.get("color")
        base_color_of[chapter_no] = (
            parse_color(raw) if raw else CURVE_COLORS[idx % len(CURVE_COLORS)]
        )
    color_of = {no: darken(base_color_of[no]) for no in chapter_order}

    # 章节时间区块：所有已知章节都画，哪怕还没有记录，便于看清赛程
    bands = []
    for chapter_no in sorted(chapter_meta.keys()):
        meta = chapter_meta[chapter_no] or {}
        start, end = meta.get("start"), meta.get("end")
        if not start or not end:
            continue
        base = parse_color(meta.get("color")) if meta.get("color") else CURVE_COLORS[
            (chapter_no - 1) % len(CURVE_COLORS)
        ]
        bands.append({
            "chapter_no": chapter_no,
            "start": start,
            "end": end,
            "color": base,
            "chara_id": meta.get("gameCharacterId"),
            "active": bool(meta.get("active")),
        })
    canvas.draw_chapter_bands(bands)

    for chapter_no in chapter_order:
        pts = series.get(chapter_no)
        if not pts:
            continue
        canvas.draw_series(pts, color_of[chapter_no], f"第{chapter_no}章 {_fmt_rank(pts[-1][1])}")
    canvas.flush_labels()

    lx, ly, panel_w = canvas.sidebar()
    d = canvas.d
    helpers["chip"](d, (lx, ly - 3, lx + 110, ly + 23), "分榜状态", canvas.f_label,
                    fill=(255, 246, 251), text_fill=(60, 45, 60))
    ly += 32

    icon_loader = helpers.get("wl_icon")
    for chapter_no in chapter_order:
        if ly > canvas.H - 110:
            d.text((lx, ly), "…", font=canvas.f_small, fill=(120, 95, 115), anchor="la")
            break
        records = chapter_records.get(chapter_no) or []
        metrics = compute_metrics(records, "wl_chapter_point", "wl_chapter_rank")
        color = color_of[chapter_no]
        meta = chapter_meta.get(chapter_no) or {}

        # 该章整行铺一层印象色，和主图区块对应
        canvas.helpers["panel"](
            canvas.img,
            (lx - 6, ly - 5, lx + panel_w + 6, ly + 85),
            radius=10,
            fill=(*base_color_of[chapter_no], 40),
            outline=(*color, 90),
        )
        d = ImageDraw.Draw(canvas.img)

        d.line([(lx, ly + 9), (lx + 26, ly + 9)], fill=color, width=4)
        head_x = lx + 34
        if icon_loader:
            icon = icon_loader(int(meta.get("gameCharacterId") or 0), 22)
            if icon:
                canvas.img.paste(icon, (head_x, ly - 2), icon.split()[3])
                head_x += 28
                d = ImageDraw.Draw(canvas.img)
        title = f"第{chapter_no}章"
        if meta.get("active"):
            title += "（进行中）"
        d.text((head_x, ly + 9), canvas.fit_text(title, canvas.f_label, panel_w - (head_x - lx)),
               font=canvas.f_label, fill=darken(base_color_of[chapter_no], 0.5), anchor="lm")
        ly += 26

        speed_text = f"{metrics['speed']:.1f}万/h" if metrics["speed"] is not None else "-"
        detail_rows = [
            f"排名 {_fmt_rank(metrics['rank'])}   分数 {_fmt_point(metrics['point'])}",
            f"时速 {speed_text}   近1h周回 {metrics['rounds_1h']}",
            f"本章周回 {metrics['rounds_total']} 次",
        ]
        for line in detail_rows:
            if ly > canvas.H - 84:
                break
            d.text((lx + 12, ly), canvas.fit_text(line, canvas.f_small, panel_w - 12),
                   font=canvas.f_small, fill=(100, 75, 90), anchor="la")
            ly += 18
        ly += 10

    total = sum(len(v) for v in chapter_records.values())
    canvas.footer(
        f"WL 分榜记录共 {total} 条；底色区块与虚线为各角色章节赛程，曲线取角色印象色，排名轴向上为更高排名"
    )
    return canvas.img
