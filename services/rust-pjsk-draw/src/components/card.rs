use std::{
    cmp::Reverse,
    collections::{HashMap, HashSet},
    path::Path,
    sync::LazyLock,
};

use crate::core::{AssetSourcePolicy, DrawError, RenderRequestContext, Result};
use crate::gfx::{
    GfxContext, RenderContextGfxExt, TextAnchor, draw_line, draw_rounded_rect, vertical_gradient,
};
use futures_util::{StreamExt, stream};
use image::{Rgba, RgbaImage, imageops};
use serde_json::Value;
use tokio::sync::Semaphore;

use crate::components::{
    integer, normalize_master_list,
    profile_header::MEDIUM,
    string,
    ui::{
        Bounds, ImageSize, Point, get_pjsk_asset_resized, open_image, paste_alpha,
        paste_round_image, rounded_layer,
    },
};

const ATTR_ORDER: [&str; 5] = ["cool", "cute", "happy", "mysterious", "pure"];
const CELL_W: i32 = 420;
const CELL_H: i32 = 260;
const PAD: i32 = 16;
const CONTENT_LEFT: i32 = PAD + 70;
const WHITE: [u8; 3] = [255; 3];
const LINE_HEADER: [u8; 3] = [200, 120, 150];
const LINE_HEAVY: [u8; 3] = [220, 150, 175];
const LINE_LIGHT: [u8; 3] = [235, 190, 210];

pub static CARD_RENDER_LIMIT: LazyLock<usize> = LazyLock::new(|| {
    std::thread::available_parallelism()
        .map_or(4, usize::from)
        .clamp(4, 8)
});

#[derive(Clone, Copy, Debug)]
pub struct CardTileOptions<'a> {
    pub size: u32,
    pub has_card: bool,
    pub missing_font: Option<(&'a str, f32)>,
    pub master_rank: i64,
    pub card_id: i64,
    pub id_font: Option<(&'a str, f32)>,
}

impl Default for CardTileOptions<'_> {
    fn default() -> Self {
        Self {
            size: 84,
            has_card: true,
            missing_font: None,
            master_rank: 0,
            card_id: 0,
            id_font: None,
        }
    }
}

pub fn render_card_thumbnail_tile(
    gfx: &GfxContext,
    static_dir: &Path,
    thumb: &RgbaImage,
    options: CardTileOptions<'_>,
) -> RgbaImage {
    let size = options.size;
    let mut tile = RgbaImage::new(size, size);
    if size <= 6 {
        return tile;
    }
    let side = size as i32;
    let shadow = gfx
        .cache
        .get_soft_shadow((size - 2, size - 2), 13, 26, 10, [70, 55, 90]);
    paste_alpha(&mut tile, &shadow, (2, 3));
    let mut card = RgbaImage::new(size, size);
    let backdrop = rounded_layer(
        &gfx.cache,
        (size - 2, size - 2),
        13,
        Rgba([255, 255, 255, 88]),
        Some(Rgba([255, 255, 255, 190])),
    );
    imageops::replace(&mut card, &backdrop, 1, 1);
    paste_round_image(
        &gfx.cache,
        &mut card,
        thumb,
        (3, 3),
        (size - 6, size - 6),
        12,
    );

    let ink = Rgba([70, 60, 82, 255]);
    if options.card_id != 0 {
        let (font, font_size) = options
            .id_font
            .unwrap_or((MEDIUM, (size as f32 * 0.11).floor().max(9.0)));
        let text = options.card_id.to_string();
        let (width, height) = gfx.fonts.measure_text(font, font_size, &text);
        let label_w = (side - 12).min(width as i32 + 8);
        let label_h = 14.max(height as i32 + 4);
        let label = gfx
            .cache
            .get_rounded_rect_mask(((label_w + 1).max(0) as u32, (label_h + 1) as u32), 6);
        for (x, y, coverage) in label.enumerate_pixels() {
            if coverage[0] != 0
                && let Some(pixel) = card.get_pixel_mut_checked(x + 6, y + 6)
            {
                *pixel = Rgba([255, 255, 255, 205]);
            }
        }
        gfx.fonts.draw_text(
            &mut card,
            (10.0, (6 + label_h / 2) as f32),
            &text,
            font,
            font_size,
            ink,
            TextAnchor::Lm,
            None,
        );
    }

    if options.has_card && (1..=5).contains(&options.master_rank) {
        let rank_size = 18.max((size as f32 * 0.26) as u32);
        let path = static_dir.join(format!("chara/train_rank_{}.png", options.master_rank));
        if let Ok(icon) = open_image(&gfx.cache, path, Some((rank_size, rank_size))) {
            let offset = side - rank_size as i32 - 5;
            paste_alpha(&mut card, &icon, (offset, offset));
        }
    }
    if !options.has_card {
        let mut overlay = RgbaImage::new(size, size);
        let tint = rounded_layer(
            &gfx.cache,
            (size - 6, size - 6),
            12,
            Rgba([20, 16, 26, 112]),
            None,
        );
        imageops::replace(&mut overlay, &tint, 3, 3);
        // 角标直接覆盖底图对应像素，不做 alpha 混合
        let (w, h) = ((side - 19).max(0) as u32, 18);
        let mask = gfx.cache.get_rounded_rect_mask((w, h), 8);
        let y = side - 25;
        for (sx, sy, coverage) in mask.enumerate_pixels() {
            let dy = y + sy as i32;
            if coverage[0] != 0 && (0..side).contains(&dy) {
                overlay.put_pixel(10 + sx, dy as u32, Rgba([255, 255, 255, 188]));
            }
        }
        let (font, font_size) = options
            .missing_font
            .unwrap_or((MEDIUM, (size as f32 * 0.13).floor().max(10.0)));
        gfx.fonts.draw_text(
            &mut overlay,
            ((side / 2) as f32, (side - 17) as f32),
            "未持有",
            font,
            font_size,
            ink,
            TextAnchor::Mm,
            None,
        );
        paste_alpha(&mut card, &overlay, (0, 0));
    }
    paste_alpha(&mut tile, &card, (0, 0));
    tile
}

