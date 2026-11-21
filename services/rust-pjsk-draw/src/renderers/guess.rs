use std::{collections::hash_map::RandomState, hash::BuildHasher, path::Path};

use crate::core::{DrawError, RenderOutput, RenderRequestContext, Renderer, Result};
use crate::gfx::{
    GfxContext, RenderContextGfxExt, TextAnchor, draw_line, image_to_jpeg, resize_lanczos,
};
use async_trait::async_trait;
use image::{RgbaImage, imageops};
use serde_json::Value;

use crate::renderers::common::{MEDIUM, label, number, render_error, rgba, rows, string};

#[derive(Clone, Copy)]
pub(crate) enum Guess {
    Chart,
    Jacket,
    Card,
    Lyrics,
}

#[async_trait]
impl Renderer for Guess {
    fn name(&self) -> &'static str {
        match self {
            Self::Chart => "guess_chart",
            Self::Jacket => "guess_jacket",
            Self::Card => "guess_card",
            Self::Lyrics => "guess_lyrics",
        }
    }

    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput> {
        let pjsk_type = number(&payload["pjsk_type"]) as i32;
        let root = ctx
            .app
            .paths
            .ondemand_dir
            .join(ctx.app.server_name(pjsk_type));
        if matches!(self, Self::Chart) {
            let id = number(&payload["music_id"]);
            let path = root.join(format!("charts/moe/{id}/master.jpg"));
            if !tokio::fs::try_exists(path).await? {
                crate::renderers::mappreview::ensure_moe_chart(ctx, id, "master", false, pjsk_type)
                    .await?;
            }
        }
        let gfx = ctx.gfx()?;
        let kind = *self;
        tokio::task::spawn_blocking(move || kind.cut(&root, &gfx, &payload))
            .await
            .map_err(render_error)?
    }
}

impl Guess {
    fn cut(&self, root: &Path, gfx: &GfxContext, payload: &Value) -> Result<RenderOutput> {
        let tip = payload["is_tip"].as_bool().unwrap_or_default();
        if matches!(self, Self::Chart) {
            let id = number(&payload["music_id"]);
            let path = root.join(format!("charts/moe/{id}/master.jpg"));
            if !path.try_exists()? {
                return Err(DrawError::Render(
                    "moe 谱面底图不存在，需先生成谱面预览".into(),
                ));
            }
            return cut_chart(open(&path)?, gfx, tip);
        }

        let asset = string(&payload["asset"]);
        if asset.is_empty() || asset == "." || asset == ".." || asset.contains(['/', '\\']) {
            return Err(DrawError::InvalidPayload("invalid guess asset".into()));
        }
        let card = matches!(self, Self::Card);
        let path = if card {
            let trained = payload["rarity_type"] != "rarity_birthday" && sample(2) == 1;
            let filename = if trained {
                "card_after_training.png"
            } else {
                "card_normal.png"
            };
            root.join(format!("startapp/character/member/{asset}/{filename}"))
        } else {
            root.join(format!("startapp/music/jacket/{asset}/{asset}.png"))
        };
        let image = open(&path)?;
        if matches!(self, Self::Lyrics) {
            return cut_lyrics(image, gfx, payload);
        }

        let size = if payload["size"].is_null() {
            if card { 250 } else { 140 }
        } else {
            u32::try_from(number(&payload["size"])).unwrap_or_default()
        };
        cut_square(
            image,
            gfx,
            size,
            payload["is_bw"].as_bool().unwrap_or_default(),
            tip,
        )
    }
}

fn open(path: &Path) -> Result<RgbaImage> {
    image::open(path)
        .map(|img| image::DynamicImage::ImageRgb8(img.into_rgb8()).into_rgba8())
        .map_err(|error| render_error(format!("{}: {error}", path.display())))
}

fn sample(len: u32) -> u32 {
    let random = RandomState::new();
    // Rejection sampling preserves a uniform distribution for any crop range.
    let limit = u64::MAX - u64::MAX % u64::from(len);
    for attempt in 0_u64.. {
        let value = random.hash_one(attempt);
        if value < limit {
            return (value % u64::from(len)) as u32;
        }
    }
    unreachable!()
}

fn jpeg(image: &RgbaImage, gfx: &GfxContext, quality: u8) -> Result<Vec<u8>> {
    image_to_jpeg(image, quality, false, &gfx.fonts).map_err(render_error)
}

fn mark(image: &mut RgbaImage, (x, y, w, h): (u32, u32, u32, u32), width: i32) {
    let (left, top) = (x as i32 - width, y as i32 - width);
    let (right, bottom) = ((x + w) as i32 + width, (y + h) as i32 + width);
    let corners = [
        (left, top),
        (right, top),
        (right, bottom),
        (left, bottom),
        (left, top),
    ];
    for edge in corners.windows(2) {
        let point = |(x, y)| (x as f32, y as f32);
        draw_line(
            image,
            point(edge[0]),
            point(edge[1]),
            rgba([255, 0, 0], 255),
            width as f32,
        );
    }
}

