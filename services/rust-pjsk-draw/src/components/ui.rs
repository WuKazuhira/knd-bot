use std::{path::Path, sync::Arc};

use crate::core::{AssetSourcePolicy, DrawError, RenderRequestContext, Result};
use crate::gfx::{GfxCache, paste_self_alpha, paste_with_l_mask, resize_lanczos};
use image::{Rgba, RgbaImage};

pub type ImageSize = (u32, u32);
pub type Point = (i32, i32);
// (left, top, right, bottom)，右下角开区间
pub type Bounds = (i32, i32, i32, i32);

pub(crate) fn paste_alpha(base: &mut RgbaImage, img: &RgbaImage, (x, y): Point) {
    paste_self_alpha(base, img, x, y);
}

pub(crate) fn open_image(
    gfx: &GfxCache,
    path: impl AsRef<Path>,
    size: Option<ImageSize>,
) -> Result<Arc<RgbaImage>> {
    gfx.open_pjsk_image(path, size)
        .map_err(|error| DrawError::Render(format!("{error:#}")))
}

pub fn draw_round_panel(
    gfx: &GfxCache,
    base: &mut RgbaImage,
    (x1, y1, x2, y2): Bounds,
    radius: u32,
    fill: Rgba<u8>,
    outline: Option<Rgba<u8>>,
    shadow: bool,
) {
    if x2 <= x1 || y2 <= y1 {
        return;
    }
    let size = ((x2 - x1) as u32, (y2 - y1) as u32);
    if shadow {
        let image = gfx.get_soft_shadow(size, radius, 48, 10, [70, 55, 90]);
        paste_alpha(base, &image, (x1 + 4, y1 + 6));
    }
    let panel = rounded_layer(gfx, size, radius, fill, outline);
    paste_alpha(base, &panel, (x1, y1));
}

// 带 1px 内缩描边的圆角图层
pub(crate) fn rounded_layer(
    gfx: &GfxCache,
    size: ImageSize,
    radius: u32,
    fill: Rgba<u8>,
    outline: Option<Rgba<u8>>,
) -> RgbaImage {
    let mask = gfx.get_rounded_rect_mask(size, radius);
    let color = outline.unwrap_or(fill);
    let mut layer = RgbaImage::from_fn(size.0, size.1, |x, y| {
        if mask.get_pixel(x, y)[0] == 0 {
            Rgba([0; 4])
        } else {
            color
        }
    });
    if outline.is_some() && size.0 > 2 && size.1 > 2 {
        let inner = gfx.get_rounded_rect_mask((size.0 - 2, size.1 - 2), radius.saturating_sub(1));
        for (x, y, coverage) in inner.enumerate_pixels() {
            if coverage[0] != 0 && mask.get_pixel(x + 1, y + 1)[0] != 0 {
                layer.put_pixel(x + 1, y + 1, fill);
            }
        }
    }
    layer
}

pub fn paste_round_image(
    gfx: &GfxCache,
    base: &mut RgbaImage,
    img: &RgbaImage,
    (x, y): Point,
    size: ImageSize,
    radius: u32,
) {
    if size.0 == 0 || size.1 == 0 {
        return;
    }
    let image = resize_lanczos(img, size.0, size.1);
    let mask = gfx.get_rounded_rect_mask(size, radius);
    paste_with_l_mask(base, &image, &mask, x, y);
}

pub async fn get_pjsk_asset_resized(
    ctx: &RenderRequestContext,
    category: &str,
    filename: &str,
    pjsk_type: i32,
    size: Option<ImageSize>,
) -> Result<Option<RgbaImage>> {
    let cache = ctx.app.extension::<crate::gfx::GfxContext>();
    let key = (ctx.policy != AssetSourcePolicy::LocalOnly && size.is_some())
        .then(|| format!("asset-rgba:{:?}", (pjsk_type, category, filename, size)));
    if let (Some(gfx), Some(key)) = (&cache, &key)
        && let Some(image) = gfx.cache.get_cached_render_image(key)
    {
        return Ok(Some((*image).clone()));
    }
    let image = ctx
        .app
        .get_asset_cached(category, filename, pjsk_type, ctx.policy, true)
        .await?;
    let Some(image) = image else {
        return Ok(None);
    };
    let resized = match size {
        Some((w, h)) if image.dimensions() != (w, h) => {
            tokio::task::spawn_blocking(move || resize_lanczos(&image, w, h))
                .await
                .map_err(|error| DrawError::Render(error.to_string()))?
        }
        _ => (*image).clone(),
    };
    if let (Some(gfx), Some(key)) = (&cache, key) {
        gfx.cache.put_cached_render_image(key, resized.clone());
    }
    Ok(Some(resized))
}

pub async fn get_pjsk_music_jacket_cached(
    ctx: &RenderRequestContext,
    music_id: i64,
    pjsk_type: i32,
    size: Option<ImageSize>,
    asset_name: Option<&str>,
    prefer_thumbnail: bool,
) -> Result<Option<RgbaImage>> {
    let default_name = format!("jacket_s_{music_id:03}");
    let name = asset_name
        .filter(|name| !name.is_empty())
        .unwrap_or(&default_name);
    let name = name.strip_suffix(".png").unwrap_or(name);
    let filename = format!("{name}.png");
    let full = format!("startapp/music/jacket/{name}");
    let mut categories = [full.as_str(), "startapp/thumbnail/music_jacket"];
    if prefer_thumbnail {
        categories.reverse();
    }
    let mut missing = None;
    for category in categories {
        match get_pjsk_asset_resized(ctx, category, &filename, pjsk_type, size).await {
            Ok(Some(image)) => return Ok(Some(image)),
            Ok(None) => {}
            Err(error @ DrawError::LocalOnlyAssetNotFound(_)) => missing = Some(error),
            Err(error) => return Err(error),
        }
    }
    missing.map_or(Ok(None), Err)
}
