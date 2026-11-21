use std::{
    collections::{BTreeMap, HashMap, HashSet},
    path::{Path, PathBuf},
};

use crate::core::{RenderOutput, RenderRequestContext, Renderer, Result};
use crate::gfx::{
    GfxContext, RenderContextGfxExt, TextAnchor,
    TextAnchor::{La, Lm, Mm, Rm},
    draw_line, paste_rgba, resize_lanczos, vertical_gradient,
};
use async_trait::async_trait;
use image::RgbaImage;
use serde_json::Value;

use crate::renderers::common::{
    BOLD, MEDIUM, decimal, fit_text, label, number, render_jpeg, rgba, rounded_panel_overlay, rows,
    string,
};

pub(super) const TEXT: [u8; 3] = [50, 30, 50];
pub(super) const MUTED: [u8; 3] = [120, 80, 100];
pub(super) const WHITE: [u8; 3] = [255; 3];
pub(super) const CHIP: [u8; 3] = [255, 246, 251];
pub(super) const BORDER: [u8; 3] = [235, 210, 226];
pub(super) const SIDE: [u8; 3] = [60, 45, 60];
const CHIP_BORDER: [u8; 3] = [245, 218, 232];
const HEAD: [u8; 3] = [230, 140, 170];
const PINK: [u8; 3] = [255, 128, 178];
const ACCENT: [u8; 3] = [208, 95, 142];
const WARN: [u8; 3] = [200, 60, 60];

pub(super) type Rect = (i32, i32, i32, i32);
pub(super) type Point = (i32, i32);
pub(super) type Sample = (i64, i64);
type Color = [u8; 3];

#[derive(Clone, Copy)]
pub(super) struct Font(pub &'static str, pub f32);
pub(super) const SMALL: Font = Font(MEDIUM, 13.0);
pub(super) const LABEL: Font = Font(MEDIUM, 16.0);

pub(super) struct Canvas<'a> {
    pub image: RgbaImage,
    pub gfx: &'a GfxContext,
    pub width: i32,
    pub height: i32,
}

impl<'a> Canvas<'a> {
    pub fn new(gfx: &'a GfxContext, width: i32, height: i32) -> Self {
        let mut image = vertical_gradient(
            width as u32,
            height as u32,
            [255, 246, 250],
            [236, 244, 255],
        );
        let glows = [
            (
                (
                    (-width).div_euclid(5),
                    (-height).div_euclid(4),
                    width / 2,
                    height / 3,
                ),
                [255u8, 190, 220],
                62,
            ),
            (
                (
                    width / 2,
                    height / 4,
                    width + width / 4,
                    height + height / 5,
                ),
                [170, 210, 255],
                54,
            ),
        ];
        let stride = width as usize * 4;
        let raw = image.as_mut();
        for (bounds, color, alpha) in glows {
            let (l, t, r, b) = bounds;
            let (x0, x1) = (l.max(0), r.min(width - 1));
            let (y0, y1) = (t.max(0), b.min(height - 1));
            if x0 > x1 || y0 > y1 {
                continue;
            }
            let inv_rx = 1.0 / f64::from((r - l).max(1));
            let inv_ry = 1.0 / f64::from((b - t).max(1));
            let inv_alpha = 255 - alpha;
            for y in y0..=y1 {
                let dy = f64::from(2 * y - t - b) * inv_ry;
                let dy_sq = dy * dy;
                if dy_sq > 1.0 {
                    continue;
                }
                let row = &mut raw[y as usize * stride + x0 as usize * 4
                    ..=y as usize * stride + x1 as usize * 4 + 3];
                for (dx_idx, pixel) in row.chunks_exact_mut(4).enumerate() {
                    let x = x0 + dx_idx as i32;
                    let dx = f64::from(2 * x - l - r) * inv_rx;
                    if dx * dx + dy_sq <= 1.0 {
                        for channel in 0..3 {
                            pixel[channel] = ((u32::from(pixel[channel]) * inv_alpha
                                + u32::from(color[channel]) * alpha
                                + 127)
                                / 255) as u8;
                        }
                    }
                }
            }
        }
        Self {
            image,
            gfx,
            width,
            height,
        }
    }

    pub fn text(
        &mut self,
        pos: Point,
        text: &str,
        Font(font, size): Font,
        color: Color,
        anchor: TextAnchor,
    ) {
        label(
            self.gfx,
            &mut self.image,
            pos,
            text,
            (font, size, color, anchor),
        );
    }

    pub fn fit(&self, text: &str, Font(font, size): Font, width: i32) -> String {
        fit_text(&self.gfx.fonts, text, font, size, width as f32)
    }

    pub fn panel(
        &mut self,
        bounds: Rect,
        radius: u32,
        fill: (Color, u8),
        outline: Option<(Color, u8)>,
    ) {
        rounded_panel_overlay(
            self.gfx,
            &mut self.image,
            bounds,
            radius,
            rgba(fill.0, fill.1),
            outline.map(|(color, alpha)| rgba(color, alpha)),
        );
    }

    pub fn rect(&mut self, bounds: Rect, radius: u32, fill: Color, outline: Option<Color>) {
        self.panel(
            bounds,
            radius,
            (fill, 255),
            outline.map(|color| (color, 255)),
        );
    }

    pub fn chip(
        &mut self,
        bounds: Rect,
        text: &str,
        font: Font,
        fill: Color,
        color: Color,
        anchor: TextAnchor,
    ) {
        let (l, t, r, b) = bounds;
        self.rect(
            bounds,
            14,
            fill,
            Some(if fill == WHITE { WHITE } else { CHIP_BORDER }),
        );
        let x = if anchor == Lm { l + 12 } else { (l + r) / 2 };
        self.text((x, (t + b) / 2), text, font, color, anchor);
    }

    pub fn title(
        &mut self,
        title: &str,
        subtitle: &str,
        pad: i32,
        height: i32,
        title_size: f32,
        small_size: f32,
    ) {
        let right = self.width - pad;
        self.panel(
            (pad, pad, right, pad + height),
            22,
            (WHITE, 222),
            Some((WHITE, 230)),
        );
        self.rect((pad + 16, pad + 15, pad + 78, pad + 21), 3, PINK, None);
        let font = Font(BOLD, title_size);
        let title = self.fit(
            title,
            font,
            right - pad - 44 - if subtitle.is_empty() { 0 } else { 150 },
        );
        self.text((pad + 18, pad + height / 2 + 8), &title, font, TEXT, Lm);
        if !subtitle.is_empty() {
            self.chip(
                (right - 142, pad + 16, right - 14, pad + 44),
                subtitle,
                Font(MEDIUM, small_size),
                CHIP,
                MUTED,
                Mm,
            );
        }
    }

    pub fn line(&mut self, (x, y): Point, (xx, yy): Point, color: Color, width: f32) {
        draw_line(
            &mut self.image,
            (x as f32 + 0.5, y as f32 + 0.5),
            (xx as f32 + 0.5, yy as f32 + 0.5),
            rgba(color, 255),
            width,
        );
    }

    pub fn icon(&mut self, icon: &RgbaImage, (x, y): Point) {
        paste_rgba(&mut self.image, icon, x, y);
    }
}

pub(super) fn width(gfx: &GfxContext, text: &str, Font(font, size): Font) -> i32 {
    gfx.fonts.measure_text(font, size, text).0 as i32
}

pub(super) fn wl_icon(
    gfx: &GfxContext,
    root: &Path,
    id: i64,
    size: u32,
) -> Option<std::sync::Arc<RgbaImage>> {
    ["", "_2"].iter().find_map(|suffix| {
        gfx.cache
            .open_pjsk_image(
                root.join(format!("chara/chr_ts_90_{id}{suffix}.png")),
                Some((size, size)),
            )
            .ok()
    })
}

pub(super) fn chara_colors(ctx: &RenderRequestContext, payload: &Value) -> HashMap<i64, String> {
    let mut colors = HashMap::new();
    if let Ok(data) = ctx.app.master_data.load_master_data(
        "gameCharacterUnits.json",
        number(&payload["pjsk_type"]) as i32,
    ) {
        for item in crate::components::normalize_master_list(&data) {
            let code = string(&item["colorCode"]);
            if !item["gameCharacterId"].is_null() && !code.is_empty() {
                colors
                    .entry(number(&item["gameCharacterId"]))
                    .or_insert_with(|| code.to_owned());
            }
        }
    }
    colors
}

