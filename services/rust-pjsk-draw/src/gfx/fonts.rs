use std::{
    collections::HashMap,
    fs,
    ops::{Deref, DerefMut},
    path::{Path, PathBuf},
    sync::{Arc, OnceLock},
};

use anyhow::{Context, Result};
use cosmic_text::{
    Attrs, AttrsOwned, Buffer, Color, Family, FontSystem, Metrics, Shaping, SwashCache, Wrap,
    fontdb,
};
use image::{Rgba, RgbaImage};
use parking_lot::Mutex;

use crate::gfx::shapes::{blend_over, interpolate};

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum TextAnchor {
    #[default]
    La,
    Lm,
    Lt,
    Lb,
    Ma,
    Mm,
    Mt,
    Mb,
    Ra,
    Rm,
    Rt,
    Rb,
}

pub struct FontRegistry {
    font_dir: PathBuf,
    svg_fonts: OnceLock<Arc<resvg::usvg::fontdb::Database>>,
    base_db: fontdb::Database,
    faces: HashMap<String, NamedFace>,
    fallback: Option<NamedFace>,
    pool: Mutex<Vec<FontState>>,
}

#[derive(Clone)]
struct NamedFace {
    attrs: AttrsOwned,
    id: fontdb::ID,
}

impl NamedFace {
    fn new(face: &fontdb::FaceInfo, family: &str) -> Self {
        Self {
            attrs: AttrsOwned::new(
                Attrs::new()
                    .family(Family::Name(family))
                    .weight(face.weight)
                    .stretch(face.stretch)
                    .style(face.style),
            ),
            id: face.id,
        }
    }
}

struct FontState {
    system: FontSystem,
    cache: SwashCache,
}

struct FontGuard<'a> {
    registry: &'a FontRegistry,
    state: Option<FontState>,
}

impl Deref for FontGuard<'_> {
    type Target = FontState;

    fn deref(&self) -> &Self::Target {
        self.state.as_ref().expect("font state checked out")
    }
}

impl DerefMut for FontGuard<'_> {
    fn deref_mut(&mut self) -> &mut Self::Target {
        self.state.as_mut().expect("font state checked out")
    }
}

impl Drop for FontGuard<'_> {
    fn drop(&mut self) {
        if std::thread::panicking() {
            return;
        }
        if let Some(state) = self.state.take() {
            let mut pool = self.registry.pool.lock();
            if pool.len() < 16 {
                pool.push(state);
            }
        }
    }
}

impl FontRegistry {
    pub fn new(font_dir: impl AsRef<Path>) -> Result<Self> {
        let font_dir = font_dir.as_ref();
        let mut db = fontdb::Database::new();
        db.load_system_fonts();
        let mut faces = HashMap::new();
        let mut paths = match fs::read_dir(font_dir) {
            Ok(entries) => entries
                .map(|entry| entry.map(|entry| entry.path()))
                .collect::<std::io::Result<Vec<_>>>()?,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Vec::new(),
            Err(error) => {
                return Err(error).with_context(|| format!("reading {}", font_dir.display()));
            }
        };
        paths.sort();
        for path in paths {
            let Some(filename) = path.file_name().and_then(|name| name.to_str()) else {
                continue;
            };
            let Some(ext) = path.extension().and_then(|ext| ext.to_str()) else {
                continue;
            };
            if !matches!(
                ext.to_ascii_lowercase().as_str(),
                "ttf" | "otf" | "ttc" | "otc"
            ) {
                continue;
            }
            let data = match fs::read(&path) {
                Ok(data) => data,
                Err(error) => {
                    tracing::warn!(path = %path.display(), %error, "could not read font");
                    continue;
                }
            };
            let ids = db.load_font_source(fontdb::Source::Binary(Arc::new(data)));
            if let Some(mut face) = ids.first().and_then(|id| db.face(*id)).cloned() {
                // 加私有别名，防止系统里有同名同字重字体时被抢走
                let alias = format!("pjsk-draw/{filename}");
                db.remove_face(face.id);
                face.families
                    .push((alias.clone(), fontdb::Language::English_UnitedStates));
                db.push_face_info(face);
                if let Some(face) = db
                    .faces()
                    .find(|face| face.families.iter().any(|(name, _)| name == &alias))
                {
                    faces.insert(filename.to_owned(), NamedFace::new(face, &alias));
                }
            } else {
                tracing::warn!(path = %path.display(), "could not parse font");
            }
        }
        let fallback = faces
            .get("SourceHanSansCN-Medium.otf")
            .cloned()
            .or_else(|| {
                let query = fontdb::Query {
                    families: &[
                        Family::Name("DejaVu Sans"),
                        Family::Name("Arial"),
                        Family::SansSerif,
                    ],
                    ..fontdb::Query::default()
                };
                db.query(&query)
                    .and_then(|id| db.face(id))
                    .or_else(|| {
                        db.faces()
                            .find(|face| !face.post_script_name.contains("Emoji"))
                    })
                    .and_then(|face| {
                        face.families
                            .first()
                            .map(|(name, _)| NamedFace::new(face, name))
                    })
            });
        let initial = FontState {
            system: FontSystem::new_with_locale_and_db("en-US".to_owned(), db.clone()),
            cache: SwashCache::new(),
        };
        Ok(Self {
            font_dir: font_dir.to_owned(),
            svg_fonts: OnceLock::new(),
            base_db: db,
            faces,
            fallback,
            pool: Mutex::new(vec![initial]),
        })
    }

