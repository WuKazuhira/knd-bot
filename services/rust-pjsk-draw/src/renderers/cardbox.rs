use std::{
    cmp::Reverse,
    collections::{HashMap, HashSet},
    sync::LazyLock,
};

use crate::components::{
    card::{
        CARD_RENDER_LIMIT, CardTileOptions, cardthumnail, get_chara_icon_by_chara_id,
        paste_card_thumbnail_tile,
    },
    profile_header::{PjskHeaderData, draw_pjsk_profile_header},
    ui::draw_round_panel,
};
use crate::core::{RenderOutput, RenderRequestContext, Renderer, Result};
use crate::gfx::{GfxContext, RenderContextGfxExt, TextAnchor, paste_rgba};
use async_trait::async_trait;
use image::RgbaImage;
use serde::Deserialize;
use serde_json::Value;
use tokio::{sync::Semaphore, task::JoinSet};

use crate::renderers::common::*;

pub(crate) struct CardBox;

const ATTRS: [(&str, [u8; 3], [u8; 3]); 5] = [
    ("cool", [224, 238, 255], [78, 145, 255]),
    ("cute", [255, 226, 240], [255, 104, 165]),
    ("happy", [255, 242, 217], [255, 174, 75]),
    ("mysterious", [239, 228, 255], [147, 103, 255]),
    ("pure", [222, 247, 228], [78, 190, 105]),
];
const PADDING: i32 = 28;
const ATTR_WIDTH: i32 = 76;
const CHARACTER_HEIGHT: i32 = 76;
const THUMB: i32 = 84;
const GAP: i32 = 8;
const CELL_PAD: i32 = 8;
const HEADER: i32 = 230;
static THUMBNAIL_LIMIT: LazyLock<Semaphore> = LazyLock::new(|| Semaphore::new(*CARD_RENDER_LIMIT));

#[derive(Default, Deserialize)]
#[serde(default)]
struct Payload {
    #[serde(deserialize_with = "deserialize_ids")]
    card_ids: Vec<i64>,
    #[serde(deserialize_with = "deserialize_ids")]
    ordered_chars: Vec<i64>,
    user_cards: Option<Vec<[Value; 2]>>,
    profile: Value,
    show_box: bool,
    pjsk_type: i32,
    #[serde(deserialize_with = "deserialize_ids")]
    limited_card_ids: Vec<i64>,
    #[serde(deserialize_with = "deserialize_ids")]
    fes_card_ids: Vec<i64>,
}

#[derive(Clone, Copy)]
struct Tile {
    id: i64,
    owned: bool,
    rank: i64,
}
type Grid = HashMap<(i64, usize), Vec<Tile>>;

fn build_grid(payload: &Payload, cards: &crate::core::MasterIndex) -> (Vec<i64>, Grid) {
    let owned: Option<HashMap<_, _>> = payload.user_cards.as_ref().map(|values| {
        values
            .iter()
            .map(|[id, rank]| (number(id), number(rank)))
            .collect()
    });
    let mut grid = Grid::new();
    for &id in &payload.card_ids {
        let Some(card) = cards.get(&id.to_string()) else {
            continue;
        };
        let Some(attr) = ATTRS.iter().position(|(attr, _, _)| card["attr"] == *attr) else {
            continue;
        };
        let rank = owned.as_ref().and_then(|cards| cards.get(&id)).copied();
        let tile = Tile {
            id,
            owned: owned.is_none() || rank.is_some(),
            rank: rank.unwrap_or_default(),
        };
        if tile.owned || !payload.show_box {
            grid.entry((number(&card["characterId"]), attr))
                .or_default()
                .push(tile);
        }
    }
    for tiles in grid.values_mut() {
        tiles.sort_by_key(|tile| {
            let card = &cards[&tile.id.to_string()];
            let rarity = match string(&card["cardRarityType"]) {
                "rarity_4" => 5,
                "rarity_birthday" => 4,
                "rarity_3" => 3,
                "rarity_2" => 2,
                "rarity_1" => 1,
                _ => 0,
            };
            Reverse((rarity, number(&card["releaseAt"])))
        });
    }
    let chars = payload
        .ordered_chars
        .iter()
        .copied()
        .filter(|id| (0..ATTRS.len()).any(|attr| grid.contains_key(&(*id, attr))))
        .collect();
    (chars, grid)
}