pub(super) fn int_map(value: &Value) -> BTreeMap<i64, &Value> {
    value
        .as_object()
        .into_iter()
        .flatten()
        .filter_map(|(key, value)| Some((key.parse().ok()?, value)))
        .collect()
}

pub(super) fn fallback<'a>(value: &'a Value, default: &'a str) -> &'a str {
    let value = string(value);
    if value.is_empty() { default } else { value }
}

pub(super) use crate::core::context::truthy as truth;

fn uid(value: &Value) -> String {
    if value.is_null() {
        String::new()
    } else if let Some(value) = value.as_str() {
        value.to_owned()
    } else {
        value.to_string()
    }
}

fn last_four(uid: &str) -> &str {
    uid.char_indices()
        .rev()
        .nth(3)
        .map_or(uid, |(i, _)| &uid[i..])
}

fn masked(value: &Value) -> String {
    let uid = uid(value);
    let suffix = last_four(&uid);
    if suffix.len() < uid.len() {
        format!("****{suffix}")
    } else {
        uid
    }
}

fn player_name(p: &Value) -> String {
    format!("{}({})", string(&p["name"]), masked(&p["uid"]))
}

fn score(value: f64) -> String {
    if value == 0.0 {
        "-".into()
    } else {
        format!("{:.2}万", value / 10000.0)
    }
}

fn speed(value: &Value, unit: &str) -> String {
    if value.is_null() {
        "-".into()
    } else {
        format!("{:.1}{unit}", decimal(value))
    }
}

fn stripe(i: usize) -> Color {
    if i % 2 == 0 { WHITE } else { [255, 240, 248] }
}

#[derive(Clone)]
struct Cell {
    text: String,
    font: Font,
    color: Color,
}
impl Cell {
    fn new(text: impl Into<String>, font: Font, color: Color) -> Self {
        Self {
            text: text.into(),
            font,
            color,
        }
    }
}

fn cells<T: Into<String>>(
    texts: impl IntoIterator<Item = T>,
    font: Font,
    color: Color,
) -> Vec<Cell> {
    texts
        .into_iter()
        .map(|text| Cell::new(text, font, color))
        .collect()
}

fn columns(
    gfx: &GfxContext,
    entries: impl IntoIterator<Item = impl AsRef<[Cell]>>,
    count: usize,
    pad: i32,
) -> Vec<i32> {
    let mut widths = vec![0; count];
    for row in entries {
        for (w, cell) in widths.iter_mut().zip(row.as_ref()) {
            *w = (*w).max(width(gfx, &cell.text, cell.font));
        }
    }
    widths.iter_mut().for_each(|w| *w += pad * 2);
    widths
}

struct TableRow<'a> {
    widths: &'a [i32],
    right_pad: Option<i32>,
    dividers: bool,
    radius: u32,
}

impl TableRow<'_> {
    fn draw(
        &self,
        c: &mut Canvas,
        (left, y, right, bottom): Rect,
        entries: &[Cell],
        bg: Color,
        center: i32,
    ) {
        c.rect((left, y, right, bottom), self.radius, bg, Some(WHITE));
        let mut x = left;
        for (i, (cell, &w)) in entries.iter().zip(self.widths).enumerate() {
            let (px, anchor) = match self.right_pad {
                Some(pad) if i > 0 => (x + w - pad, Rm),
                _ => (x + w / 2, Mm),
            };
            c.text((px, center), &cell.text, cell.font, cell.color, anchor);
            x += w;
            if self.dividers && i + 1 < self.widths.len() {
                c.rect((x - 1, y + 8, x + 1, bottom - 8), 1, BORDER, None);
            }
        }
    }
}

fn table_panel(c: &mut Canvas, y: i32, alpha: u8) {
    c.panel(
        (18, y - 4, c.width - 18, c.height - 18),
        20,
        (WHITE, alpha),
        Some((WHITE, 210)),
    );
}

#[derive(Clone, Copy)]
pub(super) enum Sk {
    Single,
    Multi,
    RankTable,
    WlRankTable,
    Forecast,
    WlForecast,
    ForecastCurve,
    Cf,
    CfRange,
    Csb,
}

#[async_trait]
impl Renderer for Sk {
    fn name(&self) -> &'static str {
        match self {
            Self::Single => "sk_single",
            Self::Multi => "sk_multi",
            Self::RankTable => "sk_rank_table",
            Self::WlRankTable => "sk_wl_rank_table",
            Self::Forecast => "sk_forecast",
            Self::WlForecast => "sk_wl_forecast",
            Self::ForecastCurve => "sk_forecast_curve",
            Self::Cf => "sk_cf",
            Self::CfRange => "sk_cf_range",
            Self::Csb => "sk_csb",
        }
    }

    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput> {
        let task = *self;
        let root: PathBuf = ctx.app.paths.static_dir.clone();
        let event = string(&payload["event_asset"]);
        let team = string(&payload["team_asset"]);
        let team_image = if matches!(task, Self::Single) && !event.is_empty() && !team.is_empty() {
            let (directory, file) = (
                format!("ondemand/event/{event}/team_image"),
                format!("{team}.png"),
            );
            ctx.app
                .get_asset_cached(
                    &directory,
                    &file,
                    number(&payload["pjsk_type"]) as i32,
                    ctx.policy,
                    true,
                )
                .await
                .ok()
                .flatten()
        } else {
            None
        };
        render_jpeg(ctx.gfx()?, 88, true, move |gfx| match task {
            Self::Single => single(gfx, &payload, team_image.as_deref()),
            Self::Multi => multi(gfx, &payload),
            Self::RankTable => rank_table(gfx, &payload, &root, false),
            Self::WlRankTable => rank_table(gfx, &payload, &root, true),
            Self::Forecast => forecast(gfx, &payload, &root, false),
            Self::WlForecast => forecast(gfx, &payload, &root, true),
            Self::ForecastCurve => forecast_curve(gfx, &payload),
            Self::Cf => cf(gfx, &payload),
            Self::CfRange => cf_range(gfx, &payload),
            Self::Csb => csb(gfx, &payload),
        })
        .await
    }
}

fn single(gfx: &GfxContext, p: &Value, team_image: Option<&RgbaImage>) -> RgbaImage {
    let near = rows(&p["near_ranks"]);
    let team = rows(&p["team_info"]);
    let remain = string(&p["remain_time"]);
    let update = string(&p["update_time"]);
    let predictions: Vec<_> = near
        .iter()
        .filter(|row| truth(&p["pred_data"]) && truth(&row["pred"]))
        .collect();
    let team_h = if team.is_empty() { 0 } else { 50 };
    let pred_h = if predictions.is_empty() {
        0
    } else {
        42 + 38 * predictions.len() as i32
    };
    let h = 154
        + team_h
        + 38 * near.len() as i32
        + pred_h
        + 46 * i32::from(!remain.is_empty())
        + 38 * i32::from(!update.is_empty());
    let mut c = Canvas::new(gfx, 620, h);
    c.title(
        &format!("{} - {}", string(&p["name"]), masked(&p["uid"])),
        "SK",
        22,
        58,
        24.0,
        14.0,
    );
    let mut y = 84;
    let body = Font(MEDIUM, 18.0);
    if let Some(name) = team.first() {
        let tag = team.get(1).map(string).unwrap_or_default();
        let name = if tag.is_empty() {
            string(name).to_owned()
        } else {
            format!("{}({tag})", string(name))
        };
        c.panel((22, y, 598, y + 46), 18, (WHITE, 210), Some((WHITE, 220)));
        let mut x = 36;
        if let Some(icon) = team_image {
            c.icon(&resize_lanczos(icon, 40, 40), (32, y + 3));
            x = 84;
        }
        c.text((x, y + 25), &name, body, TEXT, Lm);
        y += 50;
    }
    c.panel((22, y, 598, y + 50), 18, (HEAD, 226), Some((WHITE, 210)));
    c.text(
        (40, y + 25),
        &format!(
            "分数 {:.1}W，排名 {}",
            decimal(&p["score"]) / 10000.0,
            number(&p["rank"])
        ),
        Font(BOLD, 30.0),
        WHITE,
        Lm,
    );
    y += 58;
    for (i, r) in near.iter().enumerate() {
        c.rect((22, y, 598, y + 34), 15, stripe(i), Some(WHITE));
        c.text(
            (38, y + 17),
            &format!(
                "T{}  {:.1}W  {}{:.1}W",
                number(&r["rank"]),
                decimal(&r["score"]) / 10000.0,
                string(&r["tag"]),
                decimal(&r["deviation"])
            ),
            body,
            TEXT,
            Lm,
        );
        y += 38;
    }
    let small = Font(MEDIUM, 14.0);
    if !predictions.is_empty() {
        y += 8;
        c.chip(
            (22, y, 118, y + 28),
            "预测线",
            Font(BOLD, 18.0),
            CHIP,
            ACCENT,
            Mm,
        );
        y += 36;
        for r in predictions {
            c.rect((22, y, 598, y + 34), 15, CHIP, Some(CHIP_BORDER));
            c.text(
                (38, y + 17),
                &format!(
                    "T{}  预测 {:.1}W",
                    number(&r["rank"]),
                    decimal(&r["pred"]) / 10000.0
                ),
                body,
                [180, 80, 135],
                Lm,
            );
            y += 38;
        }
        c.text((598, y - 4), "预测线来自33（3-3.dev）", small, MUTED, Rm);
    }
    if !remain.is_empty() {
        y += 8;
        c.chip(
            (22, y, 598, y + 30),
            &format!("活动还剩 {remain}"),
            body,
            WHITE,
            TEXT,
            Lm,
        );
        y += 38;
    }
    if !update.is_empty() {
        c.text(
            (22, y + 19),
            &format!("数据生成于 {update}"),
            small,
            MUTED,
            Lm,
        );
    }
    c.image
}

