use std::sync::LazyLock;

use crate::components::{
    normalize_master_list,
    profile_header::{PjskHeaderData, draw_pjsk_profile_header},
    ui::{get_pjsk_music_jacket_cached, paste_round_image},
};
use crate::core::{RenderOutput, RenderRequestContext, Renderer, Result};
use crate::gfx::{GfxContext, RenderContextGfxExt, TextAnchor::*};
use async_trait::async_trait;
use image::RgbaImage;
use serde_json::Value;
use tokio::sync::Semaphore;

use crate::renderers::common::*;

pub(crate) struct B30;
const LIMIT: usize = 8;
static JACKETS: LazyLock<Semaphore> = LazyLock::new(|| Semaphore::new(LIMIT));

#[async_trait]
impl Renderer for B30 {
    fn name(&self) -> &'static str {
        "b30"
    }

    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput> {
        let gfx = ctx.gfx()?;
        let server = number(&payload["pjsk_type"]) as i32;
        let store = &ctx.app.master_data;
        let cards = store.load_master_data_async("cards.json", server).await?;
        let music = store.load_master_data_async("musics.json", server).await?;
        let titles: std::collections::HashMap<_, _> = normalize_master_list(&music)
            .into_iter()
            .map(|row| (number(&row["id"]), string(&row["title"])))
            .collect();

        let tasks: Vec<_> = rows(&payload["diff"])
            .iter()
            .take(30)
            .cloned()
            .map(|entry| {
                let ctx = ctx.clone();
                async move {
                    let _permit = JACKETS.acquire().await.expect("jacket semaphore is open");
                    let jacket = get_pjsk_music_jacket_cached(
                        &ctx,
                        number(&entry["musicId"]),
                        server,
                        Some((100, 100)),
                        None,
                        true,
                    )
                    .await
                    .ok()
                    .flatten();
                    (entry, jacket)
                }
            })
            .collect();
        let entries = load_ordered(tasks, LIMIT).await?;
        let average = entries
            .iter()
            .map(|(entry, _)| decimal(&entry["rank"]))
            .sum::<f64>()
            / entries.len().max(1) as f64;
        let average = format!("{average:.2}");
        let highest = payload["highest"]
            .as_str()
            .map(str::to_owned)
            .unwrap_or_else(|| {
                payload["highest"]
                    .as_number()
                    .map_or_else(|| "0".into(), ToString::to_string)
            });
        let entries: Vec<_> = entries
            .into_iter()
            .map(|(entry, jacket)| {
                let id = number(&entry["musicId"]);
                let title = titles
                    .get(&id)
                    .filter(|title| !title.is_empty())
                    .map_or_else(|| format!("Music {id}"), |title| (*title).to_owned());
                (entry, jacket, title)
            })
            .collect();

        let mut image = make_style_gradient_bg(
            (1120, 1810),
            [255, 246, 250],
            [236, 244, 255],
            &[
                SoftGlow {
                    center: (140., 113.),
                    radii: (420., 339.),
                    color: rgba([255, 190, 220], 70),
                },
                SoftGlow {
                    center: (952., 1282.),
                    radii: (392., 830.),
                    color: rgba([170, 210, 255], 58),
                },
            ],
        );
        draw_pjsk_profile_header(
            ctx,
            &mut image,
            (36, 28, 1084, 286),
            &PjskHeaderData::from_payload(&payload["header"]),
            "BEST 30 REPORT",
            server,
            Some(&card_assets(&cards)),
            Some(&[("B30", &average), ("最高理论值", &highest)]),
            true,
            false,
        )
        .await?;

        render_jpeg(gfx, 90, true, move |gfx| {
            rounded_panel_overlay(
                gfx,
                &mut image,
                (36, 330, 1084, 1668),
                28,
                rgba([255; 3], 132),
                Some(rgba([255; 3], 210)),
            );
            for (i, (entry, jacket, title)) in entries.iter().enumerate() {
                draw_entry(
                    gfx,
                    &mut image,
                    (53 + (i % 3) as i32 * 342, 356 + (i / 3) as i32 * 130),
                    entry,
                    jacket.as_ref(),
                    title,
                );
            }
            label(
                gfx,
                &mut image,
                (50, 1716),
                &format!(
                    "注：33+FC权重减1，其他减1.5，非官方算法，仅供参考娱乐，当前理论值为{highest}"
                ),
                (MEDIUM, 15., [92, 72, 98], La),
            );
            label(
                gfx,
                &mut image,
                (50, 1742),
                "※定数非官方 仅供参考娱乐 请勿当真",
                (MEDIUM, 15., [130, 104, 138], La),
            );
            label(
                gfx,
                &mut image,
                (1070, 1744),
                "BEST 30",
                (RODIN, 18., [120, 80, 100], Rm),
            );
            label(
                gfx,
                &mut image,
                (68, 20),
                string(&payload["data_update_text"]),
                (BOLD, 25., [100; 3], La),
            );
            image
        })
        .await
    }
}

