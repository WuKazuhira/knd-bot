//! PNG and JPEG encoding with optional KNDBOT attribution.

use anyhow::Result;
use image::{
    ExtendedColorType, ImageEncoder, Rgba, RgbaImage,
    codecs::{
        jpeg::JpegEncoder,
        png::{CompressionType, FilterType, PngEncoder},
    },
};

use crate::gfx::{FontRegistry, PJSK_WATERMARK_TEXT, TextAnchor, shapes::interpolate};

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum ImageFormat {
    #[default]
    Png,
    Jpeg,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ImageEncodingOptions {
    pub format: ImageFormat,
    pub quality: u8,
    pub watermark: bool,
}

impl Default for ImageEncodingOptions {
    fn default() -> Self {
        Self {
            format: ImageFormat::Png,
            quality: 88,
            watermark: true,
        }
    }
}

pub fn add_kndbot_watermark_in_place(img: &mut RgbaImage, font_registry: &FontRegistry) {
    let min_side = img.width().min(img.height());
    if min_side < 120 {
        return;
    }

    const RODIN: &str = "FOT-RodinNTLGPro-DB.ttf";
    const SOURCE_HAN: &str = "SourceHanSansCN-Medium.otf";
    let font = if font_registry.has_font(RODIN) {
        RODIN
    } else {
        SOURCE_HAN
    };
    let size = ((u64::from(min_side) * 18 / 1000).clamp(10, 18)) as f32;
    let pad = (u64::from(min_side) * 12 / 1000).max(10) as f32;
    let (width, height) = font_registry.measure_text(font, size, PJSK_WATERMARK_TEXT);
    let x = (img.width() as f32 - width - pad).max(pad);
    let y = (img.height() as f32 - height - pad).max(pad);
    font_registry.draw_text(
        img,
        (x + 1.0, y + 1.0),
        PJSK_WATERMARK_TEXT,
        font,
        size,
        Rgba([255; 4]),
        TextAnchor::La,
        None,
    );
    font_registry.draw_text(
        img,
        (x, y),
        PJSK_WATERMARK_TEXT,
        font,
        size,
        Rgba([80, 80, 80, 255]),
        TextAnchor::La,
        None,
    );
}

pub fn add_kndbot_watermark(img: &RgbaImage, font_registry: &FontRegistry) -> RgbaImage {
    let mut result = img.clone();
    add_kndbot_watermark_in_place(&mut result, font_registry);
    result
}

pub fn encode_image_bytes(
    img: &RgbaImage,
    options: ImageEncodingOptions,
    font_registry: &FontRegistry,
) -> Result<Vec<u8>> {
    let watermarked;
    let img = if options.watermark && img.width().min(img.height()) >= 120 {
        watermarked = add_kndbot_watermark(img, font_registry);
        &watermarked
    } else {
        img
    };
    encode_raw(img, options)
}

pub fn encode_owned_image_bytes(
    mut img: RgbaImage,
    options: ImageEncodingOptions,
    font_registry: &FontRegistry,
) -> Result<Vec<u8>> {
    if options.watermark {
        add_kndbot_watermark_in_place(&mut img, font_registry);
    }
    encode_raw(&img, options)
}

fn encode_raw(img: &RgbaImage, options: ImageEncodingOptions) -> Result<Vec<u8>> {
    let mut output = Vec::new();
    match options.format {
        ImageFormat::Png => {
            PngEncoder::new_with_quality(&mut output, CompressionType::Fast, FilterType::Adaptive)
                .write_image(
                    img.as_raw(),
                    img.width(),
                    img.height(),
                    ExtendedColorType::Rgba8,
                )?;
        }
        ImageFormat::Jpeg => {
            let (width, height) = img.dimensions();
            let mut rgb = vec![0_u8; width as usize * height as usize * 3];
            for (dst, src) in rgb.chunks_exact_mut(3).zip(img.as_raw().chunks_exact(4)) {
                match src[3] {
                    255 => {
                        dst[0] = src[0];
                        dst[1] = src[1];
                        dst[2] = src[2];
                    }
                    0 => dst.fill(255),
                    alpha => {
                        dst[0] = interpolate(255, src[0], alpha);
                        dst[1] = interpolate(255, src[1], alpha);
                        dst[2] = interpolate(255, src[2], alpha);
                    }
                }
            }
            JpegEncoder::new_with_quality(&mut output, options.quality).encode(
                &rgb,
                width,
                height,
                ExtendedColorType::Rgb8,
            )?;
        }
    }
    Ok(output)
}

pub fn image_to_png(
    img: &RgbaImage,
    watermark: bool,
    font_registry: &FontRegistry,
) -> Result<Vec<u8>> {
    encode_image_bytes(
        img,
        ImageEncodingOptions {
            watermark,
            ..ImageEncodingOptions::default()
        },
        font_registry,
    )
}

pub fn image_to_jpeg(
    img: &RgbaImage,
    quality: u8,
    watermark: bool,
    font_registry: &FontRegistry,
) -> Result<Vec<u8>> {
    encode_image_bytes(
        img,
        ImageEncodingOptions {
            format: ImageFormat::Jpeg,
            quality,
            watermark,
        },
        font_registry,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::Rgb;

    #[test]
    fn png_preserves_rgba_and_jpeg_composites_over_white() {
        let fonts = FontRegistry::new("").unwrap();
        let img = RgbaImage::from_pixel(8, 8, Rgba([0, 0, 0, 128]));
        let png = image_to_png(&img, false, &fonts).unwrap();
        assert!(png.starts_with(b"\x89PNG\r\n\x1a\n"));
        assert_eq!(image::load_from_memory(&png).unwrap().into_rgba8(), img);
        let jpeg = image_to_jpeg(&img, 95, false, &fonts).unwrap();
        assert!(jpeg.starts_with(b"\xff\xd8\xff"));
        assert_eq!(
            *image::load_from_memory(&jpeg)
                .unwrap()
                .into_rgb8()
                .get_pixel(0, 0),
            Rgb([127; 3])
        );
    }

    #[test]
    fn watermark_skips_small_images_and_draws_in_bottom_right() {
        let fonts = FontRegistry::new("").unwrap();
        let small = RgbaImage::new(240, 119);
        assert_eq!(add_kndbot_watermark(&small, &fonts), small);
        let image = RgbaImage::new(320, 120);
        let marked = add_kndbot_watermark(&image, &fonts);
        let changed: Vec<_> = marked
            .enumerate_pixels()
            .filter(|(_, _, p)| p[3] != 0)
            .collect();
        assert!(!changed.is_empty());
        assert!(changed.iter().all(|(x, y, _)| *x >= 10 && *y >= 100));
    }
}