fn multi(gfx: &GfxContext, p: &Value) -> RgbaImage {
    let players = rows(&p["players"]);
    let body = Font(MEDIUM, 15.0);
    let header = cells(["玩家名", "分数", "排名"], Font(BOLD, 16.0), WHITE);
    let data: Vec<_> = players
        .iter()
        .map(|p| {
            cells(
                [
                    player_name(p),
                    format!("{:.1}W", decimal(&p["score"]) / 10000.0),
                    format!("T{}", number(&p["rank"])),
                ],
                body,
                TEXT,
            )
        })
        .collect();
    let sizing = cells(["", "9999.9W", "T99999"], body, TEXT);
    let mut widths = columns(gfx, [&header, &sizing], 3, 15);
    for row in &data {
        widths[0] = widths[0].max(width(gfx, &row[0].text, body) + 30);
    }
    let table_w: i32 = widths.iter().sum();
    let content = table_w.max(600);
    widths[0] += content - table_w;
    let mut c = Canvas::new(gfx, content + 36, 176 + 42 * data.len() as i32);
    c.title(
        &format!("查询结果（共 {} 人）", data.len()),
        "MULTI",
        18,
        56,
        20.0,
        12.0,
    );
    let mut y = 82;
    table_panel(&mut c, y, 140);
    let table = TableRow {
        widths: &widths,
        right_pad: None,
        dividers: true,
        radius: 14,
    };
    let right = c.width - 28;
    table.draw(&mut c, (28, y, right, y + 40), &header, HEAD, y + 20);
    y += 42;
    for (i, row) in data.iter().enumerate() {
        table.draw(&mut c, (28, y, right, y + 40), row, stripe(i), y + 20);
        y += 42;
    }
    if truth(&p["update_time"]) {
        c.chip(
            (28, y + 4, right, y + 32),
            &format!("数据生成于 {}", string(&p["update_time"])),
            Font(MEDIUM, 12.0),
            WHITE,
            MUTED,
            Lm,
        );
    }
    c.image
}

fn rank_table(gfx: &GfxContext, p: &Value, root: &Path, wl: bool) -> RgbaImage {
    let title = string(&p["title"]);
    let chapters = if wl { rows(&p["chapters"]) } else { &[] };
    let data = rows(&p[if wl { "rows" } else { "ranks_data" }]);
    let size = if wl { 16 } else { 18 };
    let value_header = fallback(&p["value_header"], "分数");
    let mut heads = if wl {
        vec!["排名".to_owned(), format!("总榜{value_header}")]
    } else {
        ["排名", "实时分数", fallback(&p["speed_header"], "时速")]
            .map(str::to_owned)
            .to_vec()
    };
    heads.extend(
        chapters
            .iter()
            .map(|ch| format!("第{}章", number(&ch["chapterNo"]))),
    );
    let header = cells(heads, Font(BOLD, size as f32), WHITE);
    let data: Vec<_> = data
        .iter()
        .map(|r| {
            let mut values = vec![format!("T{}", number(&r["rank"]))];
            if wl {
                let format = |v: &Value| {
                    if string(&p["value_mode"]) == "speed" {
                        speed(&v["speed"], string(&p["value_unit"]))
                    } else {
                        score(decimal(&v["score"]))
                    }
                };
                values.push(format(&r["total"]));
                let by_chapter = int_map(&r["chapters"]);
                values.extend(chapters.iter().map(|ch| {
                    format(
                        by_chapter
                            .get(&number(&ch["chapterNo"]))
                            .copied()
                            .unwrap_or(&Value::Null),
                    )
                }));
            } else {
                values.extend([
                    score(decimal(&r["score"])),
                    speed(&r["speed"], fallback(&p["speed_unit"], "万/h")),
                ]);
            }
            cells(values, Font(MEDIUM, size as f32), TEXT)
        })
        .collect();
    let mut widths = if wl {
        [82, 126]
            .into_iter()
            .chain(std::iter::repeat_n(104, chapters.len()))
            .collect()
    } else {
        columns(gfx, std::iter::once(&header).chain(&data), 3, 20)
    };
    let table_w: i32 = widths.iter().sum();
    let content = if wl {
        table_w.max(620)
    } else {
        table_w
            .max(520)
            .max(width(gfx, title, Font(BOLD, 22.0)) + 40)
    };
    let (header_h, row_h) = if wl { (52, 38) } else { (44, 40) };
    let mut c = Canvas::new(
        gfx,
        content + 36,
        144 + header_h + row_h * data.len() as i32,
    );
    let ago = number(&p["update_minutes_ago"]);
    let small = Font(MEDIUM, 12.0);
    if wl {
        c.title(title, "WL总榜+单榜", 18, 58, 22.0, 12.0);
    } else {
        let right = c.width - 18;
        c.panel((18, 18, right, 76), 22, (WHITE, 222), Some((WHITE, 220)));
        c.rect((36, 34, 98, 40), 3, PINK, None);
        c.text(
            (38, 55),
            &c.fit(title, Font(BOLD, 22.0), content - 190),
            Font(BOLD, 22.0),
            TEXT,
            Lm,
        );
        let update = if ago > 0 {
            format!("{ago} 分钟前")
        } else {
            "刚刚更新".into()
        };
        c.chip(
            (right - 132, 34, right - 14, 62),
            &update,
            small,
            CHIP,
            MUTED,
            Mm,
        );
    }
    let mut y = 84;
    table_panel(&mut c, y, if wl { 140 } else { 132 });
    let (left, right) = if wl {
        let left = 18 + (content - table_w) / 2;
        (left, left + table_w)
    } else {
        widths[2] += (content - table_w - 20).max(0);
        (28, c.width - 28)
    };
    let table = TableRow {
        widths: &widths,
        right_pad: None,
        dividers: !wl,
        radius: 15,
    };
    table.draw(
        &mut c,
        (left, y, right, y + header_h - 2),
        &header,
        HEAD,
        y + header_h / 2,
    );
    for (i, ch) in chapters.iter().enumerate() {
        if let Some(icon) = wl_icon(gfx, root, number(&ch["gameCharacterId"]), 34) {
            let x = left + 208 + 104 * i as i32;
            c.rect((x + 1, y + 5, x + 102, y + header_h - 4), 0, HEAD, None);
            c.icon(&icon, (x + 35, y + 6));
            c.text((x + 52, y + 43), &header[i + 2].text, small, WHITE, Mm);
        }
    }
    y += header_h + 8;
    for (i, row) in data.iter().enumerate() {
        table.draw(
            &mut c,
            (left, y, right, y + row_h - 2),
            row,
            stripe(i),
            y + row_h / 2,
        );
        y += row_h;
    }
    let footer = if ago > 0 {
        format!("数据更新于 {ago} 分钟前")
    } else {
        "数据刚刚更新".into()
    };
    c.text((c.width - 34, c.height - 33), &footer, small, MUTED, Rm);
    c.image
}

fn commas(value: i64) -> String {
    let value = value.to_string();
    let digits = value.trim_start_matches('-');
    let mut output = if value.starts_with('-') {
        "-".to_owned()
    } else {
        String::new()
    };
    for (i, ch) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i) % 3 == 0 {
            output.push(',');
        }
        output.push(ch);
    }
    output
}

