use std::sync::Arc;

use crate::components::ui::{Bounds, draw_round_panel};
use crate::core::{DrawError, RenderOutput, Result};
use crate::gfx::{
    FontRegistry, GfxContext, ImageEncodingOptions, ImageFormat, encode_owned_image_bytes,
    vertical_gradient,
};
use image::{Rgba, RgbaImage};
use serde::{Deserialize, Deserializer, de::DeserializeOwned};
use serde_json::Value;

pub(crate) const BOLD: &str = "SourceHanSansCN-Bold.otf";
pub(crate) const MEDIUM: &str = "SourceHanSansCN-Medium.otf";
pub(crate) const RODIN: &str = "FOT-RodinNTLGPro-DB.ttf";

pub(crate) fn parse_payload<T: DeserializeOwned>(payload: Value) -> Result<T> {
    serde_json::from_value(payload).map_err(|error| DrawError::InvalidPayload(error.to_string()))
}

pub(crate) fn null_default<'de, D: Deserializer<'de>, T: Deserialize<'de> + Default>(
    deserializer: D,
) -> std::result::Result<T, D::Error> {
    Ok(Option::deserialize(deserializer)?.unwrap_or_default())
}

pub(crate) async fn render_png(
    gfx: Arc<GfxContext>,
    draw: impl FnOnce(&GfxContext) -> RgbaImage + Send + 'static,
) -> Result<RenderOutput> {
    tokio::task::spawn_blocking(move || {
        encode(encode_owned_image_bytes(
            draw(&gfx),
            ImageEncodingOptions {
                format: ImageFormat::Png,
                quality: 88,
                watermark: true,
            },
            &gfx.fonts,
        ))
    })
    .await
    .map_err(render_error)?
}

pub(crate) async fn render_jpeg(
    gfx: Arc<GfxContext>,
    quality: u8,
    watermark: bool,
    draw: impl FnOnce(&GfxContext) -> RgbaImage + Send + 'static,
) -> Result<RenderOutput> {
    tokio::task::spawn_blocking(move || {
        encode(encode_owned_image_bytes(
            draw(&gfx),
            ImageEncodingOptions {
                format: ImageFormat::Jpeg,
                quality,
                watermark,
            },
            &gfx.fonts,
        ))
    })
    .await
    .map_err(render_error)?
}

fn encode(result: anyhow::Result<Vec<u8>>) -> Result<RenderOutput> {
    result.map(RenderOutput::single).map_err(render_error)
}

pub(crate) fn render_error(error: impl std::fmt::Display) -> DrawError {
    DrawError::Render(error.to_string())
}

pub(crate) struct SoftGlow {
    pub center: (f32, f32),
    pub radii: (f32, f32),
    pub color: Rgba<u8>,
}

pub(crate) fn make_style_gradient_bg(
    (width, height): (u32, u32),
    top: [u8; 3],
    bottom: [u8; 3],
    glows: &[SoftGlow],
) -> RgbaImage {
    let mut image = vertical_gradient(width, height, top, bottom);
    let stride = width as usize * 4;
    let raw = image.as_mut();
    for glow in glows {
        let (cx, cy) = glow.center;
        let (rx, ry) = glow.radii;
        if rx <= 0.0 || ry <= 0.0 {
            continue;
        }
        let inv_rx = 1.0 / rx;
        let inv_ry = 1.0 / ry;
        let max_alpha = f32::from(glow.color[3]) / 255.0;
        let x0 = (cx - rx).max(0.0) as u32;
        let x1 = ((cx + rx).ceil() as u32).min(width);
        let y0 = (cy - ry).max(0.0) as u32;
        let y1 = ((cy + ry).ceil() as u32).min(height);
        let [gr, gg, gb] = [
            f32::from(glow.color[0]),
            f32::from(glow.color[1]),
            f32::from(glow.color[2]),
        ];
        for y in y0..y1 {
            let dy_sq = ((y as f32 - cy) * inv_ry).powi(2);
            if dy_sq >= 1.0 {
                continue;
            }
            let row = &mut raw
                [y as usize * stride + x0 as usize * 4..y as usize * stride + x1 as usize * 4];
            for (dx, px) in row.chunks_exact_mut(4).enumerate() {
                let x = x0 as f32 + dx as f32;
                let dist = ((x - cx) * inv_rx).powi(2) + dy_sq;
                if dist < 1.0 {
                    let alpha = (1.0 - dist).powi(2) * max_alpha;
                    let inv = 1.0 - alpha;
                    px[0] = (f32::from(px[0]) * inv + gr * alpha).round() as u8;
                    px[1] = (f32::from(px[1]) * inv + gg * alpha).round() as u8;
                    px[2] = (f32::from(px[2]) * inv + gb * alpha).round() as u8;
                }
            }
        }
    }
    image
}