    fn checkout(&self) -> FontGuard<'_> {
        let state = self.pool.lock().pop().unwrap_or_else(|| FontState {
            system: FontSystem::new_with_locale_and_db("en-US".to_owned(), self.base_db.clone()),
            cache: SwashCache::new(),
        });
        FontGuard {
            registry: self,
            state: Some(state),
        }
    }

    pub fn svg_fonts(&self) -> Arc<resvg::usvg::fontdb::Database> {
        Arc::clone(self.svg_fonts.get_or_init(|| {
            let mut fonts = resvg::usvg::fontdb::Database::new();
            fonts.load_system_fonts();
            fonts.load_fonts_dir(&self.font_dir);
            fonts.set_sans_serif_family("DejaVu Sans");
            Arc::new(fonts)
        }))
    }

    pub fn has_font(&self, font_name: &str) -> bool {
        self.faces.contains_key(font_name)
    }

    fn face(&self, font_name: &str) -> Option<&NamedFace> {
        self.faces.get(font_name).or(self.fallback.as_ref())
    }

    pub fn measure_text(&self, font_name: &str, size: f32, text: &str) -> (f32, f32) {
        if !valid_size(size) || text.is_empty() {
            return (0.0, 0.0);
        }
        let mut state = self.checkout();
        let buffer = shape(&mut state.system, self.face(font_name), size, text);
        let bounds = state.bounds(&buffer);
        (bounds.right - bounds.left, bounds.bottom - bounds.top)
    }

    pub fn fit_text_ellipsis(
        &self,
        font_name: &str,
        size: f32,
        text: &str,
        max_width: f32,
    ) -> String {
        if !valid_size(size) {
            return text.to_owned();
        }
        let mut state = self.checkout();
        let face = self.face(font_name);
        let mut width = |text: &str| {
            shape(&mut state.system, face, size, text)
                .layout_runs()
                .map(|run| run.line_w)
                .fold(0.0, f32::max)
        };
        if width(text) <= max_width {
            return text.to_owned();
        }
        if width("…") > max_width {
            return "…".to_owned();
        }
        let boundaries: Vec<_> = text.char_indices().map(|(index, _)| index).collect();
        let mut candidate = String::with_capacity(text.len() + '…'.len_utf8());
        let index = boundaries.partition_point(|&end| {
            candidate.clear();
            candidate.push_str(&text[..end]);
            candidate.push('…');
            width(&candidate) <= max_width
        });
        format!("{}…", &text[..boundaries[index.saturating_sub(1)]])
    }

    #[allow(clippy::too_many_arguments)]
    pub fn draw_text(
        &self,
        canvas: &mut RgbaImage,
        pos: (f32, f32),
        text: &str,
        font_name: &str,
        size: f32,
        color: Rgba<u8>,
        anchor: TextAnchor,
        stroke: Option<(u32, Rgba<u8>)>,
    ) {
        if !valid_size(size)
            || text.is_empty()
            || canvas.is_empty()
            || !pos.0.is_finite()
            || !pos.1.is_finite()
        {
            return;
        }
        let face = self.face(font_name);
        let mut state = self.checkout();
        let buffer = shape(&mut state.system, face, size, text);
        let bounds = state.bounds(&buffer);
        let baseline = buffer
            .layout_runs()
            .next()
            .map_or(0.0, |run| run.line_y.round());
        let (ascent, descent) =
            face.and_then(|face| state.system.get_font(face.id))
                .map_or((baseline, 0.0), |font| {
                    let metrics = font.as_swash().metrics(&[]).scale(size);
                    (metrics.ascent, metrics.descent)
                });
        let horizontal = match anchor {
            TextAnchor::La | TextAnchor::Lm | TextAnchor::Lt | TextAnchor::Lb => 0.0,
            TextAnchor::Ma | TextAnchor::Mm | TextAnchor::Mt | TextAnchor::Mb => {
                bounds.advance / 2.0
            }
            TextAnchor::Ra | TextAnchor::Rm | TextAnchor::Rt | TextAnchor::Rb => bounds.advance,
        };
        let vertical = match anchor {
            TextAnchor::La | TextAnchor::Ma | TextAnchor::Ra => baseline - ascent,
            TextAnchor::Lm | TextAnchor::Mm | TextAnchor::Rm => baseline + (descent - ascent) / 2.0,
            TextAnchor::Lt | TextAnchor::Mt | TextAnchor::Rt => bounds.top,
            TextAnchor::Lb | TextAnchor::Mb | TextAnchor::Rb => bounds.bottom,
        };
        let offset = (pos.0 - horizontal, pos.1 - vertical);
        let base = Color::rgb(color[0], color[1], color[2]);
        let stroke = stroke.filter(|(radius, color)| *radius > 0 && color[3] > 0);
        let FontState { system, cache } = &mut *state;
        let mut pixels = Vec::new();
        for run in buffer.layout_runs() {
            for glyph in run.glyphs {
                let physical = glyph.physical((offset.0, offset.1 + run.line_y.round()), 1.0);
                cache.with_pixels(system, physical.cache_key, base, |x, y, sample| {
                    let (x, y) = (
                        i64::from(physical.x) + i64::from(x),
                        i64::from(physical.y) + i64::from(y),
                    );
                    if sample.a() == 0 {
                        return;
                    }
                    let mut rgba = Rgba([sample.r(), sample.g(), sample.b(), sample.a()]);
                    if stroke.is_some() {
                        pixels.push((x, y, rgba));
                    } else if let Some(dest) = visible_pixel(canvas, x, y) {
                        rgba[3] = interpolate(0, color[3], rgba[3]);
                        blend_over(dest, rgba);
                    }
                });
            }
        }
        drop(state);
        if let Some((radius, stroke_color)) = stroke {
            draw_stroke(canvas, &pixels, radius, stroke_color);
            for (x, y, mut rgba) in pixels {
                if let Some(dest) = visible_pixel(canvas, x, y) {
                    rgba[3] = interpolate(0, color[3], rgba[3]);
                    blend_over(dest, rgba);
                }
            }
        }
    }
}

