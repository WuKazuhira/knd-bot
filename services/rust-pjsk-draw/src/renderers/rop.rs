use std::collections::{BTreeMap, HashMap};

use crate::components::profile_header::{PjskHeaderData, draw_pjsk_profile_header};
use crate::core::{RenderOutput, RenderRequestContext, Renderer, Result};
use crate::gfx::{GfxContext, RenderContextGfxExt, TextAnchor, paste_with_l_mask};
use async_trait::async_trait;
use image::{GrayImage, Luma, Rgba, RgbaImage};
use serde::Deserialize;
use serde_json::Value;

use crate::renderers::common::{
    BOLD, RODIN, draw_stat_bar, make_style_gradient_bg, parse_payload, render_jpeg,
    rounded_panel_overlay,
};

pub(crate) struct Rop;

#[derive(Default, Deserialize)]
#[serde(default)]
struct Payload {
    diff: Option<String>,
    score: Value,
    header: Value,
    data_update_text: Option<String>,
    pjsk_type: i32,
}

type Score = BTreeMap<i32, [i64; 4]>;

#[async_trait]
impl Renderer for Rop {
    fn name(&self) -> &'static str {
        "rop"
    }

    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput> {
        let payload: Payload = parse_payload(payload)?;
        let master = payload
            .diff
            .as_deref()
            .is_none_or(|diff| diff.is_empty() || diff == "master");
        let (top, bottom) = if payload.diff.as_deref() == Some("expert") {
            ([255, 246, 250], [255, 235, 242])
        } else {
            ([248, 246, 255], [236, 244, 255])
        };
        let mut image = make_style_gradient_bg((1050, 1000), top, bottom, &[]);
        let header = PjskHeaderData::from_payload(&payload.header);
        let mut assets = HashMap::new();
        if let Some(card_id) = header.user_decks.first() {
            let store = ctx.app.master_data.clone();
            let pjsk_type = payload.pjsk_type;
            let card_id = *card_id;
            if let Ok(Ok(cards)) = tokio::task::spawn_blocking(move || {
                store.master_data_by_id("cards.json", pjsk_type, "id")
            })
            .await
                && let Some(asset) = cards
                    .get(&card_id.to_string())
                    .and_then(|card| card["assetbundleName"].as_str())
            {
                assets.insert(card_id, asset.to_owned());
            }
        }

        let gfx = ctx.gfx()?;
        for (bounds, alpha) in [((36, 226, 1014, 710), 148), ((36, 728, 1014, 948), 178)] {
            rounded_panel_overlay(
                &gfx,
                &mut image,
                bounds,
                26,
                Rgba([255, 255, 255, alpha]),
                Some(Rgba([255, 255, 255, 220])),
            );
        }
        draw_pjsk_profile_header(
            ctx,
            &mut image,
            (36, 28, 1014, 192),
            &header,
            if master {
                "MASTER PROGRESS"
            } else {
                "EXPERT PROGRESS"
            },
            payload.pjsk_type,
            Some(&assets),
            None,
            false,
            true,
        )
        .await?;

        let score = payload
            .score
            .as_object()
            .into_iter()
            .flatten()
            .filter_map(|(level, values)| {
                Some((
                    level.parse().ok()?,
                    serde_json::from_value(values.clone()).ok()?,
                ))
            })
            .collect();
        render_jpeg(gfx, 90, true, move |gfx| {
            draw_progress(
                gfx,
                &mut image,
                &score,
                master,
                payload.data_update_text.as_deref(),
            );
            for pixel in image.pixels_mut() {
                // The reference converts RGBA directly to RGB before encoding.
                pixel[3] = 255;
            }
            image
        })
        .await
    }
}

const INK: Rgba<u8> = Rgba([74, 54, 86, 255]);
const MUTED: Rgba<u8> = Rgba([130, 104, 138, 255]);

fn draw_progress(
    gfx: &GfxContext,
    image: &mut RgbaImage,
    score: &Score,
    master: bool,
    update: Option<&str>,
) {
    let fonts = &gfx.fonts;
    fonts.draw_text(
        image,
        (64.0, 238.0),
        "LEVEL PROGRESS",
        RODIN,
        18.0,
        INK,
        TextAnchor::La,
        None,
    );
    fonts.draw_text(
        image,
        (920.0, 238.0),
        "AP / FC / CLEAR",
        RODIN,
        13.0,
        MUTED,
        TextAnchor::Ra,
        None,
    );

    let min_level = if master { 26 } else { 21 };
    for i in 0..5 {
        let level = min_level + i;
        draw_level_card(
            gfx,
            image,
            level,
            score.get(&level).copied().unwrap_or_default(),
            (64, 266 + i * 82, 68),
        );
    }
    for i in 0..if master { 7 } else { 6 } {
        let level = min_level + 5 + i;
        draw_level_card(
            gfx,
            image,
            level,
            score.get(&level).copied().unwrap_or_default(),
            (556, 266 + i * 60, 54),
        );
    }

    let chart = jindu_chart(gfx, score);
    let mask = GrayImage::from_fn(chart.width(), chart.height(), |x, y| {
        Luma([chart.get_pixel(x, y)[3]])
    });
    paste_with_l_mask(image, &chart, &mask, 58, 728);
    fonts.draw_text(
        image,
        (996.0, 918.0),
        "PROGRESS",
        RODIN,
        18.0,
        Rgba([120, 80, 100, 255]),
        TextAnchor::Rm,
        None,
    );
    if let Some(update) = update {
        fonts.draw_text(
            image,
            (54.0, 960.0),
            update,
            BOLD,
            25.0,
            Rgba([92, 72, 98, 255]),
            TextAnchor::La,
            None,
        );
    }
}