pub fn paste_card_thumbnail_tile(
    gfx: &GfxContext,
    static_dir: &Path,
    base: &mut RgbaImage,
    thumb: &RgbaImage,
    pos: Point,
    options: CardTileOptions<'_>,
) {
    paste_alpha(
        base,
        &render_card_thumbnail_tile(gfx, static_dir, thumb, options),
        pos,
    );
}

fn training_suffix(trained: bool, rarity: &str) -> &'static str {
    if trained && matches!(rarity, "rarity_3" | "rarity_4") {
        "after_training"
    } else {
        "normal"
    }
}

fn stars(rarity: &str) -> i32 {
    match rarity {
        "rarity_1" => 1,
        "rarity_2" => 2,
        "rarity_3" => 3,
        "rarity_4" => 4,
        _ => 0,
    }
}

fn lookup_card(cards: &Value, id: i64) -> Option<&Value> {
    normalize_master_list(cards)
        .into_iter()
        .find(|row| row["id"].as_i64() == Some(id))
}

pub async fn cardthumnail(
    ctx: &RenderRequestContext,
    cardid: i64,
    istrained: bool,
    cards: Option<&Value>,
    limitedbadge: bool,
    fesbadge: bool,
    pjsk_type: i32,
) -> Result<Option<RgbaImage>> {
    let index;
    let card = if let Some(cards) = cards {
        lookup_card(cards, cardid)
    } else {
        index = ctx
            .app
            .master_data
            .master_data_by_id("cards.json", pjsk_type, "id")?;
        index.get(&cardid.to_string())
    };
    let Some(card) = card else { return Ok(None) };
    render_thumbnail(ctx, card, istrained, limitedbadge, fesbadge, pjsk_type)
        .await
        .map(Some)
}

async fn render_thumbnail(
    ctx: &RenderRequestContext,
    card: &Value,
    trained: bool,
    limited: bool,
    fes: bool,
    pjsk_type: i32,
) -> Result<RgbaImage> {
    let gfx = ctx.gfx()?;
    let static_dir = &ctx.app.paths.static_dir;
    let rarity = string(&card["cardRarityType"]);
    let asset = string(&card["assetbundleName"]);
    let attr = string(&card["attr"]);
    let suffix = training_suffix(trained, rarity);
    let key = format!(
        "card-thumbnail-v2:{:?}",
        (
            pjsk_type,
            &card["id"],
            asset,
            rarity,
            attr,
            suffix,
            limited,
            fes
        )
    );
    let strict_local = ctx.policy == AssetSourcePolicy::LocalOnly;
    if !strict_local && let Some(image) = gfx.cache.get_cached_render_image(&key) {
        return Ok((*image).clone());
    }

    let frame = open_image(
        &gfx.cache,
        static_dir.join(format!("chara/cardFrame_{rarity}.png")),
        None,
    )?;
    let (w, h) = frame.dimensions();
    let filename = format!("{asset}_{suffix}.png");
    let missing = || DrawError::LocalOnlyAsset(format!("local-updater 卡牌缩略图缺失: {filename}"));
    let loaded = match get_pjsk_asset_resized(
        ctx,
        "startapp/thumbnail/chara",
        &filename,
        pjsk_type,
        Some((w, h)),
    )
    .await
    {
        Ok(None) if strict_local => return Err(missing()),
        Err(DrawError::LocalOnlyAssetNotFound(_)) if strict_local => return Err(missing()),
        result => result?,
    };
    let asset_loaded = loaded.is_some();
    let mut pic = loaded.unwrap_or_else(|| solid(w, h, [220; 3]));
    decorate_card(
        &gfx, static_dir, &mut pic, &frame, rarity, attr, suffix, false,
    )?;
    let badge = if fes {
        Some("badge_fesLimited.png")
    } else if limited {
        Some("badge_limited.png")
    } else {
        None
    };
    if let Some(name) = badge
        && let Ok(badge) = open_image(&gfx.cache, static_dir.join("pics").join(name), None)
    {
        paste_alpha(&mut pic, &badge, (w as i32 - badge.width() as i32, 0));
    }
    if asset_loaded && !strict_local {
        gfx.cache.put_cached_render_image(key, pic.clone());
    }
    Ok(pic)
}