fn cf(gfx: &GfxContext, p: &Value) -> RgbaImage {
    let title = format!("玩家 {}(id={})", string(&p["name"]), masked(&p["uid"]));
    let seconds = decimal(&p["stop_seconds"]);
    let status = if truth(&p["is_playing"]) {
        "周回中".into()
    } else if seconds != 0.0 {
        let seconds = seconds.floor() as i64;
        let hours = seconds.div_euclid(3600);
        let tail = format!(
            "{}分钟{}秒",
            seconds.rem_euclid(3600) / 60,
            seconds.rem_euclid(60)
        );
        if hours > 0 {
            format!("停车中（已停止 {hours}小时{tail}）")
        } else {
            format!("停车中（已停止 {tail}）")
        }
    } else {
        "停车中".into()
    };
    let mut data = vec![
        ("总榜分数".into(), commas(number(&p["score"]))),
        ("总榜排名".into(), format!("T{}", number(&p["rank"]))),
        (
            "总榜时速".into(),
            format!("{:.1}W/h", decimal(&p["hourly_speed"])),
        ),
        (
            "20*3时速".into(),
            format!("{:.1}W/h", decimal(&p["twenty_min_speed"])),
        ),
        ("周回数".into(), number(&p["play_count"]).to_string()),
        (
            "近10次平均Pt".into(),
            format!("{:.0}", decimal(&p["avg_pt"])),
        ),
        ("最近一次Pt".into(), number(&p["last_pt"]).to_string()),
        ("状态".into(), status),
    ];
    data.extend(rows(&p["wl_chapter_stats"]).iter().map(|stat| {
        let rank = number(&stat["rank"]);
        let value = if rank == 0 {
            "-".into()
        } else {
            format!(
                "T{rank} / {:.2}万 / {:.1}W/h",
                decimal(&stat["score"]) / 10000.0,
                decimal(&stat["hourly_speed"])
            )
        };
        (format!("第{}章", number(&stat["chapter_no"])), value)
    }));
    let header = Font(BOLD, 22.0);
    let key_font = Font(BOLD, 16.0);
    let mut w = 520.max(width(gfx, &title, header) + 40);
    for (key, value) in &data {
        w = w.max(width(gfx, key, key_font) + 24 + width(gfx, value, LABEL) + 40);
    }
    let mut c = Canvas::new(gfx, w, 100 + 38 * data.len() as i32);
    c.panel((10, 8, w - 10, 62), 22, (WHITE, 222), Some((WHITE, 220)));
    c.rect((20, 22, 82, 28), 3, PINK, None);
    c.text((20, 37), &c.fit(&title, header, w - 40), header, TEXT, Lm);
    for (i, (key, value)) in data.iter().enumerate() {
        let y = 74 + 38 * i as i32;
        c.rect((20, y, w - 20, y + 34), 15, stripe(i), Some(WHITE));
        c.text((34, y + 17), key, key_font, ACCENT, Lm);
        c.text(
            (20 + 132.max(width(gfx, key, key_font) + 28), y + 17),
            value,
            LABEL,
            TEXT,
            Lm,
        );
    }
    c.text((w - 20, c.height - 12), "ACTIVITY CHECK", SMALL, MUTED, Rm);
    c.image
}

fn cf_range(gfx: &GfxContext, p: &Value) -> RgbaImage {
    let header = cells(
        ["排名", "玩家名", "当前分", "时速", "周回", "状态"],
        Font(BOLD, 16.0),
        WHITE,
    );
    let body = Font(MEDIUM, 14.0);
    let data: Vec<_> = rows(&p["cf_data_list"])
        .iter()
        .map(|r| {
            let score = number(&r["score"]);
            cells(
                [
                    format!("T{}", number(&r["rank"])),
                    player_name(r),
                    if score >= 10000 {
                        format!("{:.1}W", score as f64 / 10000.0)
                    } else {
                        score.to_string()
                    },
                    format!("{:.1}W/h", decimal(&r["hourly_speed"])),
                    number(&r["play_count"]).to_string(),
                    if truth(&r["is_playing"]) {
                        "周回中".into()
                    } else {
                        "停车中".into()
                    },
                ],
                body,
                TEXT,
            )
        })
        .collect();
    let template = cells(["T100", "", "9999.9W", "999.9W/h", "999", ""], body, TEXT);
    let mut widths = columns(gfx, [&header, &template], 6, 15);
    for row in &data {
        for i in [1, 5] {
            widths[i] = widths[i].max(width(gfx, &row[i].text, body) + 30);
        }
    }
    let table_w: i32 = widths.iter().sum();
    let content = table_w.clamp(620, 1200);
    widths[1] += content - table_w;
    let w = content + 28;
    let h = 66 + 34 * data.len() as i32;
    let mut c = Canvas::new(gfx, w, h);
    c.panel(
        (14, 10, w - 14, h - 10),
        20,
        (WHITE, 150),
        Some((WHITE, 220)),
    );
    let table = TableRow {
        widths: &widths,
        right_pad: None,
        dividers: true,
        radius: 14,
    };
    table.draw(&mut c, (22, 14, w - 22, 50), &header, HEAD, 32);
    let x = 22 + widths[..5].iter().sum::<i32>();
    for (i, row) in data.iter().enumerate() {
        let y = 52 + 34 * i as i32;
        let mut row = row.clone();
        let status = std::mem::take(&mut row[5].text);
        row[1].text = c.fit(&row[1].text, body, widths[1] - 30);
        table.draw(&mut c, (22, y, w - 22, y + 32), &row, stripe(i), y + 16);
        c.rect((x + 4, y + 5, x + widths[5] - 4, y + 27), 10, WHITE, None);
        c.text((x + widths[5] / 2, y + 16), &status, body, TEXT, Mm);
    }
    c.image
}

fn csb(gfx: &GfxContext, p: &Value) -> RgbaImage {
    let counts: HashMap<_, _> = rows(&p["hourly_counts"])
        .iter()
        .filter_map(|r| {
            let [day, hour, count] = rows(r) else {
                return None;
            };
            Some(((number(day), number(hour)), number(count)))
        })
        .collect();
    let days = counts
        .keys()
        .map(|&(day, _)| day.saturating_add(1))
        .max()
        .unwrap_or(1)
        .clamp(0, 365) as i32;
    let periods: Vec<_> = rows(&p["stop_periods"])
        .iter()
        .filter_map(|r| {
            fn parse(value: &Value) -> Option<chrono::NaiveDateTime> {
                let text = string(value);
                chrono::DateTime::parse_from_rfc3339(text)
                    .ok()
                    .map(|dt| dt.naive_local())
                    .or_else(|| {
                        chrono::NaiveDateTime::parse_from_str(text, "%Y-%m-%dT%H:%M:%S%.f").ok()
                    })
                    .or_else(|| {
                        chrono::NaiveDateTime::parse_from_str(text, "%Y-%m-%d %H:%M:%S%.f").ok()
                    })
            }
            Some((
                parse(&r["start"])?,
                parse(&r["end"])?,
                number(&r["minutes"]),
            ))
        })
        .collect();
    let stop_rows = periods.len().div_ceil(2) as i32;
    let stop_h = if stop_rows > 0 {
        28 * stop_rows + 16
    } else {
        0
    };
    let title_font = Font(BOLD, 20.0);
    let head = Font(BOLD, 16.0);
    let body = Font(MEDIUM, 14.0);
    let small = Font(MEDIUM, 12.0);
    let col0 = width(gfx, "第99天", body).max(width(gfx, "时间", head)) + 24;
    let table_w = col0 + 24 * 36;
    let uid = uid(&p["latest_uid"]);
    let title = format!(
        "【查水表】{} (ID: ****{})",
        string(&p["latest_name"]),
        last_four(&uid)
    );
    let content = table_w.max(width(gfx, &title, title_font) + 24);
    let mut c = Canvas::new(gfx, content + 36, 172 + 32 * days + stop_h);
    c.title(
        &format!(
            "{title}  排名: T{} | 分数: {}",
            number(&p["latest_rank"]),
            number(&p["latest_score"])
        ),
        "CSB",
        18,
        58,
        20.0,
        12.0,
    );
    let mut y = 84;
    c.panel(
        (18, y - 4, 18 + table_w, y + 36 + 32 * days + 4),
        18,
        (WHITE, 150),
        Some((WHITE, 220)),
    );
    c.rect((26, y, 10 + table_w, y + 34), 14, HEAD, Some(WHITE));
    c.text((18 + col0 / 2, y + 18), "时间", head, WHITE, Mm);
    for hour in 0..24 {
        c.text(
            (36 + col0 + hour * 36, y + 18),
            &hour.to_string(),
            head,
            WHITE,
            Mm,
        );
    }
    y += 36;
    if !counts.is_empty() {
        for day in 0..days {
            c.rect(
                (26, y, 10 + table_w, y + 30),
                12,
                stripe(day as usize),
                Some(WHITE),
            );
            c.text(
                (18 + col0 / 2, y + 16),
                &format!("第{}天", day + 1),
                body,
                TEXT,
                Mm,
            );
            for hour in 0..24 {
                let count = counts
                    .get(&(i64::from(day), i64::from(hour)))
                    .copied()
                    .unwrap_or(0);
                c.text(
                    (36 + col0 + hour * 36, y + 16),
                    &count.to_string(),
                    body,
                    if count == 0 { MUTED } else { TEXT },
                    Mm,
                );
            }
            y += 32;
        }
    }
    if stop_rows > 0 {
        c.panel(
            (18, y + 4, 18 + content, y + 28 * stop_rows + 14),
            18,
            (WHITE, 160),
            Some((WHITE, 220)),
        );
        for (i, (start, end, minutes)) in periods.iter().enumerate() {
            let x = 18 + (i % 2) as i32 * (content / 2);
            let y = y + 8 + (i / 2) as i32 * 28;
            let text = format!(
                "停车: {} ~ {} ({minutes}分钟)",
                start.format("%m-%d %H:%M"),
                end.format("%H:%M")
            );
            c.rect(
                (x + 8, y + 2, x + content / 2 - 8, y + 26),
                12,
                CHIP,
                Some(BORDER),
            );
            c.text(
                (x + 20, y + 14),
                &c.fit(&text, body, content / 2 - 40),
                body,
                TEXT,
                Lm,
            );
        }
    }
    let h = c.height;
    c.chip(
        (18, h - 48, 18 + content, h - 20),
        &format!("数据更新时间: {}", string(&p["update_time_str"])),
        small,
        WHITE,
        MUTED,
        Lm,
    );
    c.image
}

