use std::sync::Arc;

use crate::core::{DrawError, RenderOutput, RenderRequestContext, Renderer, Result};
use crate::gfx::{
    GfxContext, RenderContextGfxExt, TextAnchor, paste_with_l_mask, resize_lanczos,
    vertical_gradient,
};
use async_trait::async_trait;
use chrono::{DateTime, Local};
use image::{Rgba, RgbaImage};
use serde::Deserialize;
use serde_json::Value;
use tokio::task::JoinSet;

use crate::renderers::common::{BOLD, MEDIUM, null_default, parse_payload, render_png};
const PAD: u32 = 24;
const BANNER_SIZE: (u32, u32) = (200, 108);

pub(crate) struct NotifyRows;
pub(crate) struct VliveCards;

#[derive(Default, Deserialize)]
#[serde(default)]
struct Header {
    #[serde(deserialize_with = "null_default")]
    title: String,
    #[serde(deserialize_with = "null_default")]
    footer: String,
}

#[derive(Deserialize)]
struct RowsPayload {
    #[serde(flatten)]
    header: Header,
    #[serde(default, deserialize_with = "null_default")]
    rows: Vec<Vec<String>>,
}

#[derive(Deserialize)]
struct LivesPayload {
    #[serde(flatten)]
    header: Header,
    #[serde(default, deserialize_with = "null_default")]
    vlives: Vec<Vlive>,
    #[serde(default)]
    pjsk_type: i32,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Vlive {
    id: i64,
    #[serde(default)]
    name: String,
    assetbundle_name: Option<String>,
    #[serde(flatten)]
    timing: Schedule,
    #[serde(default, deserialize_with = "null_default")]
    virtual_live_schedules: Vec<Schedule>,
}

#[derive(Default, Deserialize)]
#[serde(default, rename_all = "camelCase")]
struct Schedule {
    start_at: i64,
    end_at: i64,
}

#[async_trait]
impl Renderer for NotifyRows {
    fn name(&self) -> &'static str {
        "notify_rows"
    }

    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput> {
        let payload = parse_payload(payload)?;
        render_png(ctx.gfx()?, move |gfx| draw_rows(gfx, payload)).await
    }
}

#[async_trait]
impl Renderer for VliveCards {
    fn name(&self) -> &'static str {
        "vlive_cards"
    }

    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput> {
        let payload: LivesPayload = parse_payload(payload)?;
        let gfx = ctx.gfx()?;
        let mut fetches = JoinSet::new();
        for (index, live) in payload.vlives.iter().enumerate() {
            let Some(asset) = live.assetbundle_name.clone().filter(|s| !s.is_empty()) else {
                continue;
            };
            let ctx = ctx.clone();
            let pjsk_type = payload.pjsk_type;
            fetches.spawn(async move {
                let path = format!("virtual_live/select/banner/{asset}");
                let file = format!("{asset}.png");
                let banner = match ctx
                    .app
                    .get_asset_cached(&path, &file, pjsk_type, ctx.policy, true)
                    .await
                {
                    Ok(banner) => banner,
                    Err(error) => {
                        tracing::debug!(%asset, %error, "Failed to fetch virtual live banner");
                        None
                    }
                };
                (index, banner)
            });
        }

        let mut banners = vec![None; payload.vlives.len()];
        while let Some(result) = fetches.join_next().await {
            let (index, banner) = result.map_err(|error| DrawError::Render(error.to_string()))?;
            banners[index] = banner;
        }
        render_png(gfx, move |gfx| draw_vlive_cards(gfx, payload, banners)).await
    }
}

struct Panel<'a> {
    image: RgbaImage,
    gfx: &'a GfxContext,
}

impl<'a> Panel<'a> {
    fn new(gfx: &'a GfxContext, width: u32, height: u32) -> Self {
        Self {
            image: vertical_gradient(width, height, [255, 235, 244], [232, 240, 255]),
            gfx,
        }
    }

    // 矩形坐标按闭区间算
    fn rect(&mut self, bounds: (u32, u32, u32, u32), radius: u32, rgb: [u8; 3]) {
        self.rounded_rect(bounds, radius, rgb, None);
    }