pub async fn cardlarge(
    ctx: &RenderRequestContext,
    cardid: i64,
    istrained: bool,
    cards: Option<&Value>,
    pjsk_type: i32,
) -> Result<RgbaImage> {
    let loaded;
    let cards = match cards {
        Some(cards) => cards,
        None => {
            loaded = ctx
                .app
                .master_data
                .load_master_data("cards.json", pjsk_type)?;
            &loaded
        }
    };
    let Some(card) = lookup_card(cards, cardid) else {
        return Ok(solid(940, 530, WHITE));
    };
    let gfx = ctx.gfx()?;
    let static_dir = &ctx.app.paths.static_dir;
    let rarity = string(&card["cardRarityType"]);
    let asset = string(&card["assetbundleName"]);
    let suffix = training_suffix(istrained, rarity);
    let frame = open_image(
        &gfx.cache,
        static_dir.join(format!("chara/cardFrame_L_{rarity}.png")),
        None,
    )?;
    let (w, h) = frame.dimensions();
    let image = ctx
        .app
        .get_asset(
            &format!("startapp/character/member/{asset}"),
            &format!("card_{suffix}.png"),
            pjsk_type,
            ctx.policy,
            true,
        )
        .await?;
    let mut pic = image.map_or_else(
        || solid(w, h, [220; 3]),
        |image| imageops::resize(&*image, w, h, imageops::FilterType::CatmullRom),
    );
    decorate_card(
        &gfx,
        static_dir,
        &mut pic,
        &frame,
        rarity,
        string(&card["attr"]),
        suffix,
        true,
    )?;
    Ok(pic)
}

#[allow(clippy::too_many_arguments)]
fn decorate_card(
    gfx: &GfxContext,
    static_dir: &Path,
    pic: &mut RgbaImage,
    frame: &RgbaImage,
    rarity: &str,
    attr: &str,
    suffix: &str,
    large: bool,
) -> Result<()> {
    let (w, h) = (frame.width() as i32, frame.height() as i32);
    paste_alpha(pic, frame, (0, 0));
    let count = stars(rarity);
    if count > 0 {
        let name = if suffix == "after_training" {
            "rarity_star_afterTraining.png"
        } else {
            "rarity_star_normal.png"
        };
        let star = open_image(
            &gfx.cache,
            static_dir.join("chara").join(name),
            Some(if large { (72, 70) } else { (28, 28) }),
        )?;
        for i in (0..count).rev() {
            paste_alpha(
                pic,
                &star,
                if large {
                    (16, h - 86 - 62 * i)
                } else {
                    (8 + 25 * (count - 1 - i), h - 38)
                },
            );
        }
    } else if rarity == "rarity_birthday" {
        let star = open_image(
            &gfx.cache,
            static_dir.join("chara/rarity_birthday.png"),
            Some(if large { (72, 70) } else { (32, 31) }),
        )?;
        paste_alpha(pic, &star, if large { (16, h - 86) } else { (8, h - 40) });
    }
    let attr = open_image(
        &gfx.cache,
        static_dir.join(format!("chara/icon_attribute_{attr}.png")),
        Some(if large { (88, 88) } else { (34, 34) }),
    )?;
    paste_alpha(pic, &attr, if large { (w - 100, 12) } else { (6, 6) });
    Ok(())
}

