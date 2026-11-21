use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    path::Path,
};

use crate::core::{RenderOutput, RenderRequestContext, Renderer, Result};
use crate::gfx::{
    GfxContext, RenderContextGfxExt,
    TextAnchor::{La, Lm, Mm, Ra},
};
use async_trait::async_trait;
use image::RgbaImage;
use serde_json::Value;

use crate::renderers::{
    common::{number, render_jpeg, rows, string},
    sk::{
        CHIP, CURVE_COLORS, LABEL, Plot, SIDE, SMALL, Sample, WHITE, chara_colors, event_title,
        fallback, hourly_speed, int_map, now, time_range, truth, width, wl_icon,
    },
};

pub(super) enum MeCurve {
    Total,
    Wl,
}

#[async_trait]
impl Renderer for MeCurve {
    fn name(&self) -> &'static str {
        match self {
            Self::Total => "sk_me_curve_total",
            Self::Wl => "sk_me_curve_wl",
        }
    }

    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput> {
        let wl = matches!(self, Self::Wl);
        let colors = if wl {
            chara_colors(ctx, &payload)
        } else {
            HashMap::new()
        };
        let root = ctx.app.paths.static_dir.clone();
        render_jpeg(ctx.gfx()?, 88, true, move |gfx| {
            if wl {
                draw_wl(gfx, &payload, &root, &colors)
            } else {
                draw_total(gfx, &payload)
            }
        })
        .await
    }
}

const META: [u8; 3] = [120, 95, 115];
const DETAIL: [u8; 3] = [100, 75, 90];
const GRAY: [u8; 3] = [136; 3];

#[derive(Clone, Copy)]
struct Record {
    ts: i64,
    rank: Option<i64>,
    point: Option<i64>,
}

fn records(raw: &Value, wl: bool) -> Vec<Record> {
    let (rank, point) = if wl {
        ("wl_chapter_rank", "wl_chapter_point")
    } else {
        ("event_rank", "event_point")
    };
    rows(raw)
        .iter()
        .map(|r| Record {
            ts: number(&r["ts"]),
            rank: r[rank].as_i64(),
            point: r[point].as_i64(),
        })
        .collect()
}

fn rank_points(records: &[Record]) -> Vec<Sample> {
    records
        .iter()
        .filter_map(|r| Some((r.ts, r.rank.filter(|&rank| rank >= 1)?)))
        .collect()
}

#[derive(Default)]
struct Metrics {
    rank: Option<i64>,
    point: Option<i64>,
    speed: Option<f64>,
    rounds_1h: usize,
    rounds_total: usize,
    delta: Option<i64>,
}

fn metrics(records: &[Record], now: i64) -> Metrics {
    let mut m = Metrics {
        rounds_total: records.len(),
        ..Default::default()
    };
    let usable = records.iter().filter(|r| r.point.is_some());
    if let Some(last) = usable.clone().next_back() {
        m.point = last.point;
        m.rank = last.rank;
        m.rounds_1h = records.iter().filter(|r| r.ts >= now - 3600).count();
        let mut window = usable.filter(|r| r.ts >= last.ts.saturating_sub(3600));
        m.speed = window
            .clone()
            .next()
            .and_then(|first| hourly_speed((first.ts, first.point?), (last.ts, last.point?)));
        if let Some(first) = window.find_map(|r| r.rank)
            && let Some(last) = window.rev().find_map(|r| r.rank)
        {
            m.delta = Some(first.saturating_sub(last));
        }
    }
    m
}

fn rank(value: Option<i64>) -> String {
    value
        .filter(|&v| v != 0)
        .map_or_else(|| "-".into(), |v| format!("#{v}"))
}

fn point(value: Option<i64>) -> String {
    value.map_or_else(
        || "-".into(),
        |v| {
            if v >= 10000 {
                format!("{:.1}万", v as f64 / 10000.0)
            } else {
                v.to_string()
            }
        },
    )
}

fn speed(value: Option<f64>) -> String {
    value.map_or_else(|| "-".into(), |v| format!("{v:.1}万/h"))
}

fn rank_limits(points: impl Iterator<Item = Sample> + Clone) -> (f64, f64) {
    let Some(lo) = points.clone().map(|(_, r)| r).min() else {
        return (1.0, 100.0);
    };
    let hi = points.map(|(_, r)| r).max().unwrap_or(lo);
    let pad = (((hi - lo).max(1) as f64 * 0.08) as i64).max(1);
    let (mut lo, mut hi) = ((lo - pad).max(1) as f64, hi.saturating_add(pad) as f64);
    if hi - lo < 5.0 {
        lo = ((hi + lo) / 2.0 - 2.5).trunc().max(1.0);
        hi = lo + 5.0;
    }
    (lo, hi)
}