    fn rounded_rect(
        &mut self,
        (left, top, right, bottom): (u32, u32, u32, u32),
        radius: u32,
        rgb: [u8; 3],
        outline: Option<[u8; 3]>,
    ) {
        let mask = self
            .gfx
            .cache
            .get_rounded_rect_mask((right - left + 1, bottom - top + 1), radius);
        let fill = opaque(rgb);
        for (x, y, pixel) in mask.enumerate_pixels() {
            if pixel[0] == 0 {
                continue;
            }
            let color = match outline {
                Some(border)
                    if [
                        (x.wrapping_sub(1), y),
                        (x + 1, y),
                        (x, y.wrapping_sub(1)),
                        (x, y + 1),
                    ]
                    .into_iter()
                    .any(|(nx, ny)| mask.get_pixel_checked(nx, ny).is_none_or(|p| p[0] == 0)) =>
                {
                    opaque(border)
                }
                _ => fill,
            };
            self.image.put_pixel(left + x, top + y, color);
        }
    }

    fn text(
        &mut self,
        (x, y): (u32, u32),
        text: &str,
        (font, size): (&str, f32),
        rgb: [u8; 3],
        anchor: TextAnchor,
    ) {
        self.gfx.fonts.draw_text(
            &mut self.image,
            (x as f32, y as f32),
            text,
            font,
            size,
            opaque(rgb),
            anchor,
            None,
        );
    }

    fn footer(&mut self, footer: &str) {
        self.text(
            (PAD, self.image.height() - 24),
            footer,
            (MEDIUM, 13.0),
            [130, 110, 130],
            TextAnchor::La,
        );
    }
}

fn opaque([r, g, b]: [u8; 3]) -> Rgba<u8> {
    Rgba([r, g, b, 255])
}

fn draw_rows(gfx: &GfxContext, payload: RowsPayload) -> RgbaImage {
    let RowsPayload { header, rows } = payload;
    let (row_h, title_h) = (30, 56);
    let mut width =
        560.max(gfx.fonts.measure_text(BOLD, 24.0, &header.title).0 as u32 + PAD * 2 + 20);
    let mut height = title_h + PAD * 2 + if header.footer.is_empty() { 0 } else { 26 };
    for row in &rows {
        let longest = row
            .iter()
            .map(|line| gfx.fonts.measure_text(MEDIUM, 18.0, line).0 as u32)
            .max()
            .unwrap_or_default();
        width = width.max(longest + PAD * 2 + 24);
        height += row.len() as u32 * row_h + 16;
    }

    let mut panel = Panel::new(gfx, width, height);
    panel.rect(
        (PAD - 8, PAD - 8, width - PAD + 8, PAD + title_h - 12),
        14,
        [255; 3],
    );
    panel.text(
        (PAD + 4, PAD + 8),
        &header.title,
        (BOLD, 24.0),
        [60, 34, 60],
        TextAnchor::La,
    );
    let mut y = title_h + PAD + 8;
    for row in rows {
        let block_h = row.len() as u32 * row_h + 8;
        panel.rect(
            (PAD - 6, y - 6, width - PAD + 6, y + block_h - 4),
            12,
            [255; 3],
        );
        for line in row {
            panel.text(
                (PAD + 8, y),
                &line,
                (MEDIUM, 18.0),
                [50, 40, 56],
                TextAnchor::La,
            );
            y += row_h;
        }
        y += 16;
    }
    panel.footer(&header.footer);
    panel.image
}

fn fmt_ts(ms: i64) -> String {
    DateTime::from_timestamp_millis(ms)
        .map(|time| time.with_timezone(&Local).format("%m-%d %H:%M").to_string())
        .unwrap_or_default()
}

fn live_state(schedules: &[Schedule], now: i64) -> (String, usize) {
    let rest = schedules.iter().filter(|s| s.start_at > now).count();
    let state = match schedules.iter().find(|s| s.end_at > now) {
        Some(s) if s.start_at <= now => "当前Live进行中!".into(),
        Some(s) => format!("下一场: {}", fmt_ts(s.start_at)),
        None => "已无剩余场次".into(),
    };
    (state, rest)
}

