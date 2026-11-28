use std::{
    fs::File,
    hash::Hash,
    io::BufReader,
    num::NonZeroUsize,
    path::{Path, PathBuf},
    sync::Arc,
    time::SystemTime,
};

use anyhow::{Context, Result};
use image::{GrayImage, ImageReader, Luma, Rgba, RgbaImage};
use lru::LruCache;
use parking_lot::Mutex;

use crate::gfx::{gaussian_blur_rgba, resize_lanczos};

type Size = (u32, u32);
type MaskKey = (Size, u32);
type ShadowKey = (Size, u32, u8, u32, [u8; 3]);
type ImageKey = (PathBuf, SystemTime, u64, Option<Size>);

const MAX_MASK_BYTES: usize = 16 << 20;
const MAX_SHADOW_BYTES: usize = 32 << 20;
const MAX_ASSET_BYTES: usize = 96 << 20;
const MAX_RENDER_IMAGE_BYTES: usize = 48 << 20;
const MAX_RENDER_BYTES: usize = 16 << 20;

struct ByteLru<K: Eq + Hash, V> {
    lru: LruCache<K, (V, usize)>,
    bytes: usize,
    max_bytes: usize,
}

impl<K: Eq + Hash, V> ByteLru<K, V> {
    fn new(max_items: usize, max_bytes: usize) -> Self {
        Self {
            lru: LruCache::new(NonZeroUsize::new(max_items).unwrap()),
            bytes: 0,
            max_bytes,
        }
    }

    fn get(&mut self, key: &K) -> Option<&V> {
        self.lru.get(key).map(|(value, _)| value)
    }

    fn put(&mut self, key: K, value: V, cost: usize) {
        if cost > self.max_bytes {
            return;
        }
        if let Some((_, old_cost)) = self.lru.pop(&key) {
            self.bytes = self.bytes.saturating_sub(old_cost);
        }
        if let Some((_, (_, evicted_cost))) = self.lru.push(key, (value, cost)) {
            self.bytes = self.bytes.saturating_sub(evicted_cost);
        }
        self.bytes = self.bytes.saturating_add(cost);
        while self.bytes > self.max_bytes {
            let Some((_, (_, evicted_cost))) = self.lru.pop_lru() else {
                self.bytes = 0;
                break;
            };
            self.bytes = self.bytes.saturating_sub(evicted_cost);
        }
    }

    fn clear(&mut self) {
        self.lru.clear();
        self.bytes = 0;
    }
}

pub struct GfxCache {
    masks: Mutex<ByteLru<MaskKey, Arc<GrayImage>>>,
    shadows: Mutex<ByteLru<ShadowKey, Arc<RgbaImage>>>,
    runtime: Mutex<RuntimeCaches>,
}

struct RuntimeCaches {
    assets: ByteLru<ImageKey, Arc<RgbaImage>>,
    render_images: ByteLru<String, Arc<RgbaImage>>,
    render_bytes: ByteLru<String, Arc<[u8]>>,
}

impl Default for GfxCache {
    fn default() -> Self {
        Self::new()
    }
}

impl GfxCache {
    pub fn new() -> Self {
        Self {
            masks: Mutex::new(ByteLru::new(128, MAX_MASK_BYTES)),
            shadows: Mutex::new(ByteLru::new(128, MAX_SHADOW_BYTES)),
            runtime: Mutex::new(RuntimeCaches {
                assets: ByteLru::new(512, MAX_ASSET_BYTES),
                render_images: ByteLru::new(64, MAX_RENDER_IMAGE_BYTES),
                render_bytes: ByteLru::new(64, MAX_RENDER_BYTES),
            }),
        }
    }

    pub fn get_rounded_rect_mask(&self, size: Size, radius: u32) -> Arc<GrayImage> {
        let key = (size, radius);
        let mut masks = self.masks.lock();
        if let Some(mask) = masks.get(&key) {
            return Arc::clone(mask);
        }
        let mask = Arc::new(rounded_mask(size, radius));
        masks.put(key, Arc::clone(&mask), mask.as_raw().len());
        mask
    }

    pub fn get_soft_shadow(
        &self,
        size: Size,
        radius: u32,
        alpha: u8,
        blur: u32,
        fill_rgb: [u8; 3],
    ) -> Arc<RgbaImage> {
        let key = (size, radius, alpha, blur, fill_rgb);
        if let Some(shadow) = self.shadows.lock().get(&key).cloned() {
            return shadow;
        }
        let mask = self.get_rounded_rect_mask(size, radius);
        let fill = Rgba([fill_rgb[0], fill_rgb[1], fill_rgb[2], alpha]);
        let image = RgbaImage::from_fn(size.0, size.1, |x, y| {
            if mask.get_pixel(x, y)[0] == 0 {
                Rgba([0; 4])
            } else {
                fill
            }
        });
        let shadow = Arc::new(gaussian_blur_rgba(&image, blur as f32));
        let cost = shadow.as_raw().len();
        self.shadows.lock().put(key, Arc::clone(&shadow), cost);
        shadow
    }