const LIVE_RANKS: [i64; 16] = [
    10, 20, 30, 40, 50, 100, 200, 300, 400, 500, 1000, 2000, 3000, 4000, 5000, 10000,
];

fn source_name(source: &str) -> &str {
    match source {
        "local" => "本地预测",
        "33kit" => "33Kit预测",
        "moe" => "Moesekai预测",
        "sekarun" => "SekaRun预测",
        _ => source,
    }
}

pub(super) fn now() -> i64 {
    chrono::Utc::now().timestamp()
}

pub(super) fn event_title(p: &Value, id: &str, suffix: &str) -> String {
    format!(
        "【{}-{}】{}  {suffix}",
        string(&p["region"]).to_uppercase(),
        number(&p[id]),
        string(&p["event_name"])
    )
}

fn freshness(ts: i64, now: i64) -> (String, bool) {
    let secs = now.saturating_sub(ts);
    let text = match secs {
        ..60 => "刚刚".into(),
        60..3600 => format!("{}分钟前", secs / 60),
        3600..86400 => format!("{}小时前", secs / 3600),
        _ => format!("{}天前", secs / 86400),
    };
    let expired = secs > 10800;
    (if expired { format!("{text} ⚠") } else { text }, expired)
}

fn source_list<'a>(names: impl IntoIterator<Item = &'a str>) -> String {
    let mut seen = HashSet::new();
    let names: Vec<_> = names.into_iter().filter(|s| seen.insert(*s)).collect();
    if names.is_empty() {
        "无".into()
    } else {
        names.join(" / ")
    }
}

fn rank_data(fc: &Value, rank: i64) -> &Value {
    &fc["rank_data"][rank.to_string()]
}

fn forecast(gfx: &GfxContext, p: &Value, root: &Path, wl: bool) -> RgbaImage {
    let mut ranks: Vec<_> = rows(&p["display_ranks"]).iter().map(number).collect();
    if !wl {
        if p["display_ranks"].is_null() {
            ranks = LIVE_RANKS.to_vec();
        } else {
            ranks.retain(|r| *r > 0);
            ranks.sort_unstable();
            ranks.dedup();
        }
    }
    let mut chapters: Vec<_> = if wl {
        rows(&p["chapters"]).iter().collect()
    } else {
        vec![]
    };
    chapters.sort_by_key(|ch| number(&ch["chapterNo"]));
    let chapter_fc = int_map(&p["chapter_forecasts"]);
    let total = rows(&p[if wl { "total_forecasts" } else { "forecasts" }]);
    let forecasts: Vec<_> = total
        .iter()
        .chain(chapters.iter().map(|ch| {
            chapter_fc
                .get(&number(&ch["chapterNo"]))
                .copied()
                .unwrap_or(&Value::Null)
        }))
        .collect();
    let names: Vec<_> = total
        .iter()
        .map(|fc| source_name(string(&fc["source"])))
        .collect();
    let live_scores = int_map(&p["live_scores"]);
    let live_speeds = int_map(&p["live_speeds"]);
    let (size, title_h, header_h, pad, minimum, small) = if wl {
        (17.0, 60, 50, 18, 700, SMALL)
    } else {
        (18.0, 58, 44, 20, 620, Font(MEDIUM, 14.0))
    };
    let head = Font(BOLD, size);
    let body = Font(MEDIUM, size);
    let mut header = cells(
        ["排名", "当前分数", "时速"]
            .into_iter()
            .chain(names.iter().copied()),
        head,
        WHITE,
    );
    header.extend(cells(
        chapters
            .iter()
            .map(|ch| format!("第{}章", number(&ch["chapterNo"]))),
        head,
        WHITE,
    ));
    let data: Vec<_> = ranks
        .iter()
        .map(|&rank| {
            let mut row = cells(
                [
                    format!("T{rank}"),
                    score(live_scores.get(&rank).map_or(0.0, |v| decimal(v))),
                    speed(
                        live_speeds.get(&rank).copied().unwrap_or(&Value::Null),
                        "万/h",
                    ),
                ],
                body,
                TEXT,
            );
            row.extend(cells(
                forecasts
                    .iter()
                    .map(|fc| score(decimal(&rank_data(fc, rank)["final_score"]))),
                body,
                TEXT,
            ));
            row
        })
        .collect();
    let mut time = vec![
        Cell::new("预测时间", head, WHITE),
        Cell::new("-", body, WHITE),
        Cell::new("-", body, WHITE),
    ];
    let instant = now();
    for fc in &forecasts {
        let ts = number(&fc["forecast_ts"]);
        let (text, expired) = if ts == 0 {
            ("-".into(), false)
        } else {
            freshness(ts, instant)
        };
        time.push(Cell::new(text, body, if expired { WARN } else { WHITE }));
    }
    let mut widths = columns(
        gfx,
        std::iter::once(&header)
            .chain(&data)
            .chain((!wl).then_some(&time)),
        header.len(),
        pad,
    );
    let table_w: i32 = widths.iter().sum();
    let tag = if wl { "WL榜线预测" } else { "榜线预测" };
    let title = event_title(p, if wl { "base_event_id" } else { "event_id" }, tag);
    let content = table_w
        .max(minimum)
        .max(width(gfx, &title, Font(BOLD, 22.0)) + pad * 2);
    *widths.last_mut().unwrap() += content - table_w;
    let mut c = Canvas::new(
        gfx,
        content + 36,
        96 + title_h + 2 * header_h + 40 * data.len() as i32,
    );
    c.title(&title, tag, 18, title_h, 22.0, small.1);
    let start = 26 + title_h;
    let mut y = start;
    table_panel(&mut c, y, 140);
    let right = c.width - 28;
    let table = TableRow {
        widths: &widths,
        right_pad: None,
        dividers: true,
        radius: 14,
    };
    table.draw(
        &mut c,
        (28, y, right, y + header_h - 2),
        &header,
        HEAD,
        y + (header_h - 2) / 2,
    );
    let sep = 28
        + widths[..3 + if wl { total.len() } else { 0 }]
            .iter()
            .sum::<i32>();
    if !chapters.is_empty() {
        c.rect((sep, y, right, y + header_h - 2), 14, [200, 140, 200], None);
        let mut x = sep;
        for (i, ch) in chapters.iter().enumerate() {
            let col = 3 + total.len() + i;
            let mid = x + widths[col] / 2;
            if let Some(icon) = wl_icon(gfx, root, number(&ch["gameCharacterId"]), 32) {
                c.icon(&icon, (mid - 16, y + 3));
                c.text((mid, y + 40), &header[col].text, small, WHITE, Mm);
            } else {
                c.text((mid, y + 25), &header[col].text, head, WHITE, Mm);
            }
            x += widths[col];
        }
    }
    y += header_h;
    let table = TableRow {
        right_pad: Some(pad),
        ..table
    };
    for (i, row) in data.iter().enumerate() {
        table.draw(&mut c, (28, y, right, y + 38), row, stripe(i), y + 19);
        y += 40;
    }
    table.draw(
        &mut c,
        (28, y, right, y + header_h - 2),
        &time,
        HEAD,
        y + (header_h - 2) / 2,
    );
    y += header_h + 8;
    let footer = if wl {
        format!(
            "总榜预测源：{}；单榜仅本地预测；实线来自本地排名记录，请谨慎参考",
            source_list(names)
        )
    } else {
        "预测来自本地缓存/多源接口，实时分数来自本地排名记录，请谨慎参考".into()
    };
    c.chip(
        (28, y + 2, right, y + 34),
        &c.fit(&footer, small, content - 60),
        small,
        WHITE,
        MUTED,
        Lm,
    );
    if !wl || (!total.is_empty() && !chapters.is_empty()) {
        c.rect(
            (sep - 1, start + 8, sep + 2, y - 8),
            1,
            if wl { [180, 100, 160] } else { [205, 135, 165] },
            None,
        );
    }
    c.image
}

