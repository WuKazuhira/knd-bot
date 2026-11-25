pub const PJSK_WATERMARK_TEXT: &str = "DESIGNED by KNDBOT in California";

pub mod cache;
pub mod encode;
pub mod fonts;
pub mod shapes;

pub use cache::GfxCache;
pub use encode::{
    ImageEncodingOptions, ImageFormat, add_kndbot_watermark, add_kndbot_watermark_in_place,
    encode_image_bytes, encode_owned_image_bytes, image_to_jpeg, image_to_png,
};
pub use fonts::{FontRegistry, TextAnchor};
pub use shapes::{
    draw_line, draw_rounded_rect, gaussian_blur_rgba, paste_rgba, paste_self_alpha,
    paste_with_l_mask, resize_lanczos, vertical_gradient,
};

use std::sync::Arc;

use crate::core::{DrawError, RenderRequestContext, Result};

#[derive(Clone)]
pub struct GfxContext {
    pub fonts: Arc<FontRegistry>,
    pub cache: Arc<GfxCache>,
}

impl GfxContext {
    pub fn load(font_dir: impl AsRef<std::path::Path>) -> anyhow::Result<Self> {
        Ok(Self {
            fonts: Arc::new(FontRegistry::new(font_dir)?),
            cache: Arc::new(GfxCache::new()),
        })
    }
}

pub trait RenderContextGfxExt {
    fn gfx(&self) -> Result<Arc<GfxContext>>;
}

impl RenderContextGfxExt for RenderRequestContext {
    fn gfx(&self) -> Result<Arc<GfxContext>> {
        self.app
            .extension::<GfxContext>()
            .ok_or_else(|| DrawError::Render("GfxContext 尚未注册到 AppContext".into()))
    }
}