struct Cell {
    columns: i32,
    width: i32,
    height: i32,
}

fn cell_layout(count: usize, characters: usize) -> Cell {
    if count == 0 {
        return Cell {
            columns: 0,
            width: THUMB + 2 * CELL_PAD,
            height: THUMB + 2 * CELL_PAD,
        };
    }
    let dimensions = |columns: usize| {
        let rows = count.div_ceil(columns);
        let side = |n: usize| n as i32 * (THUMB + GAP) - GAP;
        (side(columns), side(rows))
    };
    let mut columns = (1..=count)
        .min_by_key(|&columns| {
            let (w, h) = dimensions(columns);
            ((w - h).abs(), i64::from(w) * i64::from(h), columns)
        })
        .unwrap_or(1);
    if characters <= 2 {
        let (ratio, min, max) = if characters == 1 {
            (2.4, 5, 8)
        } else {
            (1.7, 3, 5)
        };
        columns =
            count.min(columns.max(((count as f64 * ratio).sqrt().ceil() as usize).clamp(min, max)));
    }
    let (w, h) = dimensions(columns);
    Cell {
        columns: columns as i32,
        width: w + 2 * CELL_PAD,
        height: h + 2 * CELL_PAD,
    }
}

struct Layout {
    cells: HashMap<(i64, usize), Cell>,
    attrs: Vec<usize>,
    columns: Vec<(i64, i32, i32)>,
    heights: Vec<i32>,
    width: i32,
    height: i32,
}

impl Layout {
    fn new(chars: &[i64], grid: &Grid) -> Self {
        let attrs: Vec<_> = (0..ATTRS.len())
            .filter(|&attr| chars.iter().any(|&id| grid.contains_key(&(id, attr))))
            .collect();
        let cells: HashMap<_, _> = chars
            .iter()
            .flat_map(|&id| {
                attrs.iter().map(move |&attr| {
                    (
                        (id, attr),
                        cell_layout(grid.get(&(id, attr)).map_or(0, Vec::len), chars.len()),
                    )
                })
            })
            .collect();
        let mut x = PADDING + ATTR_WIDTH;
        let columns = chars
            .iter()
            .map(|&id| {
                let width = attrs
                    .iter()
                    .map(|&attr| cells[&(id, attr)].width)
                    .max()
                    .unwrap_or(0);
                let column = (id, x, width);
                x += width + GAP;
                column
            })
            .collect();
        let heights: Vec<_> = attrs
            .iter()
            .map(|&attr| {
                chars
                    .iter()
                    .map(|&id| cells[&(id, attr)].height)
                    .max()
                    .unwrap_or(0)
            })
            .collect();
        let height =
            CHARACTER_HEIGHT + heights.iter().sum::<i32>() + (attrs.len() as i32 - 1) * GAP;
        Self {
            cells,
            attrs,
            columns,
            heights,
            width: x - PADDING - GAP,
            height,
        }
    }
}