fn valid_size(size: f32) -> bool {
    size.is_finite() && size > 0.0
}

fn shape(system: &mut FontSystem, face: Option<&NamedFace>, size: f32, text: &str) -> Buffer {
    let mut buffer = Buffer::new(system, Metrics::new(size, (size * 1.25).max(1.0)));
    buffer.set_wrap(system, Wrap::None);
    buffer.set_text(
        system,
        text,
        face.map_or_else(Attrs::new, |face| face.attrs.as_attrs()),
        Shaping::Advanced,
    );
    buffer
}

#[derive(Default)]
struct Bounds {
    advance: f32,
    left: f32,
    right: f32,
    top: f32,
    bottom: f32,
}

impl FontState {
    fn bounds(&mut self, buffer: &Buffer) -> Bounds {
        let mut bounds = Bounds {
            top: f32::INFINITY,
            bottom: f32::NEG_INFINITY,
            ..Bounds::default()
        };
        for run in buffer.layout_runs() {
            bounds.advance = bounds.advance.max(run.line_w);
            bounds.right = bounds.right.max(run.line_w.ceil());
            for glyph in run.glyphs {
                let physical = glyph.physical((0.0, run.line_y.round()), 1.0);
                if let Some(image) = self.cache.get_image(&mut self.system, physical.cache_key)
                    && image.placement.width > 0
                    && image.placement.height > 0
                {
                    let left = physical.x as f32 + image.placement.left as f32;
                    let top = physical.y as f32 - image.placement.top as f32;
                    bounds.left = bounds.left.min(left);
                    bounds.right = bounds.right.max(left + image.placement.width as f32);
                    bounds.top = bounds.top.min(top);
                    bounds.bottom = bounds.bottom.max(top + image.placement.height as f32);
                }
            }
        }
        if !bounds.top.is_finite() {
            bounds.top = 0.0;
            bounds.bottom = 0.0;
        }
        bounds
    }
}