#[derive(Clone, Copy, Debug, Default)]
pub struct CardGridData<'a> {
    pub all_cards: Option<&'a Value>,
    pub card_costumes: Option<&'a Value>,
    pub costumes: Option<&'a Value>,
    pub skills: Option<&'a Value>,
    pub game_characters: Option<&'a Value>,
    pub card_supplies: Option<&'a Value>,
    pub limited_card_ids: Option<&'a HashSet<i64>>,
    pub fes_card_ids: Option<&'a HashSet<i64>>,
    pub card_by_id: Option<&'a HashMap<i64, &'a Value>>,
    pub skill_by_id: Option<&'a HashMap<i64, &'a Value>>,
    pub pjsk_type: i32,
}

pub async fn findcardsingle(
    ctx: &RenderRequestContext,
    card: &Value,
    data: CardGridData<'_>,
) -> RgbaImage {
    render_card_cell(ctx, card, data)
        .await
        .unwrap_or_else(|_| cell_placeholder())
}

async fn render_card_cell(
    ctx: &RenderRequestContext,
    card: &Value,
    data: CardGridData<'_>,
) -> Result<RgbaImage> {
    let invalid = || DrawError::Render("Card cell has no matching card".into());
    let id = if card.is_object() {
        card["id"].as_i64()
    } else {
        integer(card)
    }
    .ok_or_else(invalid)?;
    let loaded;
    let card = if let Some(index) = data.card_by_id {
        index.get(&id).copied()
    } else if let Some(cards) = data.all_cards {
        lookup_card(cards, id)
    } else {
        loaded = ctx
            .app
            .master_data
            .load_master_data("cards.json", data.pjsk_type)?;
        lookup_card(&loaded, id)
    }
    .ok_or_else(invalid)?;

    let gfx = ctx.gfx()?;
    let static_dir = &ctx.app.paths.static_dir;
    let rarity = string(&card["cardRarityType"]);
    let (row, accent) = attr_colors(string(&card["attr"]));
    let mut pic = solid(CELL_W as u32, CELL_H as u32, row);
    rect(
        &mut pic,
        (6, 6, 414, 254),
        18.0,
        soft_color(row, 0.65),
        Some((accent, 2.0)),
    );
    rect(
        &mut pic,
        (18, 18, 402, 180),
        14.0,
        WHITE,
        Some((WHITE, 1.0)),
    );

    let limited = (if let Some(ids) = data.limited_card_ids {
        ids.contains(&id)
    } else {
        ctx.app.cardtype(
            id,
            data.card_costumes.unwrap_or(&Value::Null),
            data.costumes.unwrap_or(&Value::Null),
        ) == 1
    }) || rarity == "rarity_birthday";
    let fes = limited
        && if data.limited_card_ids.is_some() {
            data.fes_card_ids.is_some_and(|ids| ids.contains(&id))
        } else {
            ctx.app
                .is_fes_card(card, data.card_supplies, data.pjsk_type)
        };
    let views: &[(bool, i32)] = if matches!(rarity, "rarity_3" | "rarity_4") {
        &[(false, 55), (true, 215)]
    } else {
        &[(false, 135)]
    };
    for &(trained, x) in views {
        let thumbnail =
            render_thumbnail(ctx, card, trained, limited && !fes, fes, data.pjsk_type).await?;
        paste_card_thumbnail_tile(
            &gfx,
            static_dir,
            &mut pic,
            &thumbnail,
            (x, 15),
            CardTileOptions {
                size: 150,
                ..Default::default()
            },
        );
    }
    let fonts = &gfx.fonts;
    let title = card["prefix"]
        .as_str()
        .filter(|s| !s.is_empty())
        .unwrap_or(string(&card["cardName"]));
    let width = fonts.measure_text(MEDIUM, 27.0, title).0;
    let size = if width > 380.0 {
        (27.0 * 380.0 / width).floor().max(12.0)
    } else {
        27.0
    };
    let (title_width, title_height) = fonts.measure_text(MEDIUM, size, title);
    fonts.draw_text(
        &mut pic,
        (
            210.0 - title_width / 2.0,
            (198.0 - title_height / 2.0).trunc(),
        ),
        title,
        MEDIUM,
        size,
        Rgba([50, 38, 58, 255]),
        TextAnchor::La,
        None,
    );

    let chara_id = card["characterId"].as_i64().ok_or_else(invalid)?;
    let name = ctx
        .app
        .getcharaname(chara_id, data.game_characters, data.pjsk_type)?
        .unwrap_or_else(|| "None".into());
    let subtitle = format!("ID {id}  {name}");
    let width = fonts.measure_text(MEDIUM, 18.0, &subtitle).0;
    let left = 18.max((210.0 - width / 2.0) as i32 - 12);
    let right = 360.min((210.0 + width / 2.0) as i32 + 12);
    rect(
        &mut pic,
        (left, 222, right, 246),
        12.0,
        WHITE,
        Some((accent, 1.0)),
    );
    fonts.draw_text(
        &mut pic,
        (210.0 - width / 2.0, 225.0),
        &subtitle,
        MEDIUM,
        18.0,
        Rgba([120, 80, 100, 255]),
        TextAnchor::La,
        None,
    );

    if let Some(id) = card["skillId"].as_i64() {
        let skill = match data.skill_by_id {
            Some(index) => index.get(&id).copied(),
            None => data.skills.and_then(|skills| lookup_card(skills, id)),
        };
        if let Some(sprite) = skill
            .and_then(|skill| skill["descriptionSpriteName"].as_str())
            .filter(|s| !s.is_empty())
        {
            let path = static_dir.join(format!("chara/skill_{sprite}.png"));
            if path.exists() {
                let icon = open_image(&gfx.cache, path, Some((40, 40)))?;
                rect(
                    &mut pic,
                    (365, 208, 409, 252),
                    12.0,
                    WHITE,
                    Some((accent, 1.0)),
                );
                paste_alpha(&mut pic, &icon, (367, 210));
            }
        }
    }
    make_opaque(&mut pic);
    Ok(pic)
}