fn draw_vlive_cards(
    gfx: &GfxContext,
    payload: LivesPayload,
    banners: Vec<Option<Arc<RgbaImage>>>,
) -> RgbaImage {
    let LivesPayload { header, vlives, .. } = payload;
    let (width, title_h, card_h, card_gap) = (760, 62, 132, 14);
    let height = PAD * 2
        + title_h
        + vlives.len() as u32 * (card_h + card_gap)
        + if header.footer.is_empty() { 0 } else { 26 };
    let mut panel = Panel::new(gfx, width, height);
    panel.rect(
        (PAD - 8, PAD - 6, width - PAD + 8, PAD + title_h - 16),
        16,
        [255; 3],
    );
    panel.rect((PAD + 4, PAD + 8, PAD + 10, PAD + 34), 3, [255, 128, 178]);
    panel.text(
        (PAD + 22, PAD + 8),
        &header.title,
        (BOLD, 26.0),
        [60, 34, 60],
        TextAnchor::La,
    );

    let now = Local::now().timestamp_millis();
    let mut y = PAD + title_h;
    for (live, banner) in vlives.into_iter().zip(banners) {
        panel.rect((PAD - 6, y, width - PAD + 6, y + card_h), 16, [255; 3]);
        let mut text_x = PAD + 8;
        if let Some(banner) = banner {
            let (w, h) = BANNER_SIZE;
            let (bx, by) = (PAD + 4, y + (card_h - h) / 2);
            let mut banner = resize_lanczos(&banner, w, h);
            // 盖图只看圆角遮罩，先把原图 alpha 拉满
            for pixel in banner.pixels_mut() {
                pixel[3] = 255;
            }
            let mask = gfx.cache.get_rounded_rect_mask(BANNER_SIZE, 12);
            paste_with_l_mask(&mut panel.image, &banner, &mask, bx as i32, by as i32);
            text_x = bx + w + 16;
        }

        let prefix = format!("【{}】", live.id);
        let mut label = format!("{prefix}{}", live.name);
        let max_w = (width - PAD - text_x - 12) as f32;
        while label.len() > prefix.len() && gfx.fonts.measure_text(BOLD, 20.0, &label).0 > max_w {
            label.pop();
        }
        panel.text(
            (text_x, y + 16),
            &label,
            (BOLD, 20.0),
            [48, 32, 56],
            TextAnchor::La,
        );
        let times = format!(
            "开始 {}  结束 {}",
            fmt_ts(live.timing.start_at),
            fmt_ts(live.timing.end_at)
        );
        panel.text(
            (text_x, y + 48),
            &times,
            (MEDIUM, 17.0),
            [96, 82, 104],
            TextAnchor::La,
        );
        let (state, rest) = live_state(&live.virtual_live_schedules, now);
        panel.text(
            (text_x, y + 76),
            &state,
            (MEDIUM, 17.0),
            [196, 72, 128],
            TextAnchor::La,
        );

        let rest_text = format!("剩余 {rest} 场");
        let rw = gfx.fonts.measure_text(MEDIUM, 13.0, &rest_text).0 as u32 + 22;
        let right = width - PAD - 4;
        let left = right - rw;
        panel.rounded_rect(
            (left, y + 74, right, y + 100),
            13,
            [255, 246, 251],
            Some([245, 218, 232]),
        );
        panel.text(
            (right - rw / 2, y + 87),
            &rest_text,
            (MEDIUM, 13.0),
            [150, 96, 126],
            TextAnchor::Mm,
        );
        y += card_h + card_gap;
    }
    panel.footer(&header.footer);
    panel.image
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::{
        AssetSourcePolicy, config::DrawConfig, context::AppContext, paths::DrawPaths,
    };
    use serde_json::json;

    fn offline_context() -> RenderRequestContext {
        let paths = DrawPaths::from_root(env!("CARGO_MANIFEST_DIR"));
        let mut config = DrawConfig::load(&paths).unwrap();
        config.helper_url.clear();
        let app = Arc::new(AppContext::new(paths, config).unwrap());
        app.insert_extension(Arc::new(GfxContext::load(&app.paths.font_dir).unwrap()));
        RenderRequestContext {
            app,
            policy: AssetSourcePolicy::Default,
        }
    }

    #[tokio::test]
    async fn notify_rows_produces_png() {
        let output = NotifyRows
            .render(
                &offline_context(),
                json!({"title": "New song", "rows": [["One", "Two"]], "footer": "PJSK"}),
            )
            .await
            .unwrap();
        let image = image::load_from_memory_with_format(&output.images[0], image::ImageFormat::Png)
            .unwrap();
        assert_eq!((image.width(), image.height()), (560, 206));
    }

    #[tokio::test]
    async fn vlive_cards_render_offline_without_banners() {
        let output = VliveCards
            .render(&offline_context(), json!({"title": "Virtual lives", "vlives": [
                {"id": 1, "name": "Live", "assetbundleName": "missing-notify-test-banner",
                 "startAt": 1000, "endAt": 2000, "virtualLiveSchedules": [{"startAt": 1000, "endAt": 2000}]}
            ]}))
            .await.unwrap();
        let image = image::load_from_memory_with_format(&output.images[0], image::ImageFormat::Png)
            .unwrap();
        assert_eq!((image.width(), image.height()), (760, 256));
        assert_eq!(image.into_rgba8().get_pixel(40, 98).0, [255; 4]);
    }
}
