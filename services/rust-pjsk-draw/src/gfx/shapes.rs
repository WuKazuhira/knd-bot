//! Raster drawing and compositing primitives.

use fast_image_resize::{FilterType, ResizeAlg, ResizeOptions, Resizer};
use image::{GrayImage, Rgba, RgbaImage};
use tiny_skia::{Paint, Path, PathBuilder, Pixmap, Stroke, Transform};

pub fn vertical_gradient(width: u32, height: u32, top: [u8; 3], bottom: [u8; 3]) -> RgbaImage {
    let (width, height) = (width.max(1), height.max(1));
    let mut image = RgbaImage::new(width, height);
    for (y, row) in image.rows_mut().enumerate() {
        let fraction = (2.0 * (y as f64 + 0.5) / f64::from(height) - 0.5).clamp(0.0, 1.0);
        let weight = (fraction * f64::from(1 << 22)).round() as u32;
        let mut rgb = [255; 4];
        for channel in 0..3 {
            rgb[channel] = ((u32::from(top[channel]) * ((1 << 22) - weight)
                + u32::from(bottom[channel]) * weight
                + (1 << 21))
                >> 22) as u8;
        }
        for pixel in row {
            *pixel = Rgba(rgb);
        }
    }
    image
}

/// Rectangle coordinates are (left, top, right, bottom). The outline is centered on the path.
pub fn draw_rounded_rect(
    canvas: &mut RgbaImage,
    (left, top, right, bottom): (f32, f32, f32, f32),
    radius: f32,
    fill: Option<Rgba<u8>>,
    outline: Option<(Rgba<u8>, f32)>,
) {
    if right <= left || bottom <= top {
        return;
    }
    let radius = radius.max(0.0).min((right - left).min(bottom - top) / 2.0);
    let control = radius * (1.0 - 0.552_284_8);
    let mut path = PathBuilder::new();
    path.move_to(left + radius, top);
    path.line_to(right - radius, top);
    path.cubic_to(
        right - control,
        top,
        right,
        top + control,
        right,
        top + radius,
    );
    path.line_to(right, bottom - radius);
    path.cubic_to(
        right,
        bottom - control,
        right - control,
        bottom,
        right - radius,
        bottom,
    );
    path.line_to(left + radius, bottom);
    path.cubic_to(
        left + control,
        bottom,
        left,
        bottom - control,
        left,
        bottom - radius,
    );
    path.line_to(left, top + radius);
    path.cubic_to(left, top + control, left + control, top, left + radius, top);
    path.close();
    if let Some(path) = path.finish() {
        paint_path(canvas, &path, fill, outline);
    }
}

pub fn draw_line(
    canvas: &mut RgbaImage,
    p1: (f32, f32),
    p2: (f32, f32),
    color: Rgba<u8>,
    width: f32,
) {
    let mut path = PathBuilder::new();
    path.move_to(p1.0, p1.1);
    path.line_to(p2.0, p2.1);
    if let Some(path) = path.finish() {
        paint_path(canvas, &path, None, Some((color, width)));
    }
}

fn paint_path(
    canvas: &mut RgbaImage,
    path: &Path,
    fill: Option<Rgba<u8>>,
    outline: Option<(Rgba<u8>, f32)>,
) {
    let outline = outline.filter(|(_, width)| width.is_finite() && *width > 0.0);
    let padding = outline.map_or(1.0, |(_, width)| 1.0 + width * 2.0);
    let bounds = path.bounds();
    let left = (bounds.left() - padding).floor().max(0.0) as u32;
    let top = (bounds.top() - padding).floor().max(0.0) as u32;
    let right = ((bounds.right() + padding).ceil() as u32).min(canvas.width());
    let bottom = ((bounds.bottom() + padding).ceil() as u32).min(canvas.height());
    let Some(mut coverage) = Pixmap::new(right.saturating_sub(left), bottom.saturating_sub(top))
    else {
        return;
    };
    let transform = Transform::from_translate(-(left as f32), -(top as f32));
    let mut paint = Paint::default();
    paint.set_color_rgba8(255, 255, 255, 255);
    if let Some(color) = fill {
        coverage.fill_path(path, &paint, tiny_skia::FillRule::Winding, transform, None);
        composite_coverage(canvas, &coverage, left, top, color);
    }
    if let Some((color, width)) = outline {
        coverage.fill(tiny_skia::Color::TRANSPARENT);
        let stroke = Stroke {
            width,
            ..Stroke::default()
        };
        coverage.stroke_path(path, &paint, &stroke, transform, None);
        composite_coverage(canvas, &coverage, left, top, color);
    }
}