#[async_trait]
impl Renderer for CardBox {
    fn name(&self) -> &'static str {
        "cardbox"
    }

    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput> {
        let has_badges =
            payload.get("limited_card_ids").is_some() || payload.get("fes_card_ids").is_some();
        let payload: Payload = parse_payload(payload)?;
        let server = payload.pjsk_type;
        let master = &ctx.app.master_data;
        let cards = master
            .master_data_by_id_async("cards.json", server, "id")
            .await?;
        let (chars, grid) = build_grid(&payload, &cards);
        let gfx = ctx.gfx()?;
        if chars.is_empty() {
            return render_png(gfx, |gfx| {
                let mut image = RgbaImage::from_pixel(800, 300, rgba([248, 246, 252], 255));
                gfx.fonts.draw_text(
                    &mut image,
                    (400., 150.),
                    "没有找到符合条件的卡牌",
                    BOLD,
                    30.,
                    rgba([0; 3], 255),
                    TextAnchor::Mm,
                    None,
                );
                image
            })
            .await;
        }
        let badge_data = if has_badges {
            None
        } else {
            Some((
                master
                    .load_master_data_async("cardCostume3ds.json", server)
                    .await?,
                master
                    .load_master_data_async("costume3ds.json", server)
                    .await?,
                master
                    .load_master_data_async("cardSupplies.json", server)
                    .await?,
            ))
        };
        let limited: HashSet<_> = payload.limited_card_ids.iter().copied().collect();
        let fes: HashSet<_> = payload.fes_card_ids.iter().copied().collect();
        let layout = Layout::new(&chars, &grid);
        let mut jobs = JoinSet::new();
        let mut seen = HashSet::new();
        for &character in &chars {
            for &attr in &layout.attrs {
                for tile in grid.get(&(character, attr)).into_iter().flatten() {
                    if !seen.insert(tile.id) {
                        continue;
                    }
                    let id = tile.id;
                    let card = &cards[&id.to_string()];
                    let rarity = string(&card["cardRarityType"]);
                    let trained = matches!(rarity, "rarity_3" | "rarity_4");
                    let (is_limited, is_fes) =
                        if let Some((links, costumes, supplies)) = &badge_data {
                            let limited = rarity == "rarity_birthday"
                                || ctx.app.cardtype(id, links, costumes) == 1;
                            (
                                limited,
                                limited && ctx.app.is_fes_card(card, Some(supplies), server),
                            )
                        } else {
                            (
                                limited.contains(&id) || rarity == "rarity_birthday",
                                fes.contains(&id),
                            )
                        };
                    let ctx = ctx.clone();
                    jobs.spawn(async move {
                        let permit = THUMBNAIL_LIMIT
                            .acquire()
                            .await
                            .expect("static semaphore stays open");
                        let result = cardthumnail(
                            &ctx,
                            id,
                            trained,
                            None,
                            is_limited && !is_fes,
                            is_limited && is_fes,
                            server,
                        )
                        .await;
                        drop(permit);
                        (id, result.ok().flatten())
                    });
                }
            }
        }
        let profile = payload.profile.as_object().filter(|map| !map.is_empty());
        let top = if profile.is_some() {
            HEADER + PADDING
        } else {
            PADDING
        };
        let w = (layout.width + 2 * PADDING).max(800);
        let h = layout.height + top + PADDING;
        let mut canvas = tokio::task::spawn_blocking(move || {
            make_style_gradient_bg(
                (w as u32, h as u32),
                [255, 246, 250],
                [236, 244, 255],
                &[
                    SoftGlow {
                        center: (w as f32 * 0.15, h as f32 * 0.04),
                        radii: (w as f32 * 0.35, h as f32 * 0.29),
                        color: rgba([255, 190, 220], 80),
                    },
                    SoftGlow {
                        center: (w as f32 * 0.875, h as f32 * 0.725),
                        radii: (w as f32 * 0.375, h as f32 * 0.475),
                        color: rgba([170, 210, 255], 70),
                    },
                ],
            )
        })
        .await
        .map_err(render_error)?;
        if let Some(profile) = profile {
            let mut normalized = Value::Object(profile.clone());
            for (python, rust) in [
                ("userDecks", "user_decks"),
                ("userProfileHonors", "user_profile_honors"),
                ("userHonorMissions", "user_honor_missions"),
            ] {
                if let Some(value) = profile.get(python) {
                    normalized[rust] = value.clone();
                }
            }
            let data = PjskHeaderData::from_payload(&normalized);
            let assets = data
                .user_decks
                .iter()
                .filter_map(|&id| {
                    let card = cards.get(&id.to_string())?;
                    Some((id, string(&card["assetbundleName"]).to_owned()))
                })
                .collect();
            draw_pjsk_profile_header(
                ctx,
                &mut canvas,
                (PADDING, 12, w - PADDING, HEADER - 12),
                &data,
                "CARD BOX",
                server,
                Some(&assets),
                None,
                false,
                false,
            )
            .await?;
        }
        let mut thumbs = HashMap::new();
        while let Some(job) = jobs.join_next().await {
            if let (id, Some(image)) = job.map_err(render_error)? {
                thumbs.insert(id, image);
            }
        }
        let colors = master
            .master_data_by_id("gameCharacterUnits.json", server, "id")
            .ok();
        let portraits: HashMap<_, _> = chars
            .into_iter()
            .filter_map(|id| {
                let image =
                    character_portrait(&gfx, &ctx.app.paths.static_dir, id).or_else(|| {
                        get_chara_icon_by_chara_id(ctx, id, None)
                            .ok()
                            .map(std::sync::Arc::new)
                    })?;
                let color = colors
                    .as_ref()
                    .and_then(|data| data.get(&id.to_string()))
                    .map(|row| string(&row["colorCode"]).trim_start_matches('#'))
                    .filter(|s| s.len() == 6)
                    .and_then(|s| u32::from_str_radix(s, 16).ok())
                    .map(|n| [(n >> 16) as u8, (n >> 8) as u8, n as u8])
                    .unwrap_or([136; 3]);
                Some((id, (image, color)))
            })
            .collect();
        let static_dir = ctx.app.paths.static_dir.clone();
        render_jpeg(gfx, 88, true, move |gfx| {
            draw_grid(
                gfx,
                &mut canvas,
                &static_dir,
                &layout,
                &grid,
                &thumbs,
                &portraits,
                top,
            );
            canvas
        })
        .await
    }
}