pub(super) const CURVE_COLORS: [Color; 8] = [
    [210, 45, 95],
    [35, 115, 210],
    [45, 150, 85],
    [220, 125, 35],
    [120, 75, 200],
    [30, 160, 170],
    [200, 70, 175],
    [95, 95, 95],
];
const FORECAST_COLORS: [Color; 8] = [
    [130, 35, 210],
    [0, 180, 210],
    [245, 70, 45],
    [95, 190, 35],
    [255, 80, 170],
    [155, 95, 25],
    [40, 200, 145],
    [35, 35, 35],
];
const AXIS: Color = [100, 80, 95];
const DETAIL: Color = [100, 75, 90];

pub(super) fn time_range(p: &Value) -> (i64, i64) {
    (number(&p["time_range"][0]), number(&p["time_range"][1]))
}

pub(super) struct Plot<'a> {
    pub c: Canvas<'a>,
    pub left: i32,
    pub right: i32,
    pub bottom: i32,
    pub start: i64,
    pub end: i64,
    pub labels: Vec<Rect>,
}

impl<'a> Plot<'a> {
    pub const TOP: i32 = 80;

    pub fn new(gfx: &'a GfxContext, height: i32, left: i32, (start, end): (i64, i64)) -> Self {
        let end = if end > start {
            end
        } else {
            start.saturating_add(3600)
        };
        Self {
            c: Canvas::new(gfx, 1180, height),
            left,
            right: 850,
            bottom: height - 105,
            start,
            end,
            labels: vec![],
        }
    }

    pub fn x(&self, ts: i64) -> i32 {
        (f64::from(self.left)
            + (ts.saturating_sub(self.start) as f64
                / self.end.saturating_sub(self.start).max(1) as f64)
                .clamp(0.0, 1.0)
                * f64::from(self.right - self.left)) as i32
    }

    pub fn y(&self, ratio: f64) -> i32 {
        (f64::from(Self::TOP) + ratio.clamp(0.0, 1.0) * f64::from(self.bottom - Self::TOP)) as i32
    }

    pub fn frame(&mut self, now: i64, remain: &str, axis: &str, tick: impl Fn(i32) -> String) {
        let (l, t, r, b) = (self.left, Self::TOP, self.right, self.bottom);
        self.c.panel(
            (l - 12, t - 12, r + 12, b + 12),
            24,
            (WHITE, 222),
            Some((WHITE, 230)),
        );
        self.c.rect((l, t, r, b), 18, WHITE, Some(BORDER));
        let mut seen = HashSet::new();
        for i in 0..=5 {
            let y = t + (b - t) * i / 5;
            self.c.line((l, y), (r, y), [240, 220, 230], 1.0);
            let text = tick(i);
            if seen.insert(text.clone()) {
                self.c.text((l - 10, y), &text, SMALL, AXIS, Rm);
            }
        }
        let days = self.end.saturating_sub(self.start) / 86400;
        let step = (days / 365).max(1) as usize;
        for day in (0..=days).step_by(step) {
            let x = self.x(self.start.saturating_add(day * 86400));
            self.x_tick(
                x,
                &if day == 0 {
                    "0天".into()
                } else {
                    format!("第{day}天")
                },
            );
        }
        if self.end.saturating_sub(self.start) % 86400 != 0 {
            self.x_tick(r, "结束");
        }
        let x = self.x(now);
        self.c.line((x, t), (x, b), [170, 110, 140], 2.0);
        self.c
            .text((x + 4, t + 8), "当前", SMALL, [150, 80, 115], La);
        self.c.text(
            ((l + r) / 2, self.c.height - 45),
            &format!("活动经过时间（剩余 {remain}）"),
            LABEL,
            [80, 60, 75],
            Mm,
        );
        self.c.text(
            (if l == 90 { 28 } else { 30 }, (t + b) / 2),
            axis,
            LABEL,
            [80, 60, 75],
            Mm,
        );
    }

    fn x_tick(&mut self, x: i32, text: &str) {
        self.c
            .line((x, Self::TOP), (x, self.bottom), [245, 230, 238], 1.0);
        self.c.text((x, self.bottom + 18), text, SMALL, AXIS, Mm);
    }

    pub fn points(&self, samples: &[Sample], ratio: impl Fn(i64) -> f64) -> Vec<Point> {
        samples
            .iter()
            .filter(|&&(ts, _)| (self.start..=self.end).contains(&ts))
            .map(|&(ts, value)| (self.x(ts), self.y(ratio(value))))
            .collect()
    }

    pub fn series(&mut self, points: &[Point], color: Color) {
        let [first, rest @ ..] = points else { return };
        let mut path = tiny_skia::PathBuilder::new();
        path.move_to(first.0 as f32 + 0.5, first.1 as f32 + 0.5);
        for &(x, y) in rest {
            path.line_to(x as f32 + 0.5, y as f32 + 0.5);
        }
        if let Some(path) = path.finish() {
            let size = self.c.image.dimensions();
            let Some(mut layer) = tiny_skia::Pixmap::new(size.0, size.1) else {
                return;
            };
            let mut paint = tiny_skia::Paint::default();
            paint.set_color_rgba8(color[0], color[1], color[2], 255);
            let stroke = tiny_skia::Stroke {
                width: 3.0,
                line_join: tiny_skia::LineJoin::Round,
                ..Default::default()
            };
            layer.stroke_path(
                &path,
                &paint,
                &stroke,
                tiny_skia::Transform::identity(),
                None,
            );
            for (dst, src) in self.c.image.pixels_mut().zip(layer.pixels()) {
                let a = u32::from(src.alpha());
                if a > 0 {
                    for (channel, source) in
                        [src.red(), src.green(), src.blue()].into_iter().enumerate()
                    {
                        dst[channel] = (u32::from(source)
                            + (u32::from(dst[channel]) * (255 - a) + 127) / 255)
                            as u8;
                    }
                }
            }
        }
    }

    pub fn dot(&mut self, (x, y): Point, color: Color) {
        self.c.rect((x - 4, y - 4, x + 4, y + 4), 5, color, None);
    }

    pub fn endpoint(&mut self, (x, y): Point, text: &str, color: Color, avoid_overlap: bool) {
        let (l, r, t, b) = (
            self.left + 2,
            self.right - 4,
            Self::TOP + 2,
            self.bottom - 18,
        );
        let text = self.c.fit(text, SMALL, r - l - 10);
        let w = width(self.c.gfx, &text, SMALL);
        let tx = if x + 6 + w + 5 > r { x - w - 10 } else { x + 6 };
        let tx = tx.clamp(l, r - w - 5);
        let ty = (y - 11).clamp(t, b);
        let bounds = |y| (tx - 3, y - 1, tx + w + 5, y + 16);
        let free = |y| {
            let (l, t, r, b) = bounds(y);
            self.labels
                .iter()
                .all(|&(x, y, xx, yy)| r <= x || xx <= l || b <= y || yy <= t)
        };
        let ty = if avoid_overlap && !free(ty) {
            (1..14)
                .flat_map(|step| [ty + step * 19, ty - step * 19])
                .find(|&y| (t..=b).contains(&y) && free(y))
                .unwrap_or(ty)
        } else {
            ty
        };
        self.labels.push(bounds(ty));
        self.c.rect(bounds(ty), 4, WHITE, Some(color));
        self.c.text((tx, ty + 7), &text, SMALL, color, Lm);
    }