pub fn get_chara_icon_by_chara_id(
    ctx: &RenderRequestContext,
    chara_id: i64,
    size: Option<ImageSize>,
) -> Result<RgbaImage> {
    let name = format!("chr_sd_{chara_id:02}_01");
    let path = ctx
        .app
        .paths
        .static_dir
        .join("chara")
        .join(&name)
        .join(format!("{name}.png"));
    if !path.exists() {
        let (w, h) = size.unwrap_or((100, 100));
        return Ok(solid(w, h, [200; 3]));
    }
    Ok((*open_image(&ctx.gfx()?.cache, path, size)?).clone())
}

fn attr_colors(attr: &str) -> ([u8; 3], [u8; 3]) {
    match attr {
        "cool" => ([224, 238, 255], [78, 145, 255]),
        "cute" => ([255, 226, 240], [255, 104, 165]),
        "happy" => ([255, 242, 217], [255, 174, 75]),
        "mysterious" => ([239, 228, 255], [147, 103, 255]),
        "pure" => ([222, 247, 228], [78, 190, 105]),
        _ => ([255, 250, 252], [220, 150, 175]),
    }
}

fn unit_color(id: i64) -> [u8; 3] {
    match id {
        1..=4 => [68, 85, 221],
        5..=8 => [136, 221, 68],
        9..=12 => [238, 17, 102],
        13..=16 => [255, 153, 0],
        17..=20 => [136, 68, 153],
        _ => [60, 190, 205],
    }
}

fn soft_color(color: [u8; 3], ratio: f64) -> [u8; 3] {
    color.map(|v| (f64::from(v) * (1.0 - ratio) + 255.0 * ratio) as u8)
}

fn opaque([r, g, b]: [u8; 3]) -> Rgba<u8> {
    Rgba([r, g, b, 255])
}
fn solid(w: u32, h: u32, color: [u8; 3]) -> RgbaImage {
    RgbaImage::from_pixel(w, h, opaque(color))
}
fn make_opaque(pic: &mut RgbaImage) {
    for pixel in pic.pixels_mut() {
        pixel[3] = 255;
    }
}

fn cell_placeholder() -> RgbaImage {
    solid(CELL_W as u32, CELL_H as u32, [200; 3])
}

fn rect(
    pic: &mut RgbaImage,
    (x1, y1, x2, y2): Bounds,
    radius: f32,
    fill: [u8; 3],
    outline: Option<([u8; 3], f32)>,
) {
    draw_rounded_rect(
        pic,
        (x1 as f32, y1 as f32, x2 as f32 + 1.0, y2 as f32 + 1.0),
        radius,
        Some(opaque(fill)),
        outline.map(|(color, width)| (opaque(color), width)),
    );
}

type CardPlacement<'a> = (&'a Value, Point);