// 矩形坐标按闭区间（含右下角）算
pub(crate) fn rounded_panel_overlay(
    gfx: &GfxContext,
    image: &mut RgbaImage,
    (left, top, right, bottom): Bounds,
    radius: u32,
    fill: Rgba<u8>,
    outline: Option<Rgba<u8>>,
) {
    draw_round_panel(
        &gfx.cache,
        image,
        (left, top, right + 1, bottom + 1),
        radius,
        fill,
        outline,
        false,
    );
}

pub(crate) fn draw_stat_bar(
    gfx: &GfxContext,
    image: &mut RgbaImage,
    (left, top, right, bottom): Bounds,
    ratio: f64,
    color: Rgba<u8>,
) {
    let radius = ((bottom - top) / 2) as u32;
    rounded_panel_overlay(
        gfx,
        image,
        (left, top, right, bottom),
        radius,
        Rgba([238, 234, 246, 255]),
        None,
    );
    if ratio > 0.0 {
        let end = left + (f64::from(right - left) * ratio.min(1.0)) as i32;
        rounded_panel_overlay(gfx, image, (left, top, end, bottom), radius, color, None);
    }
}

pub(crate) fn fit_text(
    fonts: &FontRegistry,
    text: &str,
    font: &str,
    size: f32,
    max_width: f32,
) -> String {
    if fonts.measure_text(font, size, text).0 <= max_width {
        return text.to_owned();
    }
    if fonts.measure_text(font, size, "…").0 > max_width {
        return String::new();
    }
    let mut fitted = String::from("…");
    let boundaries: Vec<_> = text.char_indices().map(|(index, _)| index).collect();
    let index = boundaries.partition_point(|&end| {
        fitted.clear();
        fitted.push_str(&text[..end]);
        fitted.push('…');
        fonts.measure_text(font, size, &fitted).0 <= max_width
    });
    format!("{}…", &text[..boundaries[index.saturating_sub(1)]])
}

#[cfg(test)]
pub(crate) fn offline_context() -> crate::core::RenderRequestContext {
    use crate::core::{
        AssetSourcePolicy, RenderRequestContext, config::DrawConfig, context::AppContext,
        paths::DrawPaths,
    };

    let paths = DrawPaths::from_root(env!("CARGO_MANIFEST_DIR"));
    let mut config = DrawConfig::load(&paths).unwrap();
    config.helper_url.clear();
    let app = Arc::new(AppContext::new(paths, config).unwrap());
    app.insert_extension(Arc::new(GfxContext::load(&app.paths.font_dir).unwrap()));
    RenderRequestContext {
        app,
        policy: AssetSourcePolicy::LocalOnly,
    }
}

pub(crate) fn deserialize_ids<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<Vec<i64>, D::Error> {
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum Id {
        Integer(i64),
        Text(String),
    }

    let ids: Vec<Id> = null_default(deserializer)?;
    ids.into_iter()
        .map(|id| match id {
            Id::Integer(id) => Ok(id),
            Id::Text(id) => id.trim().parse().map_err(serde::de::Error::custom),
        })
        .collect()
}