    pub fn sidebar(&mut self) -> (i32, i32, i32) {
        let (x, y, right) = (self.right + 24, Self::TOP, self.c.width - 24);
        self.c.panel(
            (x - 12, y - 12, right, self.bottom + 12),
            22,
            (WHITE, 205),
            Some((WHITE, 230)),
        );
        (x, y, right - x)
    }

    pub fn footer(&mut self, text: &str) {
        let (w, h) = (self.c.width, self.c.height);
        self.c.chip(
            (24, h - 34, w - 24, h - 8),
            &self.c.fit(text, SMALL, w - 72),
            SMALL,
            WHITE,
            MUTED,
            Lm,
        );
    }
}

fn dash_style(source: &str, fallback: (i32, i32)) -> (i32, i32) {
    match source {
        "local" => (3, 9),
        "33kit" => (4, 8),
        "moe" => (6, 10),
        "sekarun" => (2, 8),
        _ => fallback,
    }
}

fn dashed(
    c: &mut Canvas,
    (x, y): Point,
    (xx, yy): Point,
    color: Color,
    (dash, gap): (i32, i32),
    width: f32,
) {
    let (dx, dy) = (f64::from(xx - x), f64::from(yy - y));
    let dist = dx.hypot(dy).max(1.0);
    let steps = (2 * (dist / f64::from(dash + gap)) as i32).max(1);
    for i in 0..steps {
        let a = f64::from(i) / f64::from(steps);
        let b = (a + f64::from(dash) / dist).min(1.0);
        draw_line(
            &mut c.image,
            (
                x as f32 + (dx * a) as f32 + 0.5,
                y as f32 + (dy * a) as f32 + 0.5,
            ),
            (
                x as f32 + (dx * b) as f32 + 0.5,
                y as f32 + (dy * b) as f32 + 0.5,
            ),
            rgba(color, 255),
            width,
        );
    }
}

pub(super) fn hourly_speed((first_ts, first): Sample, (last_ts, last): Sample) -> Option<f64> {
    (last_ts > first_ts).then(|| {
        (last as f64 - first as f64) * 3600.0 / (last_ts as f64 - first_ts as f64) / 10000.0
    })
}

struct Prediction<'a> {
    source: &'a str,
    time: i64,
    final_score: i64,
}