async fn paste_grid_cells(
    ctx: &RenderRequestContext,
    pic: &mut RgbaImage,
    placements: Vec<CardPlacement<'_>>,
    mut data: CardGridData<'_>,
) {
    fn index(rows: Option<&Value>) -> HashMap<i64, &Value> {
        rows.into_iter()
            .flat_map(normalize_master_list)
            .filter_map(|row| row["id"].as_i64().map(|id| (id, row)))
            .collect()
    }
    let cards = index(data.all_cards);
    let skills = index(data.skills);
    data.card_by_id = data.card_by_id.or(Some(&cards));
    data.skill_by_id = data.skill_by_id.or(Some(&skills));
    let limit = *CARD_RENDER_LIMIT;
    let semaphore = Semaphore::new(limit);
    let mut jobs = stream::iter(placements)
        .map(|(card, (x, y))| {
            let semaphore = &semaphore;
            async move {
                let _permit = semaphore
                    .acquire()
                    .await
                    .expect("grid semaphore remains open");
                (findcardsingle(ctx, card, data).await, x, y)
            }
        })
        .buffer_unordered(limit * 2);
    while let Some((card, x, y)) = jobs.next().await {
        imageops::replace(pic, &card, i64::from(x), i64::from(y));
    }
}

fn sort_cards(cards: &mut [&Value]) {
    cards.sort_by_key(|card| {
        let weight = match string(&card["cardRarityType"]) {
            "rarity_4" => 5,
            "rarity_birthday" => 4,
            rarity => stars(rarity),
        };
        Reverse((weight, card["releaseAt"].as_i64().unwrap_or_default()))
    });
}

fn round_grid_rect(pic: &mut RgbaImage, bounds: Bounds, radius: f32) {
    rect(pic, bounds, radius, WHITE, Some((WHITE, 2.0)));
}

fn draw_attr_row(
    ctx: &RenderRequestContext,
    gfx: &GfxContext,
    pic: &mut RgbaImage,
    attr: &str,
    y: i32,
    height: i32,
) {
    let right = pic.width() as i32 - PAD - 1;
    let (row, accent) = attr_colors(attr);
    rect(
        pic,
        (CONTENT_LEFT, y, right, y + height - 1),
        14.0,
        row,
        Some((WHITE, 1.0)),
    );
    rect(
        pic,
        (PAD + 6, y + 6, CONTENT_LEFT - 7, y + height - 7),
        18.0,
        soft_color(accent, 0.78),
        Some((accent, 2.0)),
    );
    let path = ctx
        .app
        .paths
        .static_dir
        .join(format!("chara/icon_attribute_{attr}.png"));
    if let Ok(icon) = open_image(&gfx.cache, path, Some((50, 50))) {
        paste_alpha(pic, &icon, (PAD + 10, y + (height - 50) / 2));
    }
}

fn line(pic: &mut RgbaImage, (x, y): Point, (x2, y2): Point, color: [u8; 3], width: f32) {
    // 线宽为奇数时偏移 0.5 像素对齐像素中心，两端点都包含
    let half = if width as u32 % 2 == 0 { 0.0 } else { 0.5 };
    let (p1, p2) = if x == x2 {
        (
            (x as f32 + half, y as f32),
            (x2 as f32 + half, y2 as f32 + 1.0),
        )
    } else {
        (
            (x as f32, y as f32 + half),
            (x2 as f32 + 1.0, y2 as f32 + half),
        )
    };
    draw_line(pic, p1, p2, opaque(color), width);
}

pub async fn build_attr_grouped_image(
    ctx: &RenderRequestContext,
    target_cards: &[Value],
    data: CardGridData<'_>,
) -> Result<RgbaImage> {
    const MAX_COLS: usize = 20;
    const COL_GAP: i32 = 2;
    const ROW_GAP: i32 = 10;
    const ATTR_GAP: i32 = 6;

    let mut groups: Vec<_> = ATTR_ORDER
        .into_iter()
        .filter_map(|attr| {
            let mut cards: Vec<_> = target_cards
                .iter()
                .filter(|card| card["attr"] == attr)
                .collect();
            if cards.is_empty() {
                return None;
            }
            sort_cards(&mut cards);
            let rows = cards.len().div_ceil(MAX_COLS) as i32;
            Some((attr, cards, rows * (CELL_H + ROW_GAP) - ROW_GAP))
        })
        .collect();
    let Some(cols) = groups
        .iter()
        .map(|(_, cards, _)| cards.len().min(MAX_COLS))
        .max()
    else {
        return Ok(solid(436, 292, [255, 245, 248]));
    };
    let width = CONTENT_LEFT + cols as i32 * (CELL_W + COL_GAP) - COL_GAP + PAD;
    let height = 2 * PAD + groups.iter().map(|(_, _, h)| *h + ATTR_GAP).sum::<i32>() - ATTR_GAP;
    let mut pic = vertical_gradient(
        width as u32,
        height as u32,
        [255, 246, 250],
        [236, 244, 255],
    );
    let gfx = ctx.gfx()?;
    round_grid_rect(
        &mut pic,
        (PAD, PAD, CONTENT_LEFT - 1, height - PAD - 1),
        20.0,
    );
    let mut y = PAD;
    let mut row_ends = Vec::new();
    let mut placements = Vec::new();
    for (attr, cards, block_height) in groups.drain(..) {
        draw_attr_row(ctx, &gfx, &mut pic, attr, y, block_height);
        for (i, card) in cards.into_iter().enumerate() {
            placements.push((
                card,
                (
                    CONTENT_LEFT + (i % MAX_COLS) as i32 * (CELL_W + COL_GAP),
                    y + (i / MAX_COLS) as i32 * (CELL_H + ROW_GAP),
                ),
            ));
        }
        y += block_height;
        row_ends.push(y);
        y += ATTR_GAP;
    }
    make_opaque(&mut pic);
    paste_grid_cells(ctx, &mut pic, placements, data).await;
    line(
        &mut pic,
        (CONTENT_LEFT - 1, PAD),
        (CONTENT_LEFT - 1, height - PAD - 1),
        LINE_HEADER,
        4.0,
    );
    row_ends.pop();
    for y in row_ends {
        line(&mut pic, (PAD, y), (width - PAD - 1, y), LINE_HEAVY, 2.0);
    }
    for col in 1..cols {
        let x = CONTENT_LEFT + col as i32 * (CELL_W + COL_GAP) - COL_GAP;
        line(&mut pic, (x, PAD), (x, height - PAD - 1), LINE_LIGHT, 2.0);
    }
    Ok(pic)
}