fn visible_pixel(canvas: &mut RgbaImage, x: i64, y: i64) -> Option<&mut Rgba<u8>> {
    canvas.get_pixel_mut_checked(u32::try_from(x).ok()?, u32::try_from(y).ok()?)
}

fn draw_stroke(
    canvas: &mut RgbaImage,
    pixels: &[(i64, i64, Rgba<u8>)],
    radius: u32,
    mut color: Rgba<u8>,
) {
    if pixels.is_empty() {
        return;
    }
    let radius = i64::from(radius);
    let (width, height) = (i64::from(canvas.width()), i64::from(canvas.height()));
    let (mut min_x, mut max_x, mut min_y, mut max_y) = (width, -1_i64, height, -1_i64);
    for &(x, y, _) in pixels {
        min_x = min_x.min((x - radius).max(0));
        max_x = max_x.max((x + radius).min(width - 1));
        min_y = min_y.min((y - radius).max(0));
        max_y = max_y.max((y + radius).min(height - 1));
    }
    if min_x > max_x || min_y > max_y {
        return;
    }
    let box_w = (max_x - min_x + 1) as usize;
    let box_h = (max_y - min_y + 1) as usize;
    let mut coverage = vec![0_u8; box_w * box_h];
    let extents: Vec<i64> = (-radius..=radius)
        .map(|dy| ((radius as f64).powi(2) - (dy as f64).powi(2)).sqrt() as i64)
        .collect();
    for &(x, y, sample) in pixels {
        let a = sample[3];
        let y_start = (-radius).max(min_y - y);
        let y_end = radius.min(max_y - y);
        for dy in y_start..=y_end {
            let extent = extents[(dy + radius) as usize];
            let x_start = (x - extent).max(min_x);
            let x_end = (x + extent).min(max_x);
            if x_start <= x_end {
                let row = ((y + dy - min_y) as usize) * box_w;
                for dst in
                    &mut coverage[row + (x_start - min_x) as usize..=row + (x_end - min_x) as usize]
                {
                    if a > *dst {
                        *dst = a;
                    }
                }
            }
        }
    }
    let alpha = color[3];
    let raw = canvas.as_mut();
    let stride = width as usize * 4;
    for ry in 0..box_h {
        let cy = min_y as usize + ry;
        let cov_row = &coverage[ry * box_w..(ry + 1) * box_w];
        let dst_row =
            &mut raw[cy * stride + min_x as usize * 4..cy * stride + (max_x as usize + 1) * 4];
        for (rx, &cov) in cov_row.iter().enumerate() {
            if cov != 0 {
                color[3] = interpolate(0, alpha, cov);
                let px = &mut dst_row[rx * 4..rx * 4 + 4];
                let mut dst = Rgba([px[0], px[1], px[2], px[3]]);
                blend_over(&mut dst, color);
                px.copy_from_slice(&dst.0);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn measures_and_truncates_text() {
        let fonts = FontRegistry::new("").unwrap();
        assert_eq!(fonts.measure_text("", 20.0, ""), (0.0, 0.0));
        let (width, height) = fonts.measure_text("", 20.0, "Hello");
        assert!(width > 0.0 && height > 0.0);
        assert!(fonts.measure_text("", 30.0, "Hello").0 > width);
        assert_eq!(fonts.fit_text_ellipsis("", 20.0, "Hello", 0.0), "…");
    }
}