fn forecast_curve(gfx: &GfxContext, p: &Value) -> RgbaImage {
    let history: BTreeMap<i64, Vec<Sample>> = int_map(&p["history"])
        .into_iter()
        .map(|(rank, points)| {
            (
                rank,
                rows(points)
                    .iter()
                    .filter_map(|point| {
                        let [ts, score] = rows(point) else {
                            return None;
                        };
                        Some((number(ts), number(score)))
                    })
                    .collect(),
            )
        })
        .collect();
    let ranks: Vec<_> = rows(&p["ranks"]).iter().map(number).collect();
    let forecasts = rows(&p["forecasts"]);
    let (mut start, mut end) = time_range(p);
    if start == 0 {
        let ts = history.values().flatten().map(|(ts, _)| *ts);
        let now = now();
        start = ts.clone().min().unwrap_or(now - 3600);
        end = ts.max().unwrap_or(now);
    }
    let mut plot = Plot::new(gfx, 700, 90, (start, end));
    let end = plot.end;
    let now = history
        .values()
        .filter_map(|points| points.last().map(|point| point.0))
        .max()
        .unwrap_or(start)
        .clamp(start, end);
    let remain = fallback(&p["remain_text"], "未知");
    let mut series = HashMap::new();
    let mut latest: HashMap<i64, Vec<Prediction>> = HashMap::new();
    for fc in forecasts {
        let source = string(&fc["source"]);
        let from = if source == "local" { now } else { start };
        let time = number(&fc["forecast_ts"]);
        for &rank in &ranks {
            let rd = rank_data(fc, rank);
            let mut points = rows(&rd["future_rankings"]);
            if points.is_empty() {
                points = rows(&rd["history_final_score"]);
            }
            if !points.is_empty() {
                let mut samples: Vec<_> = points
                    .iter()
                    .map(|v| (number(&v["ts"]), number(&v["score"])))
                    .filter(|(ts, _)| (from..=end).contains(ts))
                    .collect();
                samples.sort_unstable();
                samples.dedup();
                series.insert((rank, source), samples);
            }
            let final_score = number(&rd["final_score"]);
            if final_score != 0 {
                latest.entry(rank).or_default().push(Prediction {
                    source,
                    time: if time == 0 { end } else { time },
                    final_score,
                });
            }
        }
    }
    let scores = history
        .values()
        .chain(series.values())
        .flatten()
        .map(|&(_, score)| score)
        .chain(latest.values().flatten().map(|pred| pred.final_score));
    let lo = (scores.clone().min().unwrap_or(0) as f64 * 0.95).max(0.0);
    let hi = scores.max().unwrap_or(10000) as f64 * 1.05;
    let hi = if hi > lo { hi } else { lo + 10000.0 };
    let ratio = |score: i64| (hi - score as f64) / (hi - lo);
    plot.c.title(
        &event_title(p, "event_id", "ycx曲线"),
        &format!("剩余 {remain}"),
        18,
        54,
        24.0,
        13.0,
    );
    plot.frame(now, remain, "活动分数", |i| {
        format!("{:.0}万", (hi - (hi - lo) * f64::from(i) / 5.0) / 10000.0)
    });
    for (i, &rank) in ranks.iter().enumerate() {
        let real = CURVE_COLORS[i % 8];
        let predicted = FORECAST_COLORS[i % 8];
        if let Some(points) = history.get(&rank) {
            plot.series(&plot.points(points, ratio), real);
            if let Some(&(ts, score)) = points.last() {
                let point = (plot.x(ts), plot.y(ratio(score)));
                plot.dot(point, real);
                plot.endpoint(
                    point,
                    &format!("T{rank} {:.1}万", score as f64 / 10000.0),
                    real,
                    false,
                );
            }
        }
        for (idx, fc) in forecasts.iter().enumerate() {
            let source = string(&fc["source"]);
            let Some(samples) = series.get(&(rank, source)) else {
                continue;
            };
            let points = plot.points(samples, ratio);
            if let Some((&(x, y), &(_, score))) = points.last().zip(samples.last()) {
                if points.len() == 1 {
                    plot.c
                        .rect((x - 4, y - 4, x + 4, y + 4), 0, predicted, None);
                }
                for pair in points.windows(2) {
                    dashed(
                        &mut plot.c,
                        pair[0],
                        pair[1],
                        predicted,
                        dash_style(source, (3 + idx as i32, 9)),
                        3.0,
                    );
                }
                plot.endpoint(
                    (x, y),
                    &format!("T{rank}预 {:.1}万", score as f64 / 10000.0),
                    predicted,
                    false,
                );
            }
        }
        for pred in latest.get(&rank).into_iter().flatten() {
            if series.contains_key(&(rank, pred.source)) {
                continue;
            }
            let (x, y) = (plot.x(pred.time), plot.y(ratio(pred.final_score)));
            plot.c
                .rect((x - 4, y - 4, x + 4, y + 4), 0, predicted, None);
            plot.c.rect((x - 2, y - 2, x + 2, y + 2), 0, WHITE, None);
            plot.endpoint(
                (x, y),
                &format!("T{rank}预 {:.1}万", pred.final_score as f64 / 10000.0),
                predicted,
                false,
            );
        }
    }
    let (x, mut y, w) = plot.sidebar();
    let c = &mut plot.c;
    c.chip((x, y - 3, x + 76, y + 23), "图例", LABEL, CHIP, SIDE, Mm);
    y += 30;
    for (i, rank) in ranks.iter().enumerate() {
        c.line((x, y + 5), (x + 42, y + 5), CURVE_COLORS[i % 8], 4.0);
        dashed(
            c,
            (x, y + 13),
            (x + 42, y + 13),
            FORECAST_COLORS[i % 8],
            (3, 9),
            3.0,
        );
        c.text(
            (x + 52, y + 9),
            &c.fit(&format!("T{rank} 上实线=真实 / 下虚线=预测"), SMALL, w - 52),
            SMALL,
            SIDE,
            Lm,
        );
        y += 24;
    }
    y += 8;
    c.text((x, y), "预测源线型", LABEL, SIDE, La);
    y += 24;
    let mut seen = HashSet::new();
    for (i, source) in forecasts
        .iter()
        .map(|fc| string(&fc["source"]))
        .filter(|s| seen.insert(*s))
        .enumerate()
    {
        let style = dash_style(source, (8 + i as i32 * 2, 6));
        dashed(
            c,
            (x, y + 8),
            (x + 42, y + 8),
            [80; 3],
            style,
            if matches!(source, "local" | "33kit" | "moe" | "sekarun") {
                3.0
            } else {
                2.0
            },
        );
        c.text(
            (x + 52, y + 8),
            &c.fit(source_name(source), SMALL, w - 52),
            SMALL,
            SIDE,
            Lm,
        );
        y += 18;
        if y > plot.bottom - 230 {
            c.text((x, y + 8), "…", SMALL, DETAIL, La);
            y += 18;
            break;
        }
    }
    y += 12;
    c.text(
        (x, y),
        &c.fit(&format!("剩余 {remain}"), LABEL, w),
        LABEL,
        [120, 70, 95],
        La,
    );
    y += 28;
    c.text((x, y), "当前状态", LABEL, SIDE, La);
    y += 24;
    for rank in ranks.iter().take(8) {
        if y > c.height - 90 {
            break;
        }
        let points = history.get(rank).map_or(&[][..], Vec::as_slice);
        let mut score = "-".to_owned();
        let mut speed = "-".to_owned();
        if let Some(&last) = points.last() {
            if last.1 != 0 {
                score = format!("{:.1}万", last.1 as f64 / 10000.0);
            }
            if let Some(speed_value) = points
                .iter()
                .find(|point| point.0 >= last.0.saturating_sub(3600))
                .and_then(|&first| hourly_speed(first, last))
            {
                speed = format!("{speed_value:.1}万/h");
            }
        }
        c.text(
            (x, y),
            &c.fit(&format!("T{rank} {score}  时速{speed}"), SMALL, w),
            SMALL,
            SIDE,
            La,
        );
        y += 18;
        if let Some(preds) = latest.get(rank) {
            for pred in preds.iter().take(4) {
                if y > c.height - 64 {
                    break;
                }
                let line = format!(
                    "- {}: {:.1}万",
                    source_name(pred.source),
                    pred.final_score as f64 / 10000.0
                );
                c.text((x + 12, y), &c.fit(&line, SMALL, w - 12), SMALL, DETAIL, La);
                y += 16;
            }
            if preds.len() > 4 && y <= c.height - 64 {
                c.text(
                    (x + 12, y),
                    &format!("…另 {} 个来源", preds.len() - 4),
                    SMALL,
                    DETAIL,
                    La,
                );
                y += 16;
            }
        } else {
            c.text((x + 12, y), "- 预测: -", SMALL, DETAIL, La);
            y += 16;
        }
        y += 6;
    }
    let names = source_list(
        forecasts
            .iter()
            .map(|fc| source_name(string(&fc["source"]))),
    );
    plot.footer(&format!(
        "预测源：{names}；实线为真实历史，高对比虚线为预测，本地预测仅显示当前之后"
    ));
    plot.c.image
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::renderers::common::RenderFixture;
    use image::GenericImageView;
    use serde_json::json;

    #[tokio::test]
    async fn cards_tables_and_activity_render_offline() {
        let fixture = RenderFixture::new();
        let ctx = fixture.context();
        fixture.png(
            &format!(
                "ondemand/{}/ondemand/event/test/team_image/team.png",
                ctx.app.server_name(0)
            ),
            (40, 40),
        );
        fixture.png("static/chara/chr_ts_90_1_2.png", (34, 34));
        let cases = [
            (
                Sk::Single,
                json!({"name":"Player","uid":123456,"score":100000,"rank":10,"event_asset":"test",
                "team_asset":"team","team_info":["Team","Tag"],"remain_time":"1天","update_time":"12:00",
                "near_ranks":[{"rank":10,"score":120000,"deviation":2,"tag":"差","pred":250000}],"pred_data":{"source":"33kit"}}),
                (620, 406),
            ),
            (
                Sk::Multi,
                json!({"players":[{"name":"Player","uid":"123456","score":100000,"rank":10}],"update_time":"12:00"}),
                (636, 218),
            ),
            (
                Sk::RankTable,
                json!({"title":"实时榜线","ranks_data":[{"rank":10,"score":120000,"speed":0},
                {"rank":20,"score":0,"speed":null}],"update_minutes_ago":2}),
                (556, 268),
            ),
            (
                Sk::WlRankTable,
                json!({"chapters":[{"chapterNo":1,"gameCharacterId":1}],"value_mode":"speed","value_unit":"万/h",
                "rows":[{"rank":10,"total":{"speed":12},"chapters":{"1":{"speed":0}}}]}),
                (656, 234),
            ),
            (
                Sk::Cf,
                json!({"uid":"123456","stop_seconds":3661,"wl_chapter_stats":[{"chapter_no":1,"rank":10,"score":120000,"hourly_speed":0}]}),
                (520, 442),
            ),
            (
                Sk::CfRange,
                json!({"cf_data_list":[{"name":"Player","uid":"123456","rank":10,"score":120000,"hourly_speed":12,"play_count":4,"is_playing":true}]}),
                (648, 100),
            ),
        ];
        for (renderer, payload, size) in cases {
            let image = jpeg(&renderer, &ctx, payload).await;
            assert_eq!(image.dimensions(), size, "{}", renderer.name());
            if matches!(renderer, Sk::Single) {
                let pixel = image.get_pixel(52, 107);
                assert!(pixel[2] > pixel[0] && pixel[0] > pixel[1]);
            }
        }
        let image = jpeg(&Sk::Csb,&ctx,json!({"latest_uid":123456,"hourly_counts":[[1,3,2]],
            "stop_periods":[{"start":"2025-02-11T03:00:00+08:00","end":"2025-02-11T03:14:00+08:00","minutes":14},{"start":"invalid"}]})).await;
        assert_eq!(image.height(), 280);
    }

    #[tokio::test]
    async fn forecast_tables_and_curve_render_offline() {
        let fixture = RenderFixture::new();
        let ctx = fixture.context();
        let prediction = json!({"source":"local","forecast_ts":1000,"rank_data":{"10":{
            "final_score":30000,"future_rankings":[{"ts":1600,"score":20000},{"ts":2200,"score":30000}]}}});
        let image = jpeg(
            &Sk::Forecast,
            &ctx,
            json!({"display_ranks":[20,10,10,0],
            "forecasts":[prediction],"live_speeds":{"10":0,"20":null}}),
        )
        .await;
        assert_eq!(image.dimensions(), (656, 322));
        let image = jpeg(&Sk::WlForecast,&ctx,json!({"display_ranks":[10],
            "chapters":[{"chapterNo":2},{"chapterNo":1}],"chapter_forecasts":{"1":prediction,"2":null}})).await;
        assert_eq!(image.dimensions(), (736, 296));
        let image = jpeg(
            &Sk::ForecastCurve,
            &ctx,
            json!({"time_range":[1000,2200],"ranks":[10],
            "history":{"10":[[1000,10000],[1300,15000]]},"forecasts":[prediction]}),
        )
        .await;
        assert_eq!(image.dimensions(), (1180, 700));
        let real = image.get_pixel(170, 534);
        assert!(i16::from(real[0]) - i16::from(real[1]) > 50);
        assert_eq!(freshness(1000, 11800), ("3小时前".into(), false));
        assert_eq!(freshness(1000, 11801), ("3小时前 ⚠".into(), true));
    }

    async fn jpeg(
        renderer: &Sk,
        ctx: &RenderRequestContext,
        payload: Value,
    ) -> image::DynamicImage {
        let output = renderer.render(ctx, payload).await.unwrap();
        assert_eq!(output.images.len(), 1);
        assert!(output.images[0].starts_with(b"\xff\xd8\xff"));
        image::load_from_memory(&output.images[0]).unwrap()
    }
}