    pub fn open_pjsk_image(
        &self,
        path: impl AsRef<Path>,
        size: Option<Size>,
    ) -> Result<Arc<RgbaImage>> {
        let path = path.as_ref();
        let context = || format!("opening PJSK image {}", path.display());
        let file = File::open(path).with_context(context)?;
        let metadata = file.metadata().with_context(context)?;
        let key = (
            path.to_owned(),
            metadata.modified().with_context(context)?,
            metadata.len(),
            size,
        );
        if let Some(image) = self.runtime.lock().assets.get(&key).cloned() {
            return Ok(image);
        }
        // 锁外解码缩放，不卡缓存读写
        let mut image = ImageReader::new(BufReader::new(file))
            .with_guessed_format()
            .with_context(context)?
            .decode()
            .with_context(context)?
            .into_rgba8();
        if let Some((width, height)) = size
            && image.dimensions() != (width, height)
        {
            image = resize_lanczos(&image, width, height);
        }
        let image = Arc::new(image);
        let cost = image.as_raw().len();
        self.runtime.lock().assets.put(key, Arc::clone(&image), cost);
        Ok(image)
    }

    pub fn get_cached_render_image(&self, key: &str) -> Option<Arc<RgbaImage>> {
        self.runtime
            .lock()
            .render_images
            .get(&key.to_owned())
            .cloned()
    }

    pub fn put_cached_render_image(
        &self,
        key: impl Into<String>,
        image: impl Into<Arc<RgbaImage>>,
    ) {
        let image = image.into();
        let cost = image.as_raw().len();
        self.runtime
            .lock()
            .render_images
            .put(key.into(), image, cost);
    }

    pub fn get_cached_render_bytes(&self, key: &str) -> Option<Arc<[u8]>> {
        self.runtime
            .lock()
            .render_bytes
            .get(&key.to_owned())
            .cloned()
    }

    pub fn put_cached_render_bytes(&self, key: impl Into<String>, data: impl Into<Arc<[u8]>>) {
        let data = data.into();
        let cost = data.len();
        self.runtime
            .lock()
            .render_bytes
            .put(key.into(), data, cost);
    }

    pub fn clear_runtime_caches(&self) {
        let mut runtime = self.runtime.lock();
        runtime.assets.clear();
        runtime.render_images.clear();
        runtime.render_bytes.clear();
    }
}

fn rounded_mask((width, height): Size, radius: u32) -> GrayImage {
    if (width, height) == (1, 1) {
        return GrayImage::new(1, 1);
    }
    if width == 0 || height == 0 || radius == 0 {
        return GrayImage::from_pixel(width, height, Luma([255]));
    }
    let (right, bottom) = (width - 1, height - 1);
    let mut diameter = radius.saturating_mul(2);
    let joined_x = diameter >= right.saturating_sub(1);
    if joined_x {
        diameter = right;
    }
    let joined_y = diameter >= bottom.saturating_sub(1);
    if joined_y {
        diameter = bottom;
    }
    let (a, b) = if joined_x && joined_y {
        (right, bottom)
    } else {
        (diameter, diameter)
    };
    if a == 0 || b == 0 {
        return GrayImage::from_pixel(width, height, Luma([255]));
    }

    // 在 2 倍网格上算 1/4 椭圆外轮廓，每行首个边界点即该扫描线缩进
    let mut insets = vec![0; (b / 2 + 1) as usize];
    let (a, b) = (i64::from(a), i64::from(b));
    let (a2, b2) = (i128::from(a).pow(2), i128::from(b).pow(2));
    let error =
        |x: i64, y: i64| (a2 * i128::from(y).pow(2) + b2 * i128::from(x).pow(2) - a2 * b2).abs();
    let (mut x, mut y) = (a, b % 2);
    insets[((b - y) / 2) as usize] = 0;
    while y < b {
        let mut next = (x, y + 2);
        if x > 1 {
            for candidate in [(x - 2, y + 2), (x - 2, y)] {
                if error(candidate.0, candidate.1) < error(next.0, next.1) {
                    next = candidate;
                }
            }
        }
        if next.1 != y {
            insets[((b - next.1) / 2) as usize] = ((a - next.0) / 2) as u32;
        }
        (x, y) = next;
    }

    let mut mask = GrayImage::new(width, height);
    for (y, row) in mask.as_mut().chunks_exact_mut(width as usize).enumerate() {
        let distance = (y as u32).min(bottom - y as u32);
        let inset = insets.get(distance as usize).copied().unwrap_or(0) as usize;
        row[inset..width as usize - inset].fill(255);
    }
    mask
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rounded_mask_matches_pillow_and_is_cached() {
        let cache = GfxCache::new();
        let mask = cache.get_rounded_rect_mask((8, 6), 2);
        assert_eq!(
            mask.rows()
                .map(|row| row.filter(|p| p[0] == 255).count())
                .collect::<Vec<_>>(),
            [4, 6, 8, 8, 6, 4]
        );
        assert_eq!(cache.get_rounded_rect_mask((1, 1), 2).get_pixel(0, 0)[0], 0);
        assert!(Arc::ptr_eq(&mask, &cache.get_rounded_rect_mask((8, 6), 2)));
    }

    #[test]
    fn soft_shadow_blurs_rgba_and_is_cached() {
        let cache = GfxCache::new();
        let shadow = cache.get_soft_shadow((8, 6), 2, 80, 1, [70, 55, 90]);
        assert_eq!(*shadow.get_pixel(0, 0), Rgba([14, 11, 18, 17]));
        assert_eq!(*shadow.get_pixel(4, 3), Rgba([70, 55, 90, 80]));
        assert!(Arc::ptr_eq(
            &shadow,
            &cache.get_soft_shadow((8, 6), 2, 80, 1, [70, 55, 90])
        ));
    }
}
