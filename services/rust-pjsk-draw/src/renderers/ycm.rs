use crate::core::{DrawError, RenderOutput, RenderRequestContext, Renderer, Result};
use crate::gfx::{GfxContext, RenderContextGfxExt, TextAnchor};
use async_trait::async_trait;
use image::{Rgba, RgbaImage};
use serde::Deserialize;
use serde_json::Value;

use crate::renderers::common::{
    BOLD, MEDIUM, SoftGlow, fit_text, make_style_gradient_bg, null_default, parse_payload,
    render_png, rounded_panel_overlay,
};

pub(crate) struct Ycm;

#[derive(Deserialize)]
struct Payload {
    #[serde(default, deserialize_with = "null_default")]
    cars: Vec<Car>,
}

#[derive(Deserialize)]
struct Car {
    room: String,
    des: String,
    time: String,
}

#[async_trait]
impl Renderer for Ycm {
    fn name(&self) -> &'static str {
        "ycm"
    }

    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput> {
        let payload: Payload = parse_payload(payload)?;
        if payload.cars.is_empty() {
            return Err(DrawError::InvalidPayload(
                "没有找到车！建议检查网页结构是否变化".into(),
            ));
        }
        render_png(ctx.gfx()?, move |gfx| draw_cars(gfx, &payload.cars)).await
    }
}

const WIDTH: u32 = 1180;
const COLUMNS: usize = 3;
const PAD: i32 = 28;
const GAP: i32 = 40;
const CARD_W: i32 = (WIDTH as i32 - 2 * PAD - 2 * GAP) / COLUMNS as i32;
const TEXT_W: f32 = (CARD_W - 36) as f32;
const LINE_H: i32 = 25;
const BODY_SIZE: f32 = 18.0;

fn draw_cars(gfx: &GfxContext, cars: &[Car]) -> RgbaImage {
    let fonts = &gfx.fonts;
    let descriptions: Vec<_> = cars
        .iter()
        .map(|car| wrap_description(gfx, &car.des))
        .collect();
    let row_heights: Vec<_> = descriptions
        .chunks(COLUMNS)
        .map(|row| 94 + LINE_H * row.iter().map(Vec::len).max().unwrap_or_default() as i32)
        .collect();
    let row_gap = 20;
    let height =
        (2 * PAD + row_heights.iter().map(|h| h + row_gap).sum::<i32>() - row_gap).max(300) as u32;
    let mut image = make_style_gradient_bg(
        (WIDTH, height),
        [239, 231, 249],
        [226, 238, 252],
        &[
            SoftGlow {
                center: (230.0, 50.0),
                radii: (610.0, 340.0),
                color: Rgba([255, 206, 229, 160]),
            },
            SoftGlow {
                center: (1050.0, 240.0),
                radii: (460.0, 400.0),
                color: Rgba([213, 217, 255, 150]),
            },
        ],
    );

    let mut y = PAD;
    for ((row, descriptions), row_h) in cars
        .chunks(COLUMNS)
        .zip(descriptions.chunks(COLUMNS))
        .zip(row_heights)
    {
        for (col, (car, lines)) in row.iter().zip(descriptions).enumerate() {
            let x = PAD + col as i32 * (CARD_W + GAP);
            let card_h = 94 + LINE_H * lines.len() as i32;
            rounded_panel_overlay(
                gfx,
                &mut image,
                (x, y, x + CARD_W, y + card_h),
                18,
                Rgba([134, 134, 169, 255]),
                None,
            );
            rounded_panel_overlay(
                gfx,
                &mut image,
                (x + 3, y + 3, x + CARD_W - 3, y + card_h - 3),
                15,
                Rgba([255, 255, 255, 210]),
                Some(Rgba([191, 191, 209, 255])),
            );

            let tx = (x + 18) as f32;
            fonts.draw_text(
                &mut image,
                (tx, (y + 15) as f32),
                &fit_text(fonts, &car.room, BOLD, 24.0, TEXT_W),
                BOLD,
                24.0,
                Rgba([136, 68, 153, 255]),
                TextAnchor::La,
                None,
            );
            let mut ty = y + 49;
            for line in lines {
                fonts.draw_text(
                    &mut image,
                    (tx, ty as f32),
                    line,
                    MEDIUM,
                    BODY_SIZE,
                    Rgba([68, 68, 102, 255]),
                    TextAnchor::La,
                    None,
                );
                ty += LINE_H;
            }
            fonts.draw_text(
                &mut image,
                (tx, (ty + 11) as f32),
                &fit_text(fonts, &car.time, BOLD, 15.0, TEXT_W),
                BOLD,
                15.0,
                Rgba([92, 83, 118, 255]),
                TextAnchor::La,
                None,
            );
        }
        y += row_h + row_gap;
    }
    for pixel in image.pixels_mut() {
        pixel[3] = 255;
    }
    image
}

fn wrap_description(gfx: &GfxContext, description: &str) -> Vec<String> {
    let mut lines = Vec::new();
    for paragraph in description.split('\n') {
        let mut line = String::new();
        for word in paragraph.split_whitespace() {
            let mut candidate = line.clone();
            if !line.is_empty() {
                candidate.push(' ');
            }
            candidate.push_str(word);
            if gfx.fonts.measure_text(MEDIUM, BODY_SIZE, &candidate).0 <= TEXT_W {
                line = candidate;
                continue;
            }
            if !line.is_empty() {
                lines.push(std::mem::take(&mut line));
            }
            for ch in word.chars() {
                line.push(ch);
                if line.len() > ch.len_utf8()
                    && gfx.fonts.measure_text(MEDIUM, BODY_SIZE, &line).0 > TEXT_W
                {
                    line.pop();
                    lines.push(std::mem::take(&mut line));
                    line.push(ch);
                }
            }
        }
        lines.push(line);
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::renderers::common::offline_context;
    use serde_json::json;

    #[tokio::test]
    async fn renders_png_and_rejects_empty_cars() {
        let ctx = offline_context();
        let output = Ycm.render(&ctx, json!({
            "cars": [{"room": "12345", "des": "Recruiting\nTwo spots left", "time": "12:20"}]
        })).await.unwrap();
        let image = image::load_from_memory_with_format(&output.images[0], image::ImageFormat::Png)
            .unwrap();
        assert_eq!((image.width(), image.height()), (1180, 300));
        assert!(matches!(
            Ycm.render(&ctx, json!({"cars": []})).await,
            Err(DrawError::InvalidPayload(_))
        ));
    }
}
