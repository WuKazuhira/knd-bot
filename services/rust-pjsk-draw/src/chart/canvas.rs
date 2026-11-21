use std::{collections::HashMap, fmt::Write, fs, path::Path};

use crate::gfx::FontRegistry;
use anyhow::{Context, Result, ensure};
use base64::{Engine, engine::general_purpose::STANDARD};
use image::RgbImage;
use resvg::usvg;
use serde::Deserialize;

use crate::chart::{
    Directional, Event, Note, Score, ScoreNote, Slide, Word, get_denominator, score::merge_events,
};

mod legacy;
pub use legacy::legacy_sus_svg;

const LANE: f64 = 12.;
const TRACK: f64 = LANE * 12.;
const PAD: f64 = 36.;
const COLUMN: f64 = TRACK + 2. * PAD;
const META: f64 = 120.;
const SANS: &str = "Avenir, DejaVu Sans, sans-serif";
const JAPANESE: &str =
    "ヒラギノ角ゴシック, Source Han Sans CN, Noto Sans CJK JP, DejaVu Sans, sans-serif";

#[derive(Clone, Debug, Default)]
pub struct ChartMeta {
    pub title: String,
    pub artist: String,
    pub difficulty: String,
    pub play_level: Option<u32>,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct MusicMeta {
    pub fever_end_time: Option<f64>,
    pub fever_score: Option<f64>,
    pub skill_score_solo: Vec<f64>,
    pub skill_score_multi: Vec<f64>,
}

#[derive(Clone, Copy, Debug)]
pub enum CoverStyle {
    SkillDuration,
    SkillPerfect,
    SkillGreat,
    FeverDuration,
}

impl CoverStyle {
    fn fill(self) -> &'static str {
        match self {
            Self::SkillDuration => "#888",
            Self::SkillPerfect => "#777",
            Self::SkillGreat => "#666",
            Self::FeverDuration => "#546",
        }
    }
}

#[derive(Clone, Debug)]
pub struct CoverRect {
    pub bar_from: f64,
    pub bar_to: f64,
    pub style: CoverStyle,
}

#[derive(Clone, Debug)]
pub struct CoverText {
    pub bar: f64,
    pub text: String,
}

pub struct SusCanvas<'a> {
    pub score: &'a Score,
    pub meta: &'a ChartMeta,
    pub notes_dir: &'a Path,
    pub jacket: Option<&'a Path>,
    pub note_size: f64,
    pub words: &'a [Word],
}

pub struct SusWithSkillCanvas<'a> {
    pub canvas: SusCanvas<'a>,
    pub music_meta: Option<&'a MusicMeta>,
    pub display_skill_extra: bool,
}

#[derive(Default)]
struct Covers {
    rects: Vec<CoverRect>,
    texts: Vec<CoverText>,
}

impl SusWithSkillCanvas<'_> {
    pub fn to_svg(&self) -> Result<String> {
        let score = self.canvas.score;
        let mut covers = Covers::default();
        if let Some(meta) = self.music_meta {
            for event in score
                .events()
                .iter()
                .filter(|e| e.text.as_deref() == Some("SUPER FEVER!!"))
            {
                if let Some(end) = meta.fever_end_time {
                    covers.rects.push(CoverRect {
                        bar_from: event.bar,
                        bar_to: score.get_bar(end),
                        style: CoverStyle::FeverDuration,
                    });
                }
                if let Some(value) = meta.fever_score {
                    covers.texts.push(CoverText {
                        bar: event.bar,
                        text: format!("多+{:.2}%", value * 100.),
                    });
                }
            }
        }
        for (index, event) in score
            .events()
            .iter()
            .filter(|e| e.text.as_deref() == Some("SKILL"))
            .enumerate()
        {
            let time = score.get_time(event.bar);
            if self.display_skill_extra {
                for (frames, style) in [
                    (5., CoverStyle::SkillGreat),
                    (2.5, CoverStyle::SkillPerfect),
                    (0., CoverStyle::SkillDuration),
                ] {
                    covers.rects.push(CoverRect {
                        bar_from: score.get_bar(time - frames / 60.),
                        bar_to: score.get_bar(time + 5. + frames / 60.),
                        style,
                    });
                }
            }
            if let Some(meta) = self.music_meta
                && let Some(solo) = meta.skill_score_solo.get(index)
            {
                let solo = format!("+{:.2}%", solo * 100.);
                let multi = meta
                    .skill_score_multi
                    .get(index)
                    .map(|multi| format!("+{:.2}%", multi * 100.));
                let label = match multi {
                    Some(multi) if multi != solo => format!("単{solo} 多{multi}"),
                    _ => solo,
                };
                covers.texts.push(CoverText {
                    bar: event.bar,
                    text: label,
                });
            }
        }
        self.canvas.render(Some(&covers))
    }
}