const UNIT_GAP: i32 = 8;

#[derive(Clone, Copy)]
struct CellLayout {
    cols: usize,
    width: i32,
    height: i32,
}

fn near_square_grid(count: usize) -> CellLayout {
    (1..=count)
        .map(|cols| {
            let rows = count.div_ceil(cols);
            CellLayout {
                cols,
                width: cols as i32 * (CELL_W + UNIT_GAP) - UNIT_GAP,
                height: rows as i32 * (CELL_H + UNIT_GAP) - UNIT_GAP,
            }
        })
        .min_by_key(|grid| {
            (
                (grid.width - grid.height).abs(),
                i64::from(grid.width) * i64::from(grid.height),
                grid.cols,
            )
        })
        .unwrap_or(CellLayout {
            cols: 0,
            width: CELL_W,
            height: CELL_H,
        })
}

pub async fn build_unit_grouped_image(
    ctx: &RenderRequestContext,
    target_cards: &[Value],
    _unit_internal: &str,
    ordered_chars: &[i64],
    data: CardGridData<'_>,
) -> Result<RgbaImage> {
    const AVATAR_ROW_H: i32 = 100;
    const ATTR_GAP: i32 = 6;

    let mut grouped = HashMap::<(i64, &str), Vec<&Value>>::new();
    let valid_chars: HashSet<_> = ordered_chars.iter().copied().collect();
    for card in target_cards {
        let attr = string(&card["attr"]);
        if let Some(id) = card["characterId"].as_i64()
            && valid_chars.contains(&id)
            && ATTR_ORDER.contains(&attr)
        {
            grouped.entry((id, attr)).or_default().push(card);
        }
    }
    if grouped.is_empty() {
        return Ok(solid(490, 392, [255, 245, 248]));
    }
    for cards in grouped.values_mut() {
        sort_cards(cards);
    }
    let chars: Vec<_> = ordered_chars
        .iter()
        .copied()
        .filter(|id| {
            ATTR_ORDER
                .iter()
                .any(|attr| grouped.contains_key(&(*id, *attr)))
        })
        .collect();
    let attrs: Vec<_> = ATTR_ORDER
        .into_iter()
        .filter(|attr| chars.iter().any(|id| grouped.contains_key(&(*id, *attr))))
        .collect();
    let layouts: HashMap<_, _> = chars
        .iter()
        .flat_map(|&id| attrs.iter().map(move |&attr| (id, attr)))
        .map(|key| (key, near_square_grid(grouped.get(&key).map_or(0, Vec::len))))
        .collect();

    let mut x = CONTENT_LEFT;
    let columns: Vec<_> = chars
        .into_iter()
        .map(|id| {
            let w = attrs
                .iter()
                .map(|&attr| layouts[&(id, attr)].width)
                .max()
                .unwrap_or(CELL_W);
            let column = (id, x, w);
            x += w + UNIT_GAP;
            column
        })
        .collect();
    let width = x - UNIT_GAP + PAD;
    let content_top = PAD + AVATAR_ROW_H;
    let mut y = content_top;
    let rows: Vec<_> = attrs
        .into_iter()
        .map(|attr| {
            let h = columns
                .iter()
                .map(|&(id, _, _)| layouts[&(id, attr)].height)
                .max()
                .unwrap_or(CELL_H);
            let row = (attr, y, h);
            y += h + ATTR_GAP;
            row
        })
        .collect();
    let height = y - ATTR_GAP + PAD;
    let mut pic = vertical_gradient(
        width as u32,
        height as u32,
        [255, 246, 250],
        [236, 244, 255],
    );
    let gfx = ctx.gfx()?;
    round_grid_rect(&mut pic, (PAD, PAD, width - PAD, content_top - 1), 22.0);
    round_grid_rect(
        &mut pic,
        (PAD, PAD, CONTENT_LEFT - 1, height - PAD - 1),
        20.0,
    );
    for &(attr, y, h) in &rows {
        draw_attr_row(ctx, &gfx, &mut pic, attr, y, h);
    }
    for &(id, x, w) in &columns {
        let color = unit_color(id);
        rect(
            &mut pic,
            (x + 8, PAD + 8, x + w - 8, content_top - 9),
            22.0,
            soft_color(color, 0.82),
            Some((color, 2.0)),
        );
        let path = ctx
            .app
            .paths
            .static_dir
            .join(format!("chara/chr_ts_{id}.png"));
        match open_image(&gfx.cache, path, Some((80, 80))) {
            Ok(avatar) => paste_alpha(
                &mut pic,
                &avatar,
                (x + (w - 80) / 2, PAD + (AVATAR_ROW_H - 80) / 2),
            ),
            Err(_) => {
                let name = ctx
                    .app
                    .getcharaname(id, data.game_characters, data.pjsk_type)?
                    .filter(|name| !name.is_empty())
                    .unwrap_or_else(|| id.to_string());
                gfx.fonts.draw_text(
                    &mut pic,
                    ((x + w / 2) as f32, (PAD + (AVATAR_ROW_H - 18) / 2) as f32),
                    &name,
                    MEDIUM,
                    18.0,
                    opaque(color),
                    TextAnchor::Ma,
                    None,
                );
            }
        }
    }
    let mut placements = Vec::new();
    for &(attr, y, h) in &rows {
        for &(id, x, w) in &columns {
            let Some(cards) = grouped.get(&(id, attr)) else {
                continue;
            };
            let layout = layouts[&(id, attr)];
            let base_x = x + (w - layout.width) / 2;
            let base_y = y + (h - layout.height) / 2;
            for (i, &card) in cards.iter().enumerate() {
                placements.push((
                    card,
                    (
                        base_x + (i % layout.cols) as i32 * (CELL_W + UNIT_GAP),
                        base_y + (i / layout.cols) as i32 * (CELL_H + UNIT_GAP),
                    ),
                ));
            }
        }
    }
    make_opaque(&mut pic);
    paste_grid_cells(ctx, &mut pic, placements, data).await;
    line(
        &mut pic,
        (PAD, content_top - 1),
        (width - PAD - 1, content_top - 1),
        LINE_HEADER,
        4.0,
    );
    line(
        &mut pic,
        (CONTENT_LEFT - 1, PAD),
        (CONTENT_LEFT - 1, height - PAD - 1),
        LINE_HEADER,
        4.0,
    );
    for &(_, y, h) in &rows[..rows.len() - 1] {
        line(
            &mut pic,
            (PAD, y + h),
            (width - PAD - 1, y + h),
            LINE_HEAVY,
            3.0,
        );
    }
    for &(_, x, _) in columns.iter().skip(1) {
        let x = x - UNIT_GAP / 2;
        line(&mut pic, (x, PAD), (x, height - PAD - 1), LINE_LIGHT, 2.0);
    }
    Ok(pic)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn thumbnail_tile_rounds_corners_and_differentiates_unowned_cards() {
        let gfx = GfxContext::load("/nonexistent/pjsk-test-fonts").unwrap();
        let thumb = solid(24, 24, [220, 60, 35]);
        let options = CardTileOptions {
            size: 84,
            ..Default::default()
        };
        let tile = render_card_thumbnail_tile(&gfx, Path::new(""), &thumb, options);
        let unowned = render_card_thumbnail_tile(
            &gfx,
            Path::new(""),
            &thumb,
            CardTileOptions {
                has_card: false,
                ..options
            },
        );
        assert_eq!(tile.dimensions(), (84, 84));
        assert_eq!(tile.get_pixel(0, 0)[3], 0);
        assert_eq!(*tile.get_pixel(42, 42), opaque([220, 60, 35]));
        assert!(unowned.get_pixel(42, 42)[0] < tile.get_pixel(42, 42)[0]);
    }
}