fn draw_level_card(
    gfx: &GfxContext,
    image: &mut RgbaImage,
    level: i32,
    [ap, fc, clear, total]: [i64; 4],
    (x, y, height): (i32, i32, i32),
) {
    let total = total.max(1);
    // Pillow replaces RGBA pixels for these cards, and the final RGB conversion drops alpha.
    rounded_panel_overlay(
        gfx,
        image,
        (x, y, x + 430, y + height),
        18,
        Rgba([255; 4]),
        None,
    );
    rounded_panel_overlay(
        gfx,
        image,
        (x + 12, y + 13, x + 78, y + height - 13),
        16,
        Rgba([244, 238, 255, 255]),
        Some(Rgba([224, 214, 246, 255])),
    );
    gfx.fonts.draw_text(
        image,
        ((x + 45) as f32, (y + height / 2) as f32),
        &format!("Lv.{level}"),
        BOLD,
        18.0,
        INK,
        TextAnchor::Mm,
        None,
    );

    let (label_size, num_size, start, gap, bar_h) = if height < 62 {
        (10.0, 13.0, 7, 14, 7)
    } else {
        (11.0, 15.0, 11, 18, 9)
    };
    for (i, (label, value, color)) in [
        ("AP", ap, Rgba([228, 159, 251, 255])),
        ("FC", fc, Rgba([254, 143, 249, 255])),
        ("CLEAR", clear, Rgba([255, 199, 92, 255])),
    ]
    .into_iter()
    .enumerate()
    {
        let yy = y + start + i as i32 * gap;
        let middle = (yy + bar_h / 2) as f32;
        gfx.fonts.draw_text(
            image,
            ((x + 92) as f32, middle),
            label,
            RODIN,
            label_size,
            MUTED,
            TextAnchor::Lm,
            None,
        );
        gfx.fonts.draw_text(
            image,
            ((x + 150) as f32, middle),
            &format!("{value}/{total}"),
            BOLD,
            num_size,
            Rgba([64, 48, 72, 255]),
            TextAnchor::Mm,
            None,
        );
        draw_stat_bar(
            gfx,
            image,
            (x + 186, yy, x + 414, yy + bar_h),
            value as f64 / total as f64,
            color,
        );
    }
}

fn jindu_chart(gfx: &GfxContext, score: &Score) -> RgbaImage {
    let levels = score.iter().filter(|(_, values)| values[3] != 0);
    let mut chart = RgbaImage::new(50 + 40 * levels.clone().count() as u32, 220);
    for (i, (level, &[ap, fc, clear, total])) in levels.enumerate() {
        let x = i as u32 * 40;
        gfx.fonts.draw_text(
            &mut chart,
            ((34 + x) as f32, 185.0),
            &level.to_string(),
            BOLD,
            18.0,
            Rgba([0, 0, 0, 255]),
            TextAnchor::La,
            None,
        );
        for (value, fill, ink) in [
            (total, [68, 68, 102, 255], [68, 68, 102, 255]),
            (clear, [255, 183, 77, 255], [255, 183, 77, 255]),
            (fc, [240, 98, 146, 255], [240, 98, 146, 255]),
            (ap, [251, 217, 221, 255], [100, 181, 246, 255]),
        ] {
            let height = (140.0 * value as f64 / total as f64) as i32;
            let top = 180i32.saturating_sub(height);
            for yy in top.max(0) as u32..=180 {
                for xx in x + 28..=x + 60 {
                    chart.put_pixel(xx, yy, Rgba(fill));
                }
            }
            if value != 0 {
                gfx.fonts.draw_text(
                    &mut chart,
                    ((43 + x) as f32, 152.0 - height as f32),
                    &value.to_string(),
                    BOLD,
                    18.0,
                    Rgba(ink),
                    TextAnchor::Ma,
                    Some((2, Rgba([255; 4]))),
                );
            }
        }
    }
    chart
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::renderers::common::offline_context;

    #[tokio::test]
    async fn renders_jpeg_without_avatar() {
        let output = Rop
            .render(
                &offline_context(),
                serde_json::json!({
                    "diff": "expert", "score": {"21": [1, 2, 3, 4], "33+musicId": [123]},
                    "header": {"name": "Player"}, "data_update_text": "Cached data"
                }),
            )
            .await
            .unwrap();
        let image =
            image::load_from_memory_with_format(&output.images[0], image::ImageFormat::Jpeg)
                .unwrap();
        assert_eq!((image.width(), image.height()), (1050, 1000));
    }
}