impl SusCanvas<'_> {
    pub fn to_svg(&self) -> Result<String> {
        self.render(None)
    }

    fn speed(&self) -> f64 {
        match self.meta.play_level {
            Some(0..=30) => 240.,
            Some(level @ 31..=33) => 240. + f64::from(level - 30) * 25.,
            _ => 340.,
        }
    }

    fn render(&self, covers: Option<&Covers>) -> Result<String> {
        let last = self
            .score
            .notes()
            .last()
            .context("SUS chart has no playable notes")?
            .note()
            .bar;
        let bars = ((last - 1e-6).ceil() as u32).max(1);
        let mut columns = Vec::new();
        let mut from = 0;
        let mut previous = self.score.get_event(0.);
        for bar in 1..=bars {
            let event = self.score.get_event(f64::from(bar));
            if bar == bars
                || bar - from == previous.sentence_length.unwrap_or(4)
                || event.section != previous.section
                || event.sentence_length != previous.sentence_length
            {
                let height = (self.speed()
                    * self.score.get_time_delta(f64::from(from), f64::from(bar))
                    + PAD * 2.)
                    .round_ties_even();
                columns.push((from, bar, height));
                from = bar;
            }
            previous = event;
        }
        let height = columns.iter().map(|column| column.2).fold(0., f64::max);
        let columns_width = COLUMN * columns.len() as f64;
        let width = columns_width + PAD * 2.;
        let meta_y = height + PAD * 2.;
        let mut svg = svg_root(width, meta_y + META + PAD * 2.);
        svg.push_str(GRADIENTS);
        rect(&mut svg, 0., 0., width, meta_y, "#000");
        rect(&mut svg, 0., meta_y, width, META + PAD * 2., "#444");
        line(&mut svg, (0., meta_y), (width, meta_y), "#e2e2e2", 2.);
        if let Some(jacket) = self.jacket {
            let bytes =
                fs::read(jacket).with_context(|| format!("reading jacket {}", jacket.display()))?;
            sprite(
                &mut svg,
                &STANDARD.encode(bytes),
                PAD * 2.,
                height + PAD * 3.,
                META,
                META,
                false,
            );
        }
        let heading_x = META + PAD * 3.;
        label(
            &mut svg,
            (heading_x, height + PAD * 3. + META - 8.),
            &format!("{} - {}", self.meta.title, self.meta.artist),
            96,
            JAPANESE,
            "white",
            r#"font-weight="900""#,
        );
        let level = self
            .meta
            .play_level
            .map_or_else(|| "?".into(), |value| value.to_string());
        label(
            &mut svg,
            (heading_x, height + PAD * 4.),
            &format!(
                "{} {level} 譜面確認 by ぷろせかもえ！ (pjsekai.moe)",
                self.meta.difficulty.to_uppercase()
            ),
            48,
            JAPANESE,
            "white",
            r#"font-weight="700""#,
        );
        if covers.is_some() {
            label(
                &mut svg,
                (columns_width - 900., height + PAD * 4.2),
                "Code by ぷろせかもえ！ (pjsekai.moe)　& Unibot",
                16,
                JAPANESE,
                "black",
                "",
            );
            label(
                &mut svg,
                (columns_width - 770., height + PAD * 5.9),
                "Modified by 33 (3-3.dev & bilibili @xfl03)",
                16,
                JAPANESE,
                "black",
                "",
            );
        } else {
            label(
                &mut svg,
                (columns_width - 600., height + PAD * 6.7),
                "Code by ぷろせかもえ！ (pjsekai.moe)",
                16,
                JAPANESE,
                "black",
                "",
            );
        }

        let mut sprites = Sprites::new(self.notes_dir);
        for (index, (from, to, h)) in columns.into_iter().enumerate() {
            let x = PAD + COLUMN * index as f64;
            let y = height - h + PAD;
            write!(
                svg,
                r#"<svg x="{x}" y="{y}" width="{COLUMN}" height="{h}" overflow="hidden">"#
            )
            .unwrap();
            self.column(&mut svg, from, to, h, covers, &mut sprites)?;
            svg.push_str("</svg>");
        }
        sprites.finish(&mut svg);
        svg.push_str("</svg>");
        Ok(svg)
    }

    pub fn scale_svg(&self) -> Result<String> {
        let mut svg = svg_root(META, META);
        rect(&mut svg, 0., 0., META, META, "#000");
        rect(&mut svg, 36., 0., 48., META, "#444");
        for x in [36., 84.] {
            line(&mut svg, (x, 0.), (x, META), "#e2e2e2", 1.);
        }
        line(&mut svg, (0., 60.), (META, 60.), "#e2e2e2", 1.);
        let y = |frame| 60. + frame / 60. * self.speed();
        for frame in -16..16 {
            line(
                &mut svg,
                (36., y(f64::from(frame))),
                (30., y(f64::from(frame))),
                "#e2e2e2",
                1.,
            );
        }
        label(
            &mut svg,
            (26., 58.),
            "frame",
            8,
            "Verdana, sans-serif",
            "#e2e2e2",
            r#"text-anchor="end""#,
        );
        for (frames, judge) in [(7.5, "BAD"), (6.5, "GOOD"), (5., "GREAT"), (2.5, "PERFECT")] {
            for frame in [frames, -frames] {
                line(&mut svg, (84., y(frame)), (108., y(frame)), "#e2e2e2", 1.);
                label(
                    &mut svg,
                    (88., y(frame) + if frame < 0. { 6. } else { 0. }),
                    judge,
                    8,
                    "Verdana, sans-serif",
                    "#e2e2e2",
                    "",
                );
            }
        }
        let mut sprites = Sprites::new(self.notes_dir);
        self.tap(
            &mut svg,
            &mut sprites,
            Note {
                bar: 0.,
                lane: 3,
                width: 4,
                kind: 1,
            },
            60.,
            "normal",
        )?;
        sprites.finish(&mut svg);
        svg.push_str("</svg>");
        Ok(svg)
    }

    fn column(
        &self,
        svg: &mut String,
        from: u32,
        to: u32,
        height: f64,
        covers: Option<&Covers>,
        sprites: &mut Sprites<'_>,
    ) -> Result<()> {
        let first = f64::from(from);
        let last = f64::from(to);
        let y = |bar| self.speed() * self.score.get_time_delta(bar, last) + PAD;
        let visible = |bar| (first - 1. ..last + 1.).contains(&bar);

        rect(svg, 0., 0., COLUMN, height, "#000");
        rect(svg, PAD, 0., TRACK, height, "#444");
        if let Some(covers) = covers {
            for cover in &covers.rects {
                let start = cover.bar_from.max(first - 0.2);
                let end = cover.bar_to.min(last + 0.2);
                if start < end {
                    rect(
                        svg,
                        PAD,
                        y(end).round_ties_even(),
                        TRACK,
                        (y(start) - y(end)).round_ties_even(),
                        cover.style.fill(),
                    );
                }
            }
            for cover in &covers.texts {
                if (first - 0.2..last - 0.1).contains(&cover.bar) {
                    let x = COLUMN - 3.;
                    let y = y(cover.bar).round_ties_even();
                    label(
                        svg,
                        (x, y),
                        &cover.text,
                        38,
                        &format!("Osaka, {JAPANESE}"),
                        "white",
                        &rotate(x, y),
                    );
                }
            }
        }

        for lane in (0..=12).step_by(2) {
            let x = PAD + LANE * f64::from(lane);
            line(svg, (x, 0.), (x, height), "#646464", 1.);
        }
        for bar in from..=to {
            let bar = f64::from(bar);
            let px = y(bar).round_ties_even();
            line(svg, (PAD, px), (PAD + TRACK, px), "#6f6f6f", 2.);
            let beats = self.score.get_event(bar).bar_length.unwrap_or(4.);
            for beat in 1..beats.ceil().min(1024.) as u32 {
                let px = y(bar + f64::from(beat) / beats).round_ties_even();
                line(svg, (PAD, px), (PAD + TRACK, px), "#646464", 1.);
            }
        }

        let mut events: Vec<_> = self
            .score
            .events()
            .iter()
            .filter(|e| visible(e.bar))
            .cloned()
            .collect();
        events.extend((from..=to).map(|bar| Event {
            bar: f64::from(bar),
            ..Event::default()
        }));
        for event in merge_events(events) {
            let color = if event.bpm.is_some()
                || event.bar_length.is_some()
                || event.section.is_some()
                || event.text.is_some()
            {
                "#fee300"
            } else {
                "#ffffff"
            };
            let mut parts = Vec::new();
            if (event.bar - event.bar.round()).abs() < 1e-6 {
                parts.push(format!("#{}", event.bar.round()));
            }
            if let Some(bpm) = event.bpm {
                parts.push(format!("{bpm} BPM"));
            }
            if let Some(beats) = event.bar_length {
                parts.push(format!("{beats}/4"));
            }
            parts.extend(event.section);
            parts.extend(event.text);
            let px = y(event.bar).round_ties_even();
            label(
                svg,
                (PAD + 8., px - 20.),
                &parts.join(", "),
                12,
                SANS,
                color,
                &format!(r#"font-weight="900" {}"#, rotate(PAD, px)),
            );
        }
        for word in self.words.iter().filter(|word| visible(word.bar)) {
            let x = PAD + TRACK;
            let px = y(word.bar).round_ties_even();
            label(
                svg,
                (x, px + 16.),
                &word.text,
                12,
                JAPANESE,
                "white",
                &rotate(x, px),
            );
        }

        let mut paths = String::new();
        let mut among = String::new();
        let mut taps = String::new();
        let mut flicks = String::new();
        let mut ticks = String::new();
        let notes = self.score.notes();
        let mut next_ticks = notes
            .iter()
            .filter_map(|entry| match entry {
                ScoreNote::Slide(slide) if !matches!(slide.note.kind, 1 | 2) => None,
                _ => Some(entry.note().bar),
            })
            .peekable();
        for entry in notes {
            let note = entry.note();
            while next_ticks.peek().is_some_and(|&bar| bar <= note.bar) {
                next_ticks.next();
            }
            if let ScoreNote::Slide(slide) = entry
                && matches!(note.kind, 1 | 3 | 5)
                && slide.is_path_note()
                && let Some(end) = self.next_path(slide)
                && note.bar < last + 1.
                && end.note.bar >= first - 1.
            {
                self.slide_path(&mut paths, &mut among, sprites, slide, end, &y)?;
            }
            if !visible(note.bar) {
                continue;
            }
            let px = y(note.bar);
            let critical = |tap: Option<crate::chart::Tap>| tap.is_some_and(|tap| tap.0.kind == 2);
            let kind = match entry {
                ScoreNote::Tap(_) => {
                    if note.kind == 2 {
                        "crtcl"
                    } else {
                        "normal"
                    }
                }
                ScoreNote::Directional(direction) => {
                    self.flick(&mut flicks, sprites, direction, px, critical(direction.tap))?;
                    if critical(direction.tap) {
                        "crtcl"
                    } else {
                        "flick"
                    }
                }
                ScoreNote::Slide(slide) => match note.kind {
                    1 => {
                        if critical(slide.tap) {
                            "crtcl"
                        } else {
                            "long"
                        }
                    }
                    2 => {
                        let critical = critical(slide.tap) || critical(self.head_tap(slide));
                        if let Some(direction) = &slide.directional {
                            self.flick(&mut flicks, sprites, direction, px, critical)?;
                        }
                        if critical {
                            "crtcl"
                        } else if slide.directional.is_some() {
                            "flick"
                        } else {
                            "long"
                        }
                    }
                    3 => {
                        let px = px.round_ties_even();
                        line(&mut ticks, (PAD - 6., px), (PAD, px), "#e2e2e2", 1.);
                        continue;
                    }
                    _ => continue,
                },
            };
            self.tap(&mut taps, sprites, note, px, kind)?;
            self.tick(
                &mut ticks,
                note.bar,
                next_ticks.peek().copied(),
                px,
                covers.is_some(),
            );
        }
        for layer in [paths, among, taps, flicks, ticks] {
            svg.push_str(&layer);
        }
        Ok(())
    }

    fn head_tap(&self, slide: &Slide) -> Option<crate::chart::Tap> {
        match self.score.notes().get(slide.head?)? {
            ScoreNote::Slide(head) => head.tap,
            _ => None,
        }
    }

    fn next_path(&self, slide: &Slide) -> Option<&Slide> {
        let mut next = slide.next;
        while let Some(index) = next {
            let ScoreNote::Slide(part) = self.score.notes().get(index)? else {
                return None;
            };
            if part.is_path_note() {
                return Some(part);
            }
            next = part.next;
        }
        None
    }

    fn slide_path(
        &self,
        paths: &mut String,
        among: &mut String,
        sprites: &mut Sprites<'_>,
        start: &Slide,
        end: &Slide,
        y: &impl Fn(f64) -> f64,
    ) -> Result<()> {
        let a = y(start.note.bar);
        let b = y(end.note.bar);
        let middle = (a + b) / 2.;
        let ease = start.directional.map(|direction| direction.note.kind);
        let c = if ease == Some(2) { middle } else { a };
        let d = if matches!(ease, Some(5 | 6)) {
            middle
        } else {
            b
        };
        let left = [x(start.note) + 1., x(end.note) + 1.];
        let right = [
            x(start.note) + LANE * f64::from(start.note.width) - 1.,
            x(end.note) + LANE * f64::from(end.note.width) - 1.,
        ];
        let critical = self.head_tap(start).is_some_and(|tap| tap.0.kind != 1);
        let color = if critical { "#fcf1c3" } else { "#c9fce2" };
        let [a, c, d, b] = [a, c, d, b].map(f64::round_ties_even);
        write!(paths, r#"<path d="M {} {a} C {} {c} {} {d} {} {b} L {} {b} C {} {d} {} {c} {} {a} Z" fill="{color}" fill-opacity="0.8"/>"#, left[0], left[0], left[1], left[1], right[1], right[1], right[0], right[0]).unwrap();
        let curve_y = [
            y(start.note.bar),
            if ease == Some(2) {
                middle
            } else {
                y(start.note.bar)
            },
            if matches!(ease, Some(5 | 6)) {
                middle
            } else {
                y(end.note.bar)
            },
            y(end.note.bar),
        ];
        let mut next = start.next;
        while let Some(index) = next {
            let ScoreNote::Slide(part) = &self.score.notes()[index] else {
                break;
            };
            if part.note.kind == 2 || part.note.bar > end.note.bar {
                break;
            }
            if part.is_among_note() {
                let py = y(part.note.bar);
                let t = bezier_parameter(curve_y, py);
                let px = (cubic([left[0], left[0], left[1], left[1]], t)
                    + cubic([right[0], right[0], right[1], right[1]], t))
                    / 2.;
                let name = if critical {
                    "notes_long_among_crtcl.png"
                } else {
                    "notes_long_among.png"
                };
                sprites.draw(
                    among,
                    name,
                    (px - LANE / 2.).round_ties_even(),
                    (py - LANE / 2.).round_ties_even(),
                    LANE,
                    LANE,
                    false,
                )?;
            }
            next = part.next;
        }
        Ok(())
    }

    fn tap(
        &self,
        svg: &mut String,
        sprites: &mut Sprites<'_>,
        note: Note,
        y: f64,
        kind: &str,
    ) -> Result<()> {
        let w = (8. * self.note_size).round_ties_even();
        let h = (16. * self.note_size).round_ties_even();
        let left = (x(note) - 4. * self.note_size).round_ties_even();
        let right =
            (x(note) + LANE * f64::from(note.width) - 4. * self.note_size).round_ties_even();
        let y = (y - 8. * self.note_size).round_ties_even();
        for (side, x) in [("left", left), ("right", right)] {
            sprites.draw(svg, &format!("notes_{kind}_{side}.png"), x, y, w, h, false)?;
        }
        if right > left + w {
            sprites.draw(
                svg,
                &format!("notes_{kind}_middle.png"),
                left + w,
                y,
                right - left - w,
                h,
                true,
            )?;
        }
        Ok(())
    }

    fn flick(
        &self,
        svg: &mut String,
        sprites: &mut Sprites<'_>,
        direction: &Directional,
        y: f64,
        critical: bool,
    ) -> Result<()> {
        let note = direction.note;
        let (suffix, bias) = match note.kind {
            3 => ("_diagonal_left", -4. * self.note_size),
            4 => ("_diagonal_right", 4. * self.note_size),
            _ => ("", 0.),
        };
        let prefix = if critical { "_crtcl" } else { "" };
        let width = note.width.min(6);
        let w = ((f64::from(width) + 0.5) / 3.).powf(0.75) * 24. * self.note_size;
        let h = ((f64::from(width) + 2.5) / 3.).powf(0.75) * 16. * self.note_size;
        sprites.draw(
            svg,
            &format!("notes_flick_arrow{prefix}_{width:02}{suffix}.png"),
            (x(note) + LANE * f64::from(note.width) / 2. - w / 2. + bias).round_ties_even(),
            (y - 8. * self.note_size - h * 2. / 3.).round_ties_even(),
            w.round_ties_even(),
            h.round_ties_even(),
            false,
        )?;
        Ok(())
    }

    fn tick(&self, svg: &mut String, bar: f64, next: Option<f64>, y: f64, skill: bool) {
        let interval = next
            .filter(|next| {
                next - bar <= 1.
                    && (next - bar <= 0.5 || (next + 1e-3).floor() == (bar + 1e-3).floor())
            })
            .map_or_else(|| (bar + 1.001).floor() - bar, |next| next - bar);
        let interval = interval * self.score.get_event(bar + 1e-3).bar_length.unwrap_or(4.) / 4.;
        let denominator = get_denominator(interval);
        let numerator = (interval * f64::from(denominator)).round_ties_even() as i32;
        line(
            svg,
            (PAD / 2., y.round_ties_even()),
            (PAD, y.round_ties_even()),
            "#e2e2e2",
            1.,
        );
        if skill || numerator != 0 || denominator != 1 {
            let text = if numerator == 1 {
                format!("/{denominator}")
            } else {
                format!("{numerator}/{denominator}")
            };
            label(
                svg,
                (PAD - 4., (y - 2.).round_ties_even()),
                &text,
                12,
                SANS,
                "#e2e2e2",
                r#"text-anchor="end""#,
            );
        }
    }
}

fn x(note: Note) -> f64 {
    PAD + LANE * (f64::from(note.lane) - 2.)
}

fn cubic([a, b, c, d]: [f64; 4], t: f64) -> f64 {
    let u = 1. - t;
    a * u.powi(3) + 3. * b * u * u * t + 3. * c * u * t * t + d * t.powi(3)
}

fn bezier_parameter(curve: [f64; 4], y: f64) -> f64 {
    if y >= curve[0] {
        return 0.;
    }
    if y <= curve[3] {
        return 1.;
    }
    let (mut low, mut high) = (0., 1.);
    for _ in 0..32 {
        let t = (low + high) / 2.;
        let point = cubic(curve, t);
        if (point - y).abs() < 0.1 {
            return t;
        }
        if point > y {
            low = t;
        } else {
            high = t;
        }
    }
    (low + high) / 2.
}

const GRADIENTS: &str = r##"<defs>
<linearGradient id="decoration-gradient" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#c9fce2" stop-opacity="0.6"/><stop offset="1" stop-color="#c9fce2" stop-opacity="0.2"/></linearGradient>
<linearGradient id="decoration-critical-gradient" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#fcf1c3" stop-opacity="0.6"/><stop offset="1" stop-color="#fcf1c3" stop-opacity="0.2"/></linearGradient>
</defs>"##;

fn svg_root(width: f64, height: f64) -> String {
    format!(
        r#"<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{width}" height="{height}">"#
    )
}

fn rect(svg: &mut String, x: f64, y: f64, w: f64, h: f64, fill: &str) {
    write!(
        svg,
        r#"<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}"/>"#
    )
    .unwrap();
}

fn line(svg: &mut String, (x1, y1): (f64, f64), (x2, y2): (f64, f64), color: &str, width: f64) {
    write!(
        svg,
        r#"<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}"/>"#
    )
    .unwrap();
}

fn label(
    svg: &mut String,
    (x, y): (f64, f64),
    text: &str,
    size: u8,
    font: &str,
    fill: &str,
    attrs: &str,
) {
    let text = text
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;");
    write!(svg, r#"<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" fill="{fill}" {attrs}>{text}</text>"#).unwrap();
}

fn rotate(x: f64, y: f64) -> String {
    format!(r#"transform="rotate(-90 {x} {y})""#)
}

fn sprite(svg: &mut String, data: &str, x: f64, y: f64, w: f64, h: f64, stretch: bool) {
    let aspect = if stretch { "none" } else { "xMidYMid meet" };
    write!(svg, r#"<image x="{x}" y="{y}" width="{w}" height="{h}" preserveAspectRatio="{aspect}" xlink:href="data:image/png;base64,{data}"/>"#).unwrap();
}

struct Sprites<'a> {
    dir: &'a Path,
    assets: HashMap<String, SpriteAsset>,
    defs: String,
}

#[derive(Clone, Copy)]
struct SpriteAsset {
    id: usize,
    width: f64,
    height: f64,
}

impl<'a> Sprites<'a> {
    fn new(dir: &'a Path) -> Self {
        Self {
            dir,
            assets: HashMap::new(),
            defs: String::new(),
        }
    }

    fn get(&mut self, name: &str) -> Result<SpriteAsset> {
        use std::collections::hash_map::Entry;

        let id = self.assets.len();
        match self.assets.entry(name.to_owned()) {
            Entry::Occupied(entry) => Ok(*entry.get()),
            Entry::Vacant(entry) => {
                let path = self.dir.join(name);
                let bytes = fs::read(&path)
                    .with_context(|| format!("reading sprite {}", path.display()))?;
                let size = image::ImageReader::with_format(
                    std::io::Cursor::new(&bytes),
                    image::ImageFormat::Png,
                )
                .into_dimensions()
                .with_context(|| format!("reading sprite dimensions {}", path.display()))?;
                let width = f64::from(size.0);
                let height = f64::from(size.1);
                let uri = STANDARD.encode(bytes);
                write!(self.defs, r#"<image id="sprite-{id}" width="{width}" height="{height}" xlink:href="data:image/png;base64,{uri}"/>"#).unwrap();
                Ok(*entry.insert(SpriteAsset { id, width, height }))
            }
        }
    }

    fn draw(
        &mut self,
        svg: &mut String,
        name: &str,
        mut x: f64,
        mut y: f64,
        w: f64,
        h: f64,
        stretch: bool,
    ) -> Result<()> {
        if w <= 0. || h <= 0. {
            return Ok(());
        }
        let SpriteAsset { id, width, height } = self.get(name)?;
        let (mut sx, mut sy) = (w / width, h / height);
        if !stretch {
            let scale = sx.min(sy);
            x += (w - scale * width) / 2.;
            y += (h - scale * height) / 2.;
            (sx, sy) = (scale, scale);
        }
        write!(
            svg,
            r##"<use xlink:href="#sprite-{id}" transform="translate({x} {y}) scale({sx} {sy})"/>"##
        )
        .unwrap();
        Ok(())
    }

    fn finish(self, svg: &mut String) {
        svg.push_str("<defs>");
        svg.push_str(&self.defs);
        svg.push_str("</defs>");
    }
}

pub fn rasterize(svg: &str, fonts: &FontRegistry, scale: f64) -> Result<RgbImage> {
    ensure!(scale.is_finite() && scale > 0., "invalid chart scale");
    let options = usvg::Options {
        fontdb: fonts.svg_fonts(),
        ..usvg::Options::default()
    };
    let tree = usvg::Tree::from_str(svg, &options)?;
    let width = (f64::from(tree.size().width()) * scale) as u32;
    let height = (f64::from(tree.size().height()) * scale) as u32;
    ensure!(
        u64::from(width) * u64::from(height) <= 40_000_000,
        "chart exceeds raster size limit"
    );
    let mut pixmap = tiny_skia::Pixmap::new(width, height).context("invalid chart raster size")?;
    resvg::render(
        &tree,
        tiny_skia::Transform::from_scale(scale as f32, scale as f32),
        &mut pixmap.as_mut(),
    );
    let rgb = pixmap
        .data()
        .chunks_exact(4)
        .flat_map(|rgba| rgba[..3].iter().copied())
        .collect();
    RgbImage::from_raw(width, height, rgb).context("invalid chart pixels")
}