fn cut_square(
    mut image: RgbaImage,
    gfx: &GfxContext,
    size: u32,
    bw: bool,
    tip: bool,
) -> Result<RenderOutput> {
    if size == 0 || size > image.width().min(image.height()) {
        return Err(DrawError::InvalidPayload(
            "guess crop size is outside the image".into(),
        ));
    }
    let x = sample(image.width() - size + 1);
    let y = sample(image.height() - size + 1);
    let mut crop = imageops::crop_imm(&image, x, y, size, size).to_image();
    if bw {
        crop = image::DynamicImage::ImageLuma8(imageops::grayscale(&crop)).into_rgba8();
    }
    let mut images = vec![jpeg(&crop, gfx, 60)?];
    if !tip {
        mark(&mut image, (x, y, size, size), 3);
        images.push(jpeg(&image, gfx, 50)?);
    }
    Ok(RenderOutput::multi(images))
}

fn cut_chart(mut image: RgbaImage, gfx: &GfxContext, tip: bool) -> Result<RenderOutput> {
    let (width, height) = image.dimensions();
    let columns = ((f64::from(width) - 93.254) / 280.8).round() as u32;
    if height < 383 || columns < if tip { 2 } else { 3 } {
        return Err(DrawError::Render("moe 谱面底图尺寸不足".into()));
    }
    let x = |column| (94.0 + 280.8 * f64::from(column)) as u32;
    let strip_h = height - 343;
    let strip = |column| {
        if x(column) + 190 > width {
            return Err(DrawError::Render("moe 谱面底图尺寸不足".into()));
        }
        Ok(imageops::crop_imm(&image, x(column), 48, 190, strip_h).to_image())
    };
    let white = rgba([255; 3], 255);
    if tip {
        let first = sample(columns);
        let second = sample(columns - 1);
        let second = second + u32::from(second >= first);
        let mut canvas = RgbaImage::from_pixel(410, height - 323, white);
        imageops::overlay(&mut canvas, &strip(second)?, 10, 10);
        imageops::overlay(&mut canvas, &strip(first)?, 210, 10);
        return Ok(RenderOutput::single(jpeg(&canvas, gfx, 60)?));
    }
    let column = sample(columns - 2) + 1;
    let strip = strip(column)?;
    let half = strip_h / 2;
    let upper = imageops::crop_imm(&strip, 0, 0, 190, half + 20);
    let lower = imageops::crop_imm(&strip, 0, half - 20, 190, strip_h - half + 20);
    let mut canvas = RgbaImage::from_pixel(410, height / 2 - 10, white);
    imageops::overlay(&mut canvas, &lower.to_image(), 10, 0);
    imageops::overlay(&mut canvas, &upper.to_image(), 210, -26);
    mark(&mut image, (x(column), 48, 190, strip_h), 5);
    Ok(RenderOutput::multi(vec![
        jpeg(&canvas, gfx, 60)?,
        jpeg(&image, gfx, 50)?,
    ]))
}