fn composite_coverage(
    canvas: &mut RgbaImage,
    coverage: &Pixmap,
    left: u32,
    top: u32,
    color: Rgba<u8>,
) {
    for (index, sample) in coverage.pixels().iter().enumerate() {
        if sample.alpha() != 0 {
            let mut source = color;
            source[3] = interpolate(0, color[3], sample.alpha());
            let index = index as u32;
            blend_over(
                canvas.get_pixel_mut(
                    left + index % coverage.width(),
                    top + index / coverage.width(),
                ),
                source,
            );
        }
    }
}

pub fn resize_lanczos(img: &RgbaImage, width: u32, height: u32) -> RgbaImage {
    if img.dimensions() == (width, height) {
        return img.clone();
    }
    let mut resized = RgbaImage::new(width, height);
    if !img.is_empty() && !resized.is_empty() {
        let options = ResizeOptions::new().resize_alg(ResizeAlg::Convolution(FilterType::Lanczos3));
        Resizer::new()
            .resize(img, &mut resized, &options)
            .expect("source and destination are both RGBA8");
    }
    resized
}

pub(crate) fn interpolate(dst: u8, src: u8, alpha: u8) -> u8 {
    let alpha = u32::from(alpha);
    ((u32::from(dst) * (255 - alpha) + u32::from(src) * alpha + 127) / 255) as u8
}

pub(crate) fn blend_over(dst: &mut Rgba<u8>, src: Rgba<u8>) {
    let alpha = u32::from(src[3]);
    if alpha == 0 {
        return;
    }
    let src_weight = alpha * 255;
    let dst_weight = u32::from(dst[3]) * (255 - alpha);
    let total = src_weight + dst_weight;
    for channel in 0..3 {
        dst[channel] = ((u32::from(src[channel]) * src_weight
            + u32::from(dst[channel]) * dst_weight
            + total / 2)
            / total) as u8;
    }
    dst[3] = ((total + 127) / 255) as u8;
}

/// Composites the source over the destination using the source alpha.
pub fn paste_rgba(dst: &mut RgbaImage, src: &RgbaImage, x: i32, y: i32) {
    let dst_w = dst.width();
    let dst_h = dst.height();
    let src_w = src.width();
    let src_h = src.height();
    let (x, y) = (i64::from(x), i64::from(y));
    let start_x = x.max(0);
    let start_y = y.max(0);
    let end_x = (x + i64::from(src_w)).min(i64::from(dst_w));
    let end_y = (y + i64::from(src_h)).min(i64::from(dst_h));
    if start_x >= end_x || start_y >= end_y {
        return;
    }
    let copy_w = (end_x - start_x) as usize;
    let sx0 = (start_x - x) as usize;
    let sy0 = (start_y - y) as usize;
    let dst_stride = dst_w as usize * 4;
    let src_stride = src_w as usize * 4;
    let dst_raw = dst.as_mut();
    let src_raw = src.as_raw();
    for dy in start_y as usize..end_y as usize {
        let sy = sy0 + (dy - start_y as usize);
        let d_off = dy * dst_stride + start_x as usize * 4;
        let s_off = sy * src_stride + sx0 * 4;
        let d_row = &mut dst_raw[d_off..d_off + copy_w * 4];
        let s_row = &src_raw[s_off..s_off + copy_w * 4];
        for (d_px, s_px) in d_row.chunks_exact_mut(4).zip(s_row.chunks_exact(4)) {
            if s_px[3] == 0 {
                continue;
            }
            if s_px[3] == 255 {
                d_px.copy_from_slice(s_px);
                continue;
            }
            let mut dst_px = Rgba([d_px[0], d_px[1], d_px[2], d_px[3]]);
            blend_over(&mut dst_px, Rgba([s_px[0], s_px[1], s_px[2], s_px[3]]));
            d_px.copy_from_slice(&dst_px.0);
        }
    }
}

/// Pillow-equivalent paste using the source's own alpha channel as mask (linear 4-channel blend).
pub fn paste_self_alpha(dst: &mut RgbaImage, src: &RgbaImage, x: i32, y: i32) {
    paste_slices(dst, src, None, x, y, src.dimensions());
}

/// Pillow L-mask paste interpolates all four channels using the mask coverage.
pub fn paste_with_l_mask(
    dst: &mut RgbaImage,
    src: &RgbaImage,
    mask_l8: &GrayImage,
    x: i32,
    y: i32,
) {
    let size = (
        src.width().min(mask_l8.width()),
        src.height().min(mask_l8.height()),
    );
    paste_slices(dst, src, Some(mask_l8), x, y, size);
}