pub(crate) fn number(value: &Value) -> i64 {
    value
        .as_i64()
        .or_else(|| value.as_str()?.trim().parse().ok())
        .unwrap_or_default()
}

pub(crate) fn string(value: &Value) -> &str {
    value.as_str().unwrap_or_default()
}

pub(crate) fn rows(value: &Value) -> &[Value] {
    value.as_array().map(Vec::as_slice).unwrap_or_default()
}

pub(crate) fn rgba(rgb: [u8; 3], alpha: u8) -> Rgba<u8> {
    Rgba([rgb[0], rgb[1], rgb[2], alpha])
}

pub(crate) fn circle_avatar(source: &RgbaImage, size: u32, color: Rgba<u8>) -> RgbaImage {
    use crate::gfx::{paste_rgba, resize_lanczos};
    let mut image = RgbaImage::from_fn(size, size, |x, y| {
        let radius = size as f32 / 2.0;
        let distance = (x as f32 + 0.5 - radius).hypot(y as f32 + 0.5 - radius);
        if distance > radius {
            Rgba([0; 4])
        } else if distance > radius - 4.0 {
            color
        } else {
            Rgba([255; 4])
        }
    });
    if !source.is_empty() {
        let (x, y, w, h) = alpha_bounds(source, 0);
        let crop = image::imageops::crop_imm(source, x, y, w, h).to_image();
        let scale = f64::from(size.saturating_sub(12).max(1)) / f64::from(w.max(h));
        let icon = resize_lanczos(
            &crop,
            (w as f64 * scale).max(1.0) as u32,
            (h as f64 * scale).max(1.0) as u32,
        );
        paste_rgba(
            &mut image,
            &icon,
            ((size - icon.width()) / 2) as i32,
            ((size - icon.height()) / 2) as i32,
        );
    }
    image
}

pub(crate) fn alpha_bounds(image: &RgbaImage, threshold: u8) -> (u32, u32, u32, u32) {
    let (mut left, mut top, mut right, mut bottom) = (image.width(), image.height(), 0, 0);
    for (x, y, pixel) in image.enumerate_pixels() {
        if pixel[3] > threshold {
            left = left.min(x);
            top = top.min(y);
            right = right.max(x + 1);
            bottom = bottom.max(y + 1);
        }
    }
    if right > left {
        (left, top, right - left, bottom - top)
    } else if threshold > 0 {
        alpha_bounds(image, 0)
    } else {
        (0, 0, image.width(), image.height())
    }
}

pub(crate) fn character_portrait(
    gfx: &GfxContext,
    static_dir: &std::path::Path,
    id: i64,
) -> Option<Arc<RgbaImage>> {
    const NAMES: [&str; 26] = [
        "ick", "saki", "hnm", "shiho", "mnr", "hrk", "airi", "szk", "khn", "an", "akt", "toya",
        "tks", "emu", "nene", "rui", "knd", "mfy", "ena", "mzk", "miku", "rin", "len", "luka",
        "meiko", "kaito",
    ];
    let name = NAMES.get(usize::try_from(id).ok()?.checked_sub(1)?)?;
    gfx.cache
        .open_pjsk_image(
            static_dir.join(format!("chara/chara_icon/{name}.png")),
            None,
        )
        .ok()
}

pub(crate) fn shanghai_time(ms: i64) -> String {
    chrono::DateTime::from_timestamp_millis(ms)
        .map(|time| {
            time.with_timezone(&chrono_tz::Asia::Shanghai)
                .format("%Y/%m/%d %H:%M:%S")
                .to_string()
        })
        .unwrap_or_default()
}

#[cfg(test)]
pub(crate) struct RenderFixture(std::path::PathBuf);