fn cut_lyrics(jacket: RgbaImage, gfx: &GfxContext, payload: &Value) -> Result<RenderOutput> {
    let lines = rows(&payload["lines"]);
    let index = usize::try_from(number(&payload["line_num"]))
        .ok()
        .filter(|&index| index < lines.len().saturating_sub(1))
        .ok_or_else(|| {
            DrawError::InvalidPayload("guess lyrics requires two selected lines".into())
        })?;
    let start = index.saturating_sub(6);
    let lines = &lines[start..(index + 6).min(lines.len())];
    let index = index - start;
    let text_w = lines
        .iter()
        .map(|line| gfx.fonts.measure_text(MEDIUM, 25.0, string(line)).0.ceil() as u32)
        .max()
        .unwrap_or_default()
        .max(
            25 * lines[index..index + 2]
                .iter()
                .map(|line| string(line).chars().count() as u32)
                .max()
                .unwrap_or_default()
                + 4,
        );
    let block_w = text_w + 20;
    let width = block_w.max(370);
    let line_h = 30;
    let mut canvas = RgbaImage::from_pixel(
        width + 20,
        420 + lines.len() as u32 * line_h,
        rgba([255; 3], 255),
    );
    let jacket = resize_lanczos(&jacket, 370, 370);
    imageops::overlay(&mut canvas, &jacket, i64::from(10 + (width - 370) / 2), 10);
    let x = 20 + (width - block_w) / 2;
    for (row, line) in lines.iter().enumerate() {
        label(
            gfx,
            &mut canvas,
            (x as i32, 400 + row as i32 * line_h as i32),
            string(line),
            (MEDIUM, 25.0, [0; 3], TextAnchor::Lt),
        );
    }
    let unmarked = if payload["is_tip"].as_bool() == Some(false) {
        Some(jpeg(&canvas, gfx, 60)?)
    } else {
        None
    };
    for (offset, line) in lines[index..index + 2].iter().enumerate() {
        let y = 397 + (index + offset + 1) as u32 * line_h;
        draw_line(
            &mut canvas,
            ((x - 3) as f32, y as f32),
            (
                (x + 25 * string(line).chars().count() as u32 + 3) as f32,
                y as f32,
            ),
            rgba([255, 0, 0], 255),
            3.0,
        );
    }
    let marked = jpeg(&canvas, gfx, 50)?;
    Ok(match unmarked {
        Some(question) => RenderOutput::multi(vec![question, marked]),
        None => RenderOutput::single(marked),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::renderers::common::RenderFixture;
    use image::GenericImageView;
    use serde_json::json;

    #[tokio::test]
    async fn offline_jacket_and_card_crops() {
        let fixture = RenderFixture::new();
        fixture.png(
            "ondemand/jp/startapp/music/jacket/jacket/jacket.png",
            (180, 160),
        );
        fixture.png(
            "ondemand/jp/startapp/character/member/card/card_normal.png",
            (300, 280),
        );
        let ctx = fixture.context();
        let jacket = Guess::Jacket
            .render(&ctx, json!({"asset":"jacket", "is_bw":true}))
            .await
            .unwrap();
        assert_eq!(jacket.images.len(), 2);
        assert_eq!(dimensions(&jacket.images[0]), (140, 140));
        assert_eq!(dimensions(&jacket.images[1]), (180, 160));
        let crop = image::load_from_memory(&jacket.images[0])
            .unwrap()
            .into_rgb8();
        let pixel = crop.get_pixel(70, 70);
        assert_eq!(pixel[0], pixel[1]);
        assert_eq!(pixel[1], pixel[2]);
        let card = Guess::Card
            .render(
                &ctx,
                json!({"asset":"card", "rarity_type":"rarity_birthday", "is_tip":true}),
            )
            .await
            .unwrap();
        assert_eq!(card.images.len(), 1);
        assert_eq!(dimensions(&card.images[0]), (250, 250));
    }

    #[tokio::test]
    async fn offline_lyrics_render_with_jacket_and_highlight() {
        let fixture = RenderFixture::new();
        fixture.png(
            "ondemand/jp/startapp/music/jacket/jacket/jacket.png",
            (100, 100),
        );
        let ctx = fixture.context();
        let mut payload = json!({"asset":"jacket", "lines":["one", "two", "three"], "line_num":1});
        let answer = Guess::Lyrics.render(&ctx, payload.clone()).await.unwrap();
        assert_eq!(answer.images.len(), 1);
        assert_eq!(dimensions(&answer.images[0]), (390, 510));
        let image = image::load_from_memory(&answer.images[0])
            .unwrap()
            .into_rgb8();
        assert!(
            image
                .enumerate_pixels()
                .any(|(_, y, p)| y >= 390 && p[0] > 160 && p[1] < 100)
        );
        payload["is_tip"] = json!(false);
        let pair = Guess::Lyrics.render(&ctx, payload).await.unwrap();
        assert_eq!(pair.images.len(), 2);
        assert_eq!(pair.images[1], answer.images[0]);
    }

    #[tokio::test]
    async fn chart_reports_missing_preview() {
        let fixture = RenderFixture::new();
        let result = Guess::Chart
            .render(&fixture.context(), json!({"music_id":42}))
            .await;
        assert!(
            matches!(result, Err(DrawError::Render(message)) if message == "moe 谱面底图不存在，需先生成谱面预览")
        );
    }

    #[tokio::test]
    async fn chart_generates_missing_moe_preview_before_cropping() {
        let fixture = RenderFixture::new();
        super::super::mappreview::tests::moe_fixture(&fixture);
        let ctx = fixture.context();
        let result = Guess::Chart
            .render(&ctx, json!({"music_id":42,"pjsk_type":1,"is_tip":true}))
            .await
            .unwrap();
        assert_eq!(result.images.len(), 1);
        assert_eq!(dimensions(&result.images[0]).0, 410);
        assert!(
            ctx.app
                .paths
                .ondemand_dir
                .join("tw/charts/moe/42/master.jpg")
                .is_file()
        );
    }

    fn dimensions(bytes: &[u8]) -> (u32, u32) {
        assert!(bytes.starts_with(b"\xff\xd8\xff"));
        image::load_from_memory(bytes).unwrap().dimensions()
    }
}