fn draw_entry(
    gfx: &GfxContext,
    image: &mut RgbaImage,
    (x, y): (i32, i32),
    entry: &Value,
    jacket: Option<&RgbaImage>,
    title: &str,
) {
    let difficulty = string(&entry["musicDifficulty"]);
    let accent = DIFFICULTIES[..5]
        .iter()
        .find(|(name, _)| *name == difficulty)
        .map_or([230, 140, 170], |&(_, color)| color);
    rounded_panel_overlay(
        gfx,
        image,
        (x, y, x + 309, y + 119),
        18,
        rgba([255; 3], 236),
        Some(rgba([255; 3], 255)),
    );
    if let Some(jacket) = jacket {
        paste_round_image(&gfx.cache, image, jacket, (x + 10, y + 10), (100, 100), 16);
    } else {
        rounded_panel_overlay(
            gfx,
            image,
            (x + 10, y + 10, x + 110, y + 110),
            16,
            rgba([235, 235, 245], 255),
            None,
        );
        label(
            gfx,
            image,
            (x + 60, y + 60),
            "♪",
            (BOLD, 38., [160, 150, 180], Mm),
        );
    }
    rounded_panel_overlay(
        gfx,
        image,
        (x + 124, y + 14, x + 178, y + 42),
        14,
        rgba(accent, 255),
        None,
    );
    label(
        gfx,
        image,
        (x + 151, y + 27),
        &number(&entry["playLevel"]).to_string(),
        (BOLD, 20., [255; 3], Mm),
    );
    label(
        gfx,
        image,
        (x + 186, y + 16),
        &difficulty.to_uppercase(),
        (RODIN, 14., accent, La),
    );
    label(
        gfx,
        image,
        (x + 124, y + 48),
        &fit_text(&gfx.fonts, title, BOLD, 18., 170.),
        (BOLD, 18., [42, 32, 48], La),
    );

    let (result, color) = match number(&entry["result"]) {
        2 => ("AP", [255, 110, 170]),
        1 => ("FC", [85, 170, 245]),
        _ => ("--", [85, 170, 245]),
    };
    rounded_panel_overlay(
        gfx,
        image,
        (x + 124, y + 82, x + 170, y + 108),
        13,
        rgba(color, 255),
        None,
    );
    label(
        gfx,
        image,
        (x + 147, y + 94),
        result,
        (RODIN, 15., [255; 3], Mm),
    );
    let base = decimal(entry.get("aplevel+").unwrap_or(&entry["playLevel"]));
    let weight = decimal(&entry["rank"]);
    label(
        gfx,
        image,
        (x + 182, y + 80),
        &format!("{base:.1}→{weight:.1}"),
        (BOLD, 17., [70, 52, 78], La),
    );
    label(
        gfx,
        image,
        (x + 182, y + 101),
        "constant weight",
        (RODIN, 10., [142, 118, 150], La),
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[tokio::test]
    async fn renders_jacket_and_three_column_grid_offline() {
        let fixture = RenderFixture::new();
        fixture.write("ondemand/jp/cards.json", "[]");
        fixture.write("ondemand/jp/musics.json", r#"[{"id":1,"title":"Test"}]"#);
        fixture.png(
            "ondemand/jp/startapp/thumbnail/music_jacket/jacket_s_001.png",
            (32, 32),
        );
        let entry = json!({"musicId":1,"musicDifficulty":"master","playLevel":31,"aplevel+":31.2,"rank":30.5,"result":1});
        let output = B30
            .render(
                &fixture.context(),
                json!({"diff":[entry,entry,entry,entry],"highest":36}),
            )
            .await
            .unwrap();
        let image =
            image::load_from_memory_with_format(&output.images[0], image::ImageFormat::Jpeg)
                .unwrap()
                .into_rgb8();
        assert_eq!(image.dimensions(), (1120, 1810));
        for (x, y) in [(100, 400), (442, 400), (784, 400), (100, 530)] {
            assert!(image.get_pixel(x, y)[1] < 90);
        }
    }
}