#[cfg(test)]
impl RenderFixture {
    pub(crate) fn new() -> Self {
        use std::sync::atomic::{AtomicUsize, Ordering};

        static NEXT: AtomicUsize = AtomicUsize::new(0);
        let dir = std::env::temp_dir().join(format!(
            "pjsk-draw-renderers-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&dir).unwrap();
        Self(dir)
    }

    pub(crate) fn write(&self, path: &str, bytes: impl AsRef<[u8]>) -> std::path::PathBuf {
        let path = self.0.join(path);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, bytes).unwrap();
        path
    }

    pub(crate) fn png(&self, path: &str, size: (u32, u32)) {
        let mut bytes = std::io::Cursor::new(Vec::new());
        RgbaImage::from_pixel(size.0, size.1, rgba([128, 64, 192], 255))
            .write_to(&mut bytes, image::ImageFormat::Png)
            .unwrap();
        self.write(path, bytes.into_inner());
    }

    pub(crate) fn context(&self) -> crate::core::RenderRequestContext {
        use crate::core::{
            AssetSourcePolicy, RenderRequestContext, config::DrawConfig, context::AppContext,
            paths::DrawPaths,
        };

        let mut paths = DrawPaths::from_root(&self.0);
        paths.static_dir = self.0.join("static");
        paths.ondemand_dir = self.0.join("ondemand");
        let mut config = DrawConfig::load(&paths).unwrap();
        config.helper_url.clear();
        let app = Arc::new(AppContext::new(paths, config).unwrap());
        app.insert_extension(Arc::new(GfxContext::load(&app.paths.font_dir).unwrap()));
        RenderRequestContext {
            app,
            policy: AssetSourcePolicy::Default,
        }
    }
}

#[cfg(test)]
impl Drop for RenderFixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

pub(crate) const DIFFICULTIES: [(&str, [u8; 3]); 6] = [
    ("easy", [102, 221, 17]),
    ("normal", [51, 187, 238]),
    ("hard", [254, 170, 0]),
    ("expert", [238, 67, 102]),
    ("master", [187, 51, 238]),
    ("append", [255, 75, 75]),
];

pub(crate) fn card_assets(cards: &Value) -> std::collections::HashMap<i64, String> {
    crate::components::normalize_master_list(cards)
        .into_iter()
        .filter_map(|card| {
            Some((
                card["id"].as_i64()?,
                string(&card["assetbundleName"]).to_owned(),
            ))
        })
        .collect()
}

pub(crate) fn decimal(value: &Value) -> f64 {
    value
        .as_f64()
        .or_else(|| value.as_str()?.trim().parse().ok())
        .filter(|value| value.is_finite())
        .unwrap_or_default()
}

pub(crate) fn label(
    gfx: &GfxContext,
    image: &mut RgbaImage,
    (x, y): (i32, i32),
    text: &str,
    (font, size, color, anchor): (&str, f32, [u8; 3], crate::gfx::TextAnchor),
) {
    gfx.fonts.draw_text(
        image,
        (x as f32, y as f32),
        text,
        font,
        size,
        rgba(color, 255),
        anchor,
        None,
    );
}

pub(crate) async fn load_ordered<T, F>(
    tasks: impl IntoIterator<Item = F>,
    limit: usize,
) -> Result<Vec<T>>
where
    T: Send + 'static,
    F: Future<Output = T> + Send + 'static,
{
    let mut pending = tasks.into_iter().enumerate();
    let mut active = tokio::task::JoinSet::new();
    for (index, future) in pending.by_ref().take(limit) {
        active.spawn(async move { (index, future.await) });
    }
    let mut completed = Vec::new();
    while let Some(task) = active.join_next().await {
        completed.push(task.map_err(render_error)?);
        if let Some((index, future)) = pending.next() {
            active.spawn(async move { (index, future.await) });
        }
    }
    completed.sort_unstable_by_key(|(index, _)| *index);
    Ok(completed.into_iter().map(|(_, value)| value).collect())
}