fn paste_slices(
    dst: &mut RgbaImage,
    src: &RgbaImage,
    mask_l8: Option<&GrayImage>,
    x: i32,
    y: i32,
    (width, height): (u32, u32),
) {
    let dst_w = dst.width();
    let dst_h = dst.height();
    let (x, y) = (i64::from(x), i64::from(y));
    let start_x = x.max(0);
    let start_y = y.max(0);
    let end_x = (x + i64::from(width)).min(i64::from(dst_w));
    let end_y = (y + i64::from(height)).min(i64::from(dst_h));
    if start_x >= end_x || start_y >= end_y {
        return;
    }
    let copy_w = (end_x - start_x) as usize;
    let sx0 = (start_x - x) as usize;
    let sy0 = (start_y - y) as usize;
    let dst_stride = dst_w as usize * 4;
    let src_stride = src.width() as usize * 4;
    let dst_raw = dst.as_mut();
    let src_raw = src.as_raw();
    match mask_l8 {
        Some(mask) => {
            let mask_stride = mask.width() as usize;
            let mask_raw = mask.as_raw();
            for dy in start_y as usize..end_y as usize {
                let sy = sy0 + (dy - start_y as usize);
                let d_off = dy * dst_stride + start_x as usize * 4;
                let s_off = sy * src_stride + sx0 * 4;
                let m_off = sy * mask_stride + sx0;
                let d_row = &mut dst_raw[d_off..d_off + copy_w * 4];
                let s_row = &src_raw[s_off..s_off + copy_w * 4];
                let m_row = &mask_raw[m_off..m_off + copy_w];
                for ((d_px, s_px), &cov) in d_row
                    .chunks_exact_mut(4)
                    .zip(s_row.chunks_exact(4))
                    .zip(m_row)
                {
                    if cov == 0 {
                        continue;
                    }
                    if cov == 255 {
                        d_px.copy_from_slice(s_px);
                        continue;
                    }
                    for channel in 0..4 {
                        d_px[channel] = interpolate(d_px[channel], s_px[channel], cov);
                    }
                }
            }
        }
        None => {
            for dy in start_y as usize..end_y as usize {
                let sy = sy0 + (dy - start_y as usize);
                let d_off = dy * dst_stride + start_x as usize * 4;
                let s_off = sy * src_stride + sx0 * 4;
                let d_row = &mut dst_raw[d_off..d_off + copy_w * 4];
                let s_row = &src_raw[s_off..s_off + copy_w * 4];
                for (d_px, s_px) in d_row.chunks_exact_mut(4).zip(s_row.chunks_exact(4)) {
                    let cov = s_px[3];
                    if cov == 0 {
                        continue;
                    }
                    if cov == 255 {
                        d_px.copy_from_slice(s_px);
                        continue;
                    }
                    for channel in 0..4 {
                        d_px[channel] = interpolate(d_px[channel], s_px[channel], cov);
                    }
                }
            }
        }
    }
}

/// Pillow-style Gaussian blur uses three extended box filters along each axis.
pub fn gaussian_blur_rgba(img: &RgbaImage, radius: f32) -> RgbaImage {
    if img.is_empty() || !radius.is_finite() || radius <= 0.0 {
        return img.clone();
    }

    let sigma_squared = radius * radius / 3.0;
    let length = (12.0 * f64::from(sigma_squared) + 1.0).sqrt() as f32;
    let whole = ((f64::from(length) - 1.0) / 2.0).floor() as f32;
    let fraction = (2.0 * whole + 1.0) * (whole * (whole + 1.0) - 3.0 * sigma_squared)
        / (6.0 * (sigma_squared - (whole + 1.0) * (whole + 1.0)));
    let box_radius = whole + fraction;
    let fixed_one = 1_u64 << 24;
    let inner = (fixed_one as f32 / (2.0 * box_radius + 1.0)) as u64;
    let reach = (box_radius as i64).clamp(0, i64::from(i32::MAX)) as usize;
    let outer = fixed_one.saturating_sub((2 * reach as u64 + 1) * inner) / 2;
    let (width, height) = (img.width() as usize, img.height() as usize);
    let mut source = img.clone();
    let mut result = RgbaImage::new(width as u32, height as u32);
    for _ in 0..3 {
        blur_axis(
            source.as_raw(),
            result.as_mut(),
            height,
            width,
            4,
            width * 4,
            reach,
            inner,
            outer,
            fixed_one,
        );
        std::mem::swap(&mut source, &mut result);
    }
    for _ in 0..3 {
        blur_axis(
            source.as_raw(),
            result.as_mut(),
            width,
            height,
            width * 4,
            4,
            reach,
            inner,
            outer,
            fixed_one,
        );
        std::mem::swap(&mut source, &mut result);
    }
    source
}

