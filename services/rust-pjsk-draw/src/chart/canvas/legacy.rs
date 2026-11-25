use std::{fmt::Write, path::Path};

use anyhow::{Context, Result};

use crate::chart::{Note, Score, ScoreNote, Slide};

use super::{JAPANESE, Sprites, label, line, rect, svg_root};

pub fn legacy_sus_svg(input: &str, notes_dir: &Path) -> Result<String> {
    let (_, tracks) = input
        .split_once("#MEASUREHS 00")
        .context("missing SUS measure header")?;
    let score = Score::parse(
        tracks
            .lines()
            .filter(|line| {
                line.trim()
                    .as_bytes()
                    .get(4)
                    .is_some_and(|track| matches!(track, b'1' | b'3' | b'5'))
            })
            .collect::<Vec<_>>()
            .join("\n")
            .as_str(),
    )?;
    let last = tracks
        .lines()
        .filter_map(|line| {
            let (header, _) = line.trim().strip_prefix('#')?.split_once(':')?;
            header.get(..3)?.parse::<u32>().ok()
        })
        .max()
        .context("SUS chart has no measures")?;

    const BAR: f64 = 2016.;
    const STEP: f64 = 564.;
    const WIDTH: f64 = 444.;
    const HEIGHT: f64 = 8184.;
    const MARGIN: f64 = 60.;
    let width = f64::from(last / 4 + 1) * STEP;
    let mut svg = svg_root(width, HEIGHT);
    rect(&mut svg, 0., 0., width, HEIGHT, "#000");
    let position = |bar: f64| {
        let column = (bar / 4.).floor();
        (
            MARGIN + column * STEP,
            HEIGHT - MARGIN - ((bar - 4. * column) * BAR).floor(),
        )
    };
    for column in 0..=last / 4 {
        let x = MARGIN + f64::from(column) * STEP;
        for lane in (0..=12).step_by(2) {
            let x = x + f64::from(lane) * 37.;
            let edge = matches!(lane, 0 | 12);
            line(
                &mut svg,
                (x, 0.),
                (x, HEIGHT),
                if edge { "#89cff0" } else { "#fff" },
                if edge { 5. } else { 1. },
            );
        }
    }
    for bar in 0..=last + 1 {
        let (x, y) = position(f64::from(bar));
        for y in [y, y - BAR] {
            line(&mut svg, (x, y), (x + WIDTH, y), "#fff", 2.);
        }
        let text = bar.to_string();
        let attrs = r#"text-anchor="end""#;
        label(
            &mut svg,
            (x - 5., y + 34.),
            &text,
            50,
            JAPANESE,
            "white",
            attrs,
        );
        if bar % 4 == 0 {
            label(
                &mut svg,
                (x - STEP - 5., MARGIN + 34.),
                &text,
                50,
                JAPANESE,
                "white",
                attrs,
            );
        }
    }
    for event in score
        .events()
        .iter()
        .filter(|e| e.text.as_deref() == Some("SKILL"))
    {
        let (x, y) = position(event.bar);
        label(
            &mut svg,
            (x - 5., y - 12.),
            "Skill",
            50,
            JAPANESE,
            "white",
            r#"text-anchor="end""#,
        );
        if event.bar % 4. == 0. {
            label(
                &mut svg,
                (x - STEP - 5., MARGIN + 2.),
                "Skill",
                50,
                JAPANESE,
                "white",
                r#"text-anchor="end""#,
            );
        }
    }

    let lane_x = |note: Note, invisible: bool| {
        let lane = f64::from(note.lane);
        match note.width {
            6.. if !invisible => (lane - 2.) * 37.,
            _ if invisible => (lane - 2.) * 37.,
            2 => (lane - 2.) * 37.,
            width => (lane - f64::from(width) / 2.).trunc() * 37. - 2.,
        }
    };
    let note_width = |note: Note| f64::from(note.width) * 39.;
    let head_critical = |slide: &Slide| {
        slide.head.and_then(|index| score.notes().get(index)).is_some_and(|entry| {
            matches!(entry, ScoreNote::Slide(head) if head.tap.is_some_and(|tap| tap.0.kind == 2))
        })
    };
    for entry in score.notes() {
        let ScoreNote::Slide(start) = entry else {
            continue;
        };
        if start.note.kind == 0 {
            continue;
        }
        let mut next = start.next;
        while let Some(index) = next {
            let ScoreNote::Slide(end) = &score.notes()[index] else {
                break;
            };
            if end.note.kind != 0 {
                let from = start.note;
                let to = end.note;
                let color = if head_critical(start) {
                    "#fff100"
                } else {
                    "#3cd2a0"
                };
                let first = (from.bar / 4.).floor() as u32;
                let last = (to.bar / 4.).floor() as u32;
                for column in first..=last {
                    let cx = MARGIN + f64::from(column) * STEP;
                    let point = |note: Note| {
                        let x = cx + lane_x(note, note.kind == 5);
                        let y =
                            HEIGHT - MARGIN - ((note.bar - f64::from(column) * 4.) * BAR).floor();
                        (x + 2., x + note_width(note) - 2., y)
                    };
                    let (l1, r1, y1) = point(from);
                    let (l2, r2, y2) = point(to);
                    write!(svg, r#"<polygon points="{l1},{y1} {l2},{y2} {r2},{y2} {r1},{y1}" fill="{color}" fill-opacity="0.49"/>"#).unwrap();
                }
                break;
            }
            next = end.next;
        }
    }

    let mut sprites = Sprites::new(notes_dir);
    for entry in score.notes() {
        let note = entry.note();
        let (base, y) = position(note.bar);
        let x = base
            + lane_x(
                note,
                matches!(entry, ScoreNote::Slide(slide) if slide.note.kind == 5),
            );
        let width = note_width(note);
        let (asset, arrow, among) = match entry {
            ScoreNote::Tap(tap) => match tap.0.kind {
                1 => ("notes_normal.png", None, false),
                2 => ("notes_crtcl.png", None, false),
                3 => ("notes_long_among.png", None, true),
                _ => continue,
            },
            ScoreNote::Directional(direction) => match direction.note.kind {
                1 | 3 | 4 => (
                    if direction.tap.is_some_and(|tap| tap.0.kind == 2) {
                        "notes_crtcl.png"
                    } else {
                        "notes_flick.png"
                    },
                    Some(direction.note.kind),
                    false,
                ),
                2 => ("note_long_among_unvisible.png", None, false),
                5 | 6 => ("notes_long.png", None, false),
                _ => continue,
            },
            ScoreNote::Slide(slide) => {
                let critical = head_critical(slide) || slide.tap.is_some_and(|tap| tap.0.kind == 2);
                let arrow = slide
                    .directional
                    .filter(|d| slide.note.kind == 2 && matches!(d.note.kind, 1 | 3 | 4))
                    .map(|d| d.note.kind);
                let (asset, among) = match slide.note.kind {
                    1 | 2 if critical => ("notes_crtcl.png", false),
                    2 if arrow.is_some() => ("notes_flick.png", false),
                    1 | 2 => ("notes_long.png", false),
                    3 if critical => ("notes_long_among_crtcl.png", true),
                    3 => ("notes_long_among.png", true),
                    5 if critical => ("note_long_among_unvisible_crtcl.png", false),
                    5 => ("note_long_among_unvisible.png", false),
                    _ => continue,
                };
                (asset, arrow, among)
            }
        };
        let mut positions = vec![(x, y)];
        if note.bar > 0. && note.bar % 4. == 0. {
            positions.push((x - STEP, MARGIN));
        }
        for (x, y) in positions {
            if among {
                let (w, h) = image::image_dimensions(notes_dir.join(asset))?;
                let w = (f64::from(w) * f64::from(note.width) / 10.).floor();
                let h = (f64::from(h) * f64::from(note.width) / 10.).floor();
                sprites.draw(
                    &mut svg,
                    asset,
                    x + ((width - w) / 2.).trunc(),
                    y - (h / 2.).floor(),
                    w,
                    h,
                    false,
                )?;
            } else {
                sprites.draw(&mut svg, asset, x, y - 9., width, 18., true)?;
            }
            if let Some(arrow) = arrow {
                let critical = if asset == "notes_crtcl.png" {
                    "_crtcl"
                } else {
                    ""
                };
                let suffix = match arrow {
                    3 => "_diagonal_left",
                    4 => "_diagonal_right",
                    _ => "",
                };
                let width_index = (note.width / 2).clamp(1, 6);
                let asset = format!("notes_flick_arrow{critical}_{width_index:02}{suffix}.png");
                let (w, h) = image::image_dimensions(notes_dir.join(&asset))?;
                let (w, h) = (f64::from(w / 4), f64::from(h / 4));
                sprites.draw(
                    &mut svg,
                    &asset,
                    x + ((width - w) / 2.).trunc(),
                    (y - 9. - h * 0.8).trunc(),
                    w,
                    h,
                    false,
                )?;
            }
        }
    }
    sprites.finish(&mut svg);
    svg.push_str("</svg>");
    Ok(svg)
}