fn frame(plot: &mut Plot, p: &Value, wl: bool, now: i64, (lo, hi): (f64, f64)) {
    let (title, subtitle) = if wl {
        ("个人WL分榜曲线", "cnskme WL")
    } else {
        ("个人总榜曲线", "cnskme")
    };
    plot.c.title(
        &event_title(p, "event_id", title),
        subtitle,
        18,
        54,
        24.0,
        13.0,
    );
    plot.frame(now, fallback(&p["remain_text"], "未知"), "排名", |i| {
        format!(
            "#{}",
            (lo + (hi - lo) * f64::from(i) / 5.0).round_ties_even() as i64
        )
    });
}

fn draw_rank_series(
    plot: &mut Plot,
    samples: &[Sample],
    (lo, hi): (f64, f64),
    color: [u8; 3],
    label: String,
) -> Option<((i32, i32), String, [u8; 3])> {
    let points = plot.points(samples, |r| (r as f64 - lo) / (hi - lo));
    plot.series(&points, color);
    let &last = points.last()?;
    plot.dot(last, color);
    Some((last, label, color))
}

fn draw_total(gfx: &GfxContext, p: &Value) -> RgbaImage {
    let records = records(&p["records"], false);
    let points = rank_points(&records);
    let limits = rank_limits(points.iter().copied());
    let now = now();
    let mut plot = Plot::new(gfx, 700, 100, time_range(p));
    frame(&mut plot, p, false, now, limits);
    let color = CURVE_COLORS[0];
    if let Some(&(_, last_rank)) = points.last()
        && let Some((at, text, color)) =
            draw_rank_series(&mut plot, &points, limits, color, rank(Some(last_rank)))
    {
        plot.endpoint(at, &text, color, true);
    }
    let m = metrics(&records, now);
    let (x, mut y, w) = plot.sidebar();
    let c = &mut plot.c;
    c.chip(
        (x, y - 3, x + 96, y + 23),
        "当前状态",
        LABEL,
        CHIP,
        SIDE,
        Mm,
    );
    y += 34;
    let mut lines = vec![
        ("总榜排名", rank(m.rank)),
        ("活动分数", point(m.point)),
        ("时速", speed(m.speed)),
        ("近1h周回", format!("{} 次", m.rounds_1h)),
        ("累计周回", format!("{} 次", m.rounds_total)),
        ("剩余", fallback(&p["remain_text"], "未知").to_owned()),
    ];
    if let Some(delta) = m.delta {
        lines.insert(
            2,
            (
                "近1h排名",
                match delta {
                    1.. => format!("↑{delta}"),
                    0 => "持平".into(),
                    _ => format!("↓{}", delta.unsigned_abs()),
                },
            ),
        );
    }
    for (name, value) in lines {
        c.text((x, y), &c.fit(name, SMALL, w), SMALL, META, La);
        c.text((x + w, y), &c.fit(&value, LABEL, w - 70), LABEL, SIDE, Ra);
        y += 30;
    }
    if let Some((first, last)) = points.first().zip(points.last()) {
        let span = (last.0.saturating_sub(first.0) as f64 / 3600.0).max(0.0);
        c.text(
            (x, y + 6),
            &c.fit(&format!("记录跨度 {span:.1} 小时"), SMALL, w),
            SMALL,
            META,
            La,
        );
    }
    plot.footer(&format!(
        "共 {} 条记录；排名轴向上为更高排名",
        m.rounds_total
    ));
    plot.c.image
}

fn parse_color(text: &str) -> [u8; 3] {
    let text = text.trim_start_matches('#');
    if text.len() != 6 || !text.bytes().all(|c| c.is_ascii_hexdigit()) {
        return GRAY;
    }
    let value = u32::from_str_radix(text, 16).unwrap_or(0x888888);
    [(value >> 16) as u8, (value >> 8) as u8, value as u8]
}

fn darken(color: [u8; 3], factor: f64) -> [u8; 3] {
    color.map(|c| (f64::from(c) * factor) as u8)
}

#[derive(Default)]
struct Chapter<'a> {
    start: i64,
    end: i64,
    id: i64,
    active: bool,
    color: Option<&'a str>,
}