#[allow(clippy::too_many_arguments)]
fn blur_axis(
    src: &[u8],
    dst: &mut [u8],
    lines: usize,
    len: usize,
    step: usize,
    line_stride: usize,
    reach: usize,
    inner: u64,
    outer: u64,
    fixed_one: u64,
) {
    let last = len - 1;
    let read_px = |base: usize, pos: isize| -> [u64; 4] {
        let clamped = pos.clamp(0, last as isize) as usize;
        let off = base + clamped * step;
        [
            u64::from(src[off]),
            u64::from(src[off + 1]),
            u64::from(src[off + 2]),
            u64::from(src[off + 3]),
        ]
    };
    let fast = len > 2 * reach + 2;
    for line in 0..lines {
        let base = line * line_stride;
        let first_px = read_px(base, 0);
        let last_px = read_px(base, last as isize);
        let mut sum = first_px.map(|v| v * (reach as u64 + 1));
        for pos in 1..=reach.min(last) {
            let px = read_px(base, pos as isize);
            for c in 0..4 {
                sum[c] += px[c];
            }
        }
        if reach > last {
            let extra = (reach - last) as u64;
            for c in 0..4 {
                sum[c] += last_px[c] * extra;
            }
        }
        if fast {
            for pos in 0..=reach {
                let before = read_px(base, pos as isize - reach as isize - 1);
                let after = read_px(base, (pos + reach + 1) as isize);
                let entering = read_px(base, pos as isize - reach as isize);
                let out = base + pos * step;
                for c in 0..4 {
                    let w = sum[c] * inner + (before[c] + after[c]) * outer;
                    sum[c] = sum[c] + after[c] - entering[c];
                    dst[out + c] = ((w + fixed_one / 2) >> 24) as u8;
                }
            }
            for pos in (reach + 1)..(last - reach) {
                let before_off = base + (pos - reach - 1) * step;
                let after_off = base + (pos + reach + 1) * step;
                let enter_off = base + (pos - reach) * step;
                let out = base + pos * step;
                for c in 0..4 {
                    let after = u64::from(src[after_off + c]);
                    let w = sum[c] * inner + (u64::from(src[before_off + c]) + after) * outer;
                    sum[c] = sum[c] + after - u64::from(src[enter_off + c]);
                    dst[out + c] = ((w + fixed_one / 2) >> 24) as u8;
                }
            }
            for pos in (last - reach)..=last {
                let before = read_px(base, pos as isize - reach as isize - 1);
                let after = read_px(base, (pos + reach + 1) as isize);
                let entering = read_px(base, pos as isize - reach as isize);
                let out = base + pos * step;
                for c in 0..4 {
                    let w = sum[c] * inner + (before[c] + after[c]) * outer;
                    sum[c] = sum[c] + after[c] - entering[c];
                    dst[out + c] = ((w + fixed_one / 2) >> 24) as u8;
                }
            }
        } else {
            for pos in 0..=last {
                let before = read_px(base, pos as isize - reach as isize - 1);
                let after = read_px(base, (pos + reach + 1) as isize);
                let entering = read_px(base, pos as isize - reach as isize);
                let out = base + pos * step;
                for c in 0..4 {
                    let w = sum[c] * inner + (before[c] + after[c]) * outer;
                    sum[c] = sum[c] + after[c] - entering[c];
                    dst[out + c] = ((w + fixed_one / 2) >> 24) as u8;
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::Luma;

    #[test]
    fn gradient_matches_strip_sampling() {
        let img = vertical_gradient(2, 4, [0; 3], [255; 3]);
        assert_eq!(
            img.rows()
                .map(|mut row| row.next().unwrap()[0])
                .collect::<Vec<_>>(),
            [0, 64, 191, 255]
        );
        assert_eq!(
            *vertical_gradient(0, 0, [0; 3], [255; 3]).get_pixel(0, 0),
            Rgba([128, 128, 128, 255])
        );
    }

    #[test]
    fn paste_clips_negative_coordinates() {
        let mut dst = RgbaImage::from_pixel(2, 2, Rgba([0, 0, 255, 255]));
        let mut src = RgbaImage::new(2, 2);
        src.put_pixel(1, 1, Rgba([255, 0, 0, 128]));
        paste_rgba(&mut dst, &src, -1, -1);
        assert_eq!(*dst.get_pixel(0, 0), Rgba([128, 0, 127, 255]));
        assert_eq!(*dst.get_pixel(1, 1), Rgba([0, 0, 255, 255]));
        let mask = GrayImage::from_pixel(2, 2, Luma([128]));
        paste_with_l_mask(&mut dst, &src, &mask, -1, -1);
        assert_eq!(*dst.get_pixel(0, 0), Rgba([192, 0, 63, 191]));
    }
}
