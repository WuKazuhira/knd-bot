use std::{path::Path, sync::Arc};

use crate::core::{DrawError, RenderOutput, RenderRequestContext, Renderer, Result};
use crate::gfx::{GfxContext, RenderContextGfxExt, paste_with_l_mask};
use async_trait::async_trait;
use image::{
    GrayImage, Luma, Rgba, RgbaImage,
    imageops::{self, FilterType},
};
use serde::Deserialize;
use tokio::task::JoinSet;

use crate::renderers::common::{deserialize_ids, parse_payload, render_error, render_png};

const TILE: u32 = 338;

pub(crate) struct Gacha;

#[derive(Default, Deserialize)]
#[serde(default)]
struct Payload {
    #[serde(deserialize_with = "deserialize_ids")]
    card_ids: Vec<i64>,
    pjsk_type: i32,
}

struct Card {
    rarity: String,
    attr: String,
    cutout: Option<Arc<RgbaImage>>,
}

#[async_trait]
impl Renderer for Gacha {
    fn name(&self) -> &'static str {
        "gacha"
    }

    async fn render(
        &self,
        ctx: &RenderRequestContext,
        payload: serde_json::Value,
    ) -> Result<RenderOutput> {
        let payload: Payload = parse_payload(payload)?;
        if payload.card_ids.len() != 10 {
            return Err(DrawError::InvalidPayload(
                "gacha requires 10 card IDs".into(),
            ));
        }
        let pjsk_type = payload.pjsk_type;
        let store = ctx.app.master_data.clone();
        let cards = tokio::task::spawn_blocking(move || {
            store.master_data_first_by_id("cards.json", pjsk_type, "id")
        })
        .await
        .map_err(render_error)??;
        let mut fetches = JoinSet::new();
        for (index, id) in payload.card_ids.into_iter().enumerate() {
            let card = cards
                .get(&id.to_string())
                .ok_or_else(|| DrawError::InvalidPayload(format!("unknown card ID {id}")))?;
            let field = |name| {
                card[name]
                    .as_str()
                    .map(str::to_owned)
                    .ok_or_else(|| DrawError::Render(format!("card {id} has no {name}")))
            };
            let rarity = field("cardRarityType")?;
            let attr = field("attr")?;
            let asset = field("assetbundleName")?;
            let ctx = ctx.clone();
            fetches.spawn(async move {
                let category = format!("startapp/character/member_cutout/{asset}");
                let cutout = ctx
                    .app
                    .get_asset_cached(&category, "normal.png", pjsk_type, ctx.policy, true)
                    .await?;
                Ok::<_, DrawError>((
                    index,
                    Card {
                        rarity,
                        attr,
                        cutout,
                    },
                ))
            });
        }
        let mut tiles = Vec::with_capacity(10);
        while let Some(result) = fetches.join_next().await {
            tiles.push(result.map_err(render_error)??);
        }
        tiles.sort_unstable_by_key(|(index, _)| *index);
        let static_dir = ctx.app.paths.static_dir.clone();
        let gfx = ctx.gfx()?;
        let draw_gfx = gfx.clone();
        let image = tokio::task::spawn_blocking(move || draw_gacha(&draw_gfx, &static_dir, &tiles))
            .await
            .map_err(render_error)??;
        render_png(gfx, |_| image).await
    }
}

fn resize(image: &RgbaImage, side: u32) -> RgbaImage {
    imageops::resize(image, side, side, FilterType::CatmullRom)
}

fn alpha_mask(image: &RgbaImage) -> GrayImage {
    GrayImage::from_fn(image.width(), image.height(), |x, y| {
        Luma([image.get_pixel(x, y)[3]])
    })
}

fn paste(base: &mut RgbaImage, image: &RgbaImage, x: i32, y: i32) {
    paste_with_l_mask(base, image, &alpha_mask(image), x, y);
}

fn draw_gacha(gfx: &GfxContext, static_dir: &Path, cards: &[(usize, Card)]) -> Result<RgbaImage> {
    let open = |path: &str| {
        gfx.cache
            .open_pjsk_image(static_dir.join(path), None)
            .map_err(render_error)
    };
    let mut background = (*open("pics/gacha.png")?).clone();
    let cover = RgbaImage::from_pixel(1550, 600, Rgba([255; 4]));
    imageops::replace(&mut background, &cover, 314, 500);

    let cutout_mask = if cards.iter().any(|(_, card)| card.cutout.is_some()) {
        let image = open("pics/gachacardmask.png")?;
        let mask = alpha_mask(&image);
        Some(imageops::resize(&mask, TILE, TILE, FilterType::CatmullRom))
    } else {
        None
    };
    for (index, card) in cards {
        let mut tile = RgbaImage::new(TILE, TILE);
        if let (Some(cutout), Some(mask)) = (&card.cutout, &cutout_mask) {
            paste_with_l_mask(&mut tile, &resize(cutout, TILE), mask, 0, 0);
        }
        let frame = open(&format!("chara/cardFrame_{}.png", card.rarity))?;
        paste(&mut tile, &resize(&frame, TILE), 0, 0);
        let stars = match card.rarity.as_str() {
            "rarity_1" | "rarity_birthday" => 1,
            "rarity_2" => 2,
            "rarity_3" => 3,
            "rarity_4" => 4,
            _ => 0,
        };
        if stars > 0 {
            let star = open(if card.rarity == "rarity_birthday" {
                "chara/rarity_birthday.png"
            } else {
                "chara/rarity_star_normal.png"
            })?;
            let star = resize(&star, if card.rarity == "rarity_1" { 61 } else { 60 });
            for x in [21, 78, 134, 190].into_iter().take(stars) {
                paste(&mut tile, &star, x, 256);
            }
        }
        let attr = open(&format!("chara/icon_attribute_{}.png", card.attr))?;
        paste(&mut tile, &resize(&attr, 76), 1, 1);
        paste(
            &mut background,
            &resize(&tile, 263),
            336 + 304 * (index % 5) as i32,
            520 + 305 * (index / 5) as i32,
        );
    }
    for pixel in background.pixels_mut() {
        pixel[3] = 255;
    }
    Ok(background)
}