fn draw_bands(plot: &mut Plot, meta: &BTreeMap<i64, Chapter>, root: &Path) {
    let top = Plot::TOP;
    let mut edges = BTreeSet::new();
    let mut bands = Vec::new();
    for (&no, ch) in meta {
        if ch.start == 0 || ch.end == 0 {
            continue;
        }
        edges.extend([ch.start, ch.end]);
        let (left, right) = (plot.x(ch.start), plot.x(ch.end));
        if right <= left {
            continue;
        }
        let color = ch.color.map_or(
            CURVE_COLORS[(no.rem_euclid(8) as usize + 7) % 8],
            parse_color,
        );
        plot.c.panel(
            (left, top, right, plot.bottom),
            0,
            (color, if ch.active { 58 } else { 34 }),
            None,
        );
        bands.push((no, ch, left, right, darken(color, 0.55)));
    }
    for ts in edges.range((
        std::ops::Bound::Excluded(plot.start),
        std::ops::Bound::Excluded(plot.end),
    )) {
        let x = plot.x(*ts);
        for y in (top..plot.bottom).step_by(8) {
            plot.c
                .line((x, y), (x, (y + 4).min(plot.bottom)), [140, 120, 140], 2.0);
        }
    }
    for (no, ch, left, right, color) in bands {
        let mid = (left + right) / 2;
        let mut y = top + 14;
        if right - left >= 38
            && let Some(icon) = wl_icon(plot.c.gfx, root, ch.id, 30)
        {
            let bounds = (mid - 18, top + 3, mid + 18, top + 39);
            plot.c.rect(bounds, 18, WHITE, Some(color));
            plot.c.icon(&icon, (mid - 15, top + 6));
            plot.labels.push(bounds);
            y = top + 48;
        }
        let label = format!("第{no}章{}", if ch.active { "·进行中" } else { "" });
        let w = width(plot.c.gfx, &label, SMALL);
        if right - left >= w + 10 {
            let bounds = (mid - w / 2 - 5, y - 8, mid + w / 2 + 5, y + 9);
            plot.c.rect(bounds, 6, WHITE, Some(color));
            plot.c.text((mid, y), &label, SMALL, color, Mm);
            plot.labels.push(bounds);
        }
    }
}