#[allow(clippy::too_many_arguments)]
fn draw_grid(
    gfx: &GfxContext,
    canvas: &mut RgbaImage,
    static_dir: &std::path::Path,
    layout: &Layout,
    grid: &Grid,
    thumbs: &HashMap<i64, RgbaImage>,
    portraits: &HashMap<i64, (std::sync::Arc<RgbaImage>, [u8; 3])>,
    top: i32,
) {
    let width = layout.width;
    draw_round_panel(
        &gfx.cache,
        canvas,
        (PADDING, top, PADDING + width, top + layout.height),
        22,
        rgba([255; 3], 232),
        Some(rgba([255; 3], 220)),
        true,
    );
    rounded_panel_overlay(
        gfx,
        canvas,
        (
            PADDING,
            top,
            PADDING + width - 1,
            top + CHARACTER_HEIGHT - 1,
        ),
        22,
        rgba([255; 3], 120),
        None,
    );
    for &(id, x, w) in &layout.columns {
        let color = portraits.get(&id).map_or([136; 3], |(_, color)| *color);
        let pill_w = (w - 10).min(82);
        let left = x + (w - pill_w) / 2;
        let soft = color.map(|v| (v as f32 * 0.22 + 255. * 0.78) as u8);
        rounded_panel_overlay(
            gfx,
            canvas,
            (left, top + 4, left + pill_w, top + 67),
            18,
            rgba(soft, 165),
            Some(rgba(color, 170)),
        );
        if let Some((source, _)) = portraits.get(&id) {
            paste_rgba(
                canvas,
                &circle_avatar(source, 64, rgba(color, 255)),
                x + (w - 64) / 2,
                top + 7,
            );
        }
        rounded_panel_overlay(
            gfx,
            canvas,
            (x + 10, top + 69, x + w - 11, top + 73),
            2,
            rgba(color, 255),
            None,
        );
    }
    let mut y = top + CHARACTER_HEIGHT;
    for (row, (&attr, &h)) in layout.attrs.iter().zip(&layout.heights).enumerate() {
        let (name, color, accent) = ATTRS[attr];
        rounded_panel_overlay(
            gfx,
            canvas,
            (PADDING + 7, y, PADDING + width - 8, y + h - 1),
            16,
            rgba(color, 150),
            None,
        );
        rounded_panel_overlay(
            gfx,
            canvas,
            (PADDING + 7, y + 6, PADDING + ATTR_WIDTH - 8, y + h - 7),
            18,
            rgba(accent, 42),
            Some(rgba(accent, 120)),
        );
        if let Ok(icon) = gfx.cache.open_pjsk_image(
            static_dir.join(format!("chara/icon_attribute_{name}.png")),
            Some((46, 46)),
        ) {
            paste_rgba(
                canvas,
                &icon,
                PADDING + (ATTR_WIDTH - 46) / 2,
                y + (h - 46) / 2,
            );
        }
        for (index, &(id, x, w)) in layout.columns.iter().enumerate() {
            if index % 2 == 0 {
                rounded_panel_overlay(
                    gfx,
                    canvas,
                    (x, y + 4, x + w - 1, y + h - 5),
                    0,
                    rgba([255; 3], 44),
                    None,
                );
            }
            let cell = &layout.cells[&(id, attr)];
            for (index, tile) in grid.get(&(id, attr)).into_iter().flatten().enumerate() {
                let Some(thumb) = thumbs.get(&tile.id) else {
                    continue;
                };
                let x = x
                    + (w - cell.width) / 2
                    + CELL_PAD
                    + (index as i32 % cell.columns) * (THUMB + GAP);
                let y = y
                    + (h - cell.height) / 2
                    + CELL_PAD
                    + (index as i32 / cell.columns) * (THUMB + GAP);
                paste_card_thumbnail_tile(
                    gfx,
                    static_dir,
                    canvas,
                    thumb,
                    (x, y),
                    CardTileOptions {
                        size: THUMB as u32,
                        has_card: tile.owned,
                        missing_font: Some((BOLD, 10.)),
                        master_rank: tile.rank,
                        card_id: tile.id,
                        id_font: Some((MEDIUM, 9.)),
                    },
                );
            }
        }
        if row + 1 < layout.attrs.len() {
            rounded_panel_overlay(
                gfx,
                canvas,
                (PADDING + 18, y + h + 3, PADDING + width - 18, y + h + 4),
                0,
                rgba([255; 3], 255),
                None,
            );
        }
        y += h + GAP;
    }
    rounded_panel_overlay(
        gfx,
        canvas,
        (
            PADDING + ATTR_WIDTH - 2,
            top + 12,
            PADDING + ATTR_WIDTH + 1,
            top + layout.height - 12,
        ),
        2,
        rgba([210, 205, 225], 255),
        None,
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn ownership_and_layout() {
        let mut payload: Payload = parse_payload(json!({"card_ids":[1,2],"ordered_chars":["3"],"user_cards":[["2",5]],"limited_card_ids":null})).unwrap();
        let cards = HashMap::from([
            (
                "1".into(),
                json!({"characterId":3,"attr":"cool","cardRarityType":"rarity_4"}),
            ),
            (
                "2".into(),
                json!({"characterId":3,"attr":"cool","cardRarityType":"rarity_2"}),
            ),
        ]);
        let (chars, grid) = build_grid(&payload, &cards);
        assert_eq!(chars, vec![3]);
        let tiles = &grid[&(3, 0)];
        assert!(!tiles[0].owned);
        assert_eq!(tiles[1].rank, 5);
        payload.show_box = true;
        assert_eq!(build_grid(&payload, &cards).1[&(3, 0)].len(), 1);
        assert_eq!(cell_layout(8, 1).columns, 5);
        assert_eq!(cell_layout(8, 3).columns, 3);
    }

    #[tokio::test]
    async fn renders_profile_grid_as_jpeg_and_empty_grid_as_png() {
        let fixture = RenderFixture::new();
        fixture.write("ondemand/jp/cards.json",r#"[{"id":1,"characterId":3,"attr":"cool","cardRarityType":"rarity_1","assetbundleName":"test"}]"#);
        fixture.png(
            "ondemand/jp/startapp/thumbnail/chara/test_normal.png",
            (32, 32),
        );
        fixture.png("static/chara/cardFrame_rarity_1.png", (32, 32));
        fixture.png("static/chara/rarity_star_normal.png", (32, 32));
        fixture.png("static/chara/icon_attribute_cool.png", (32, 32));
        let ctx = fixture.context();
        let payload = json!({"card_ids":[1],"ordered_chars":[3],"user_cards":[[1,4]],"limited_card_ids":null,"profile":{"name":"Player","userDecks":[1]}});
        let output = CardBox.render(&ctx, payload).await.unwrap();
        let image =
            image::load_from_memory_with_format(&output.images[0], image::ImageFormat::Jpeg)
                .unwrap()
                .into_rgb8();
        assert_eq!(image.dimensions(), (800, 462));
        let output = CardBox.render(&ctx, json!({})).await.unwrap();
        let image = image::load_from_memory_with_format(&output.images[0], image::ImageFormat::Png)
            .unwrap()
            .into_rgba8();
        assert_eq!(image.dimensions(), (800, 300));
    }
}