fn draw_wl(gfx: &GfxContext, p: &Value, root: &Path, colors: &HashMap<i64, String>) -> RgbaImage {
    let chapters: Vec<_> = int_map(&p["chapter_records"])
        .into_iter()
        .map(|(no, raw)| {
            let records = records(raw, true);
            let points = rank_points(&records);
            (no, records, points)
        })
        .collect();
    let meta: BTreeMap<_, _> = int_map(&p["chapter_meta"])
        .into_iter()
        .map(|(no, m)| {
            let id = number(&m["gameCharacterId"]);
            let color = if let Some(color) = m.get("color") {
                string(color)
            } else {
                colors.get(&id).map_or("", String::as_str)
            };
            (
                no,
                Chapter {
                    id,
                    color: (!color.is_empty()).then_some(color),
                    active: truth(&m["active"]),
                    start: number(&m["start"]),
                    end: number(&m["end"]),
                },
            )
        })
        .collect();
    let limits = rank_limits(chapters.iter().flat_map(|(_, _, pts)| pts.iter().copied()));
    let now = now();
    let mut plot = Plot::new(gfx, 760, 100, time_range(p));
    frame(&mut plot, p, true, now, limits);
    draw_bands(&mut plot, &meta, root);
    let default = Chapter::default();
    let style = |i: usize, no| {
        let ch = meta.get(&no).unwrap_or(&default);
        let base = ch.color.map_or(CURVE_COLORS[i % 8], parse_color);
        (ch, base, darken(base, 0.62))
    };
    let mut endpoints = Vec::new();
    for (i, (no, _, points)) in chapters.iter().enumerate() {
        if let Some(&(_, rank)) = points.last() {
            let (_, _, color) = style(i, *no);
            endpoints.extend(draw_rank_series(
                &mut plot,
                points,
                limits,
                color,
                format!("第{no}章 #{rank}"),
            ));
        }
    }
    for (at, text, color) in endpoints {
        plot.endpoint(at, &text, color, true);
    }
    let (x, mut y, w) = plot.sidebar();
    let c = &mut plot.c;
    c.chip(
        (x, y - 3, x + 110, y + 23),
        "分榜状态",
        LABEL,
        CHIP,
        SIDE,
        Mm,
    );
    y += 32;
    for (i, (no, records, _)) in chapters.iter().enumerate() {
        if y > c.height - 110 {
            c.text((x, y), "…", SMALL, META, La);
            break;
        }
        let m = metrics(records, now);
        let (ch, base, color) = style(i, *no);
        c.panel(
            (x - 6, y - 5, x + w + 6, y + 85),
            10,
            (base, 40),
            Some((color, 90)),
        );
        c.line((x, y + 9), (x + 26, y + 9), color, 4.0);
        let mut head_x = x + 34;
        if let Some(icon) = wl_icon(gfx, root, ch.id, 22) {
            c.icon(&icon, (head_x, y - 2));
            head_x += 28;
        }
        let title = format!("第{no}章{}", if ch.active { "（进行中）" } else { "" });
        c.text(
            (head_x, y + 9),
            &c.fit(&title, LABEL, w - (head_x - x)),
            LABEL,
            darken(base, 0.5),
            Lm,
        );
        y += 26;
        for line in [
            format!("排名 {}   分数 {}", rank(m.rank), point(m.point)),
            format!("时速 {}   近1h周回 {}", speed(m.speed), m.rounds_1h),
            format!("本章周回 {} 次", m.rounds_total),
        ] {
            if y > c.height - 84 {
                break;
            }
            c.text((x + 12, y), &c.fit(&line, SMALL, w - 12), SMALL, DETAIL, La);
            y += 18;
        }
        y += 10;
    }
    let count: usize = chapters.iter().map(|(_, records, _)| records.len()).sum();
    plot.footer(&format!("WL 分榜记录共 {count} 条；底色区块与虚线为各角色章节赛程，曲线取角色印象色，排名轴向上为更高排名"));
    plot.c.image
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::renderers::common::RenderFixture;
    use image::GenericImageView;
    use serde_json::json;

    #[test]
    fn metrics_use_latest_scored_record_and_its_hour_window() {
        let records = records(
            &json!([
                {"ts":100,"event_rank":50,"event_point":5000},
                {"ts":3900,"event_rank":10,"event_point":10000},
                {"ts":4500,"event_rank":5,"event_point":20000},
                {"ts":4700,"event_rank":0},
                {"ts":4800,"event_rank":true}
            ]),
            false,
        );
        assert_eq!(rank_points(&records), [(100, 50), (3900, 10), (4500, 5)]);
        let m = metrics(&records, 5000);
        assert_eq!(
            (m.rank, m.point, m.speed, m.delta),
            (Some(5), Some(20000), Some(6.0), Some(5))
        );
        assert_eq!((m.rounds_1h, m.rounds_total), (4, 5));
    }

    #[tokio::test]
    async fn personal_curves_render_from_payloads_offline() {
        let fixture = RenderFixture::new();
        fixture.png("static/chara/chr_ts_90_1.png", (30, 30));
        let ctx = fixture.context();
        fixture.write(
            &format!("ondemand/{}/gameCharacterUnits.json", ctx.app.server_name(0)),
            r##"[{"gameCharacterId":1,"colorCode":"#00bb55"},{"gameCharacterId":1,"colorCode":"#cc4477"}]"##,
        );
        let cases = [
            (
                MeCurve::Total,
                json!({"time_range":[1000,2000],"records":[
                {"ts":1000,"event_rank":20,"event_point":10000},{"ts":2000,"event_rank":10,"event_point":20000}]}),
                700,
            ),
            (
                MeCurve::Wl,
                json!({"time_range":[1000,3000],"chapter_records":{"1":[
                {"ts":1100,"wl_chapter_rank":10,"wl_chapter_point":10000},
                {"ts":1800,"wl_chapter_rank":7,"wl_chapter_point":20000}]},
                "chapter_meta":{"1":{"start":1000,"end":2000,"active":true,"gameCharacterId":1},
                    "2":{"start":2000,"end":3000,"color":"#cc4477"}}}),
                760,
            ),
        ];
        for (renderer, payload, height) in cases {
            let output = renderer.render(&ctx, payload).await.unwrap();
            assert_eq!(output.images.len(), 1);
            assert!(output.images[0].starts_with(b"\xff\xd8\xff"));
            let image = image::load_from_memory(&output.images[0]).unwrap();
            assert_eq!(image.dimensions(), (1180, height));
            if matches!(renderer, MeCurve::Wl) {
                let first = image.get_pixel(200, 300);
                let second = image.get_pixel(650, 300);
                assert!(first[1] > first[0] && second[0] > second[1]);
            } else {
                let pixel = image.get_pixel(475, 337);
                assert!(i16::from(pixel[0]) - i16::from(pixel[1]) > 50);
            }
        }
    }
}
