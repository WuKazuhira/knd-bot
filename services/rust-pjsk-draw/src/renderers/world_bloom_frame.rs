use std::{
    collections::{HashSet, hash_map::RandomState},
    hash::BuildHasher,
};

use crate::components::{
    generatehonor,
    ui::{get_pjsk_asset_resized, paste_round_image},
};
use crate::core::{DrawError, RenderOutput, RenderRequestContext, Renderer, Result};
use crate::gfx::{
    GfxContext, RenderContextGfxExt, TextAnchor, image_to_png, paste_rgba, resize_lanczos,
};
use async_trait::async_trait;
use image::{Rgba, RgbaImage, imageops::crop_imm};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::renderers::common::*;

pub(crate) struct WorldBloomFrame;

const WIDTH: i32 = 341;
const HEIGHT: i32 = 668;
const LAYERS: [(usize, &str); 9] = [
    (2, "left"),
    (3, "right"),
    (4, "left"),
    (5, "right"),
    (1, "left"),
    (1, "right"),
    (6, "left"),
    (6, "right"),
    (1, "center"),
];

#[derive(Default, Deserialize)]
#[serde(default)]
struct Payload {
    frame_id: Value,
    color: Value,
    #[serde(deserialize_with = "deserialize_ids")]
    character_ids: Vec<i64>,
    pjsk_type: i32,
    card_id: Value,
    leader_card_id: Value,
    leader_trained: Option<bool>,
    trained: Option<bool>,
    leader_level: Value,
    leader_master_rank: Value,
    player_name: Value,
    profile_name: Value,
    honors: Value,
}

fn frame_id_for_color(color: &str) -> Result<i64> {
    match color.trim().to_ascii_lowercase().as_str() {
        "100" | "tb" => Ok(20001),
        "1000" | "tk" => Ok(20002),
        _ => Err(DrawError::InvalidPayload(
            "边框颜色仅支持 100/tb 或 1000/tk".into(),
        )),
    }
}

fn expand_character_ids(ids: &[i64]) -> Result<[i64; 6]> {
    let expanded = match ids {
        [id] => [*id; 6],
        [a, b, c, d, e, f] => [*a, *b, *c, *d, *e, *f],
        _ => {
            return Err(DrawError::InvalidPayload(
                "角色缩写需要正好提供 1 个或 6 个".into(),
            ));
        }
    };
    if expanded.iter().all(|id| (1..=26).contains(id)) {
        Ok(expanded)
    } else {
        Err(DrawError::InvalidPayload(
            "角色缩写无法映射到有效角色".into(),
        ))
    }
}

fn resolve_frame_group<'a>(id: i64, frames: &[Value], groups: &'a [Value]) -> Result<&'a Value> {
    let frame = frames
        .iter()
        .find(|row| number(&row["id"]) == id)
        .ok_or_else(|| render_error(format!("找不到边框 ID {id}")))?;
    if number(&frame["partsCount"]) != 6 {
        return Err(render_error(format!("边框 ID {id} 不是六部件边框")));
    }
    let group = number(&frame["playerFrameGroupId"]);
    groups
        .iter()
        .find(|row| number(&row["id"]) == group && !string(&row["assetbundleName"]).is_empty())
        .ok_or_else(|| render_error(format!("找不到边框组 {group} 的资源映射")))
}

fn resolve_frame_parts(group: i64, ids: [i64; 6], parts: &[Value]) -> Result<Vec<i64>> {
    ids.into_iter()
        .enumerate()
        .map(|(i, id)| {
            parts
                .iter()
                .find(|row| {
                    number(&row["playerFrameGroupId"]) == group
                        && number(&row["gameCharacterId"]) == id
                })
                .map(|row| number(&row["id"]))
                .filter(|&id| id > 0)
                .ok_or_else(|| render_error(format!("part{} 找不到角色 ID {id} 的边框部件", i + 1)))
        })
        .collect()
}

fn choose_card<'a>(
    cards: &'a [Value],
    payload: &Payload,
    character: i64,
    random: &RandomState,
) -> Result<(&'a Value, bool)> {
    let explicit = number(&payload.card_id);
    let leader = number(&payload.leader_card_id);
    let usable = |row: &&Value| !string(&row["assetbundleName"]).is_empty() && !row["id"].is_null();
    let lookup = |id| {
        cards
            .iter()
            .rev()
            .filter(usable)
            .find(|row| number(&row["id"]) == id)
    };
    let (card, default_trained) = if explicit != 0 {
        (
            lookup(explicit)
                .ok_or_else(|| render_error(format!("卡牌 ID {explicit} 不存在或没有立绘资源")))?,
            false,
        )
    } else if let Some(card) = (leader != 0).then(|| lookup(leader)).flatten() {
        (card, payload.leader_trained.unwrap_or_default())
    } else {
        let all: Vec<_> = cards.iter().filter(usable).collect();
        let matching: Vec<_> = all
            .iter()
            .copied()
            .filter(|row| number(&row["characterId"]) == character)
            .collect();
        let pool = if matching.is_empty() { &all } else { &matching };
        if pool.is_empty() {
            return Err(render_error("cards.json 中没有可用卡牌"));
        }
        (
            pool[random.hash_one("card") as usize % pool.len()],
            random.hash_one("trained") % 2 == 0,
        )
    };
    let trained = matches!(string(&card["cardRarityType"]), "rarity_3" | "rarity_4")
        && payload.trained.unwrap_or(default_trained);
    Ok((card, trained))
}

fn compose_ring(layers: &[RgbaImage]) -> RgbaImage {
    let mut ring = RgbaImage::new(WIDTH as u32, HEIGHT as u32);
    let upper = 40.min(HEIGHT / 2 - 8 - alpha_bounds(&layers[0], 60).3 as i32);
    for (&(part, side), layer) in LAYERS.iter().zip(layers) {
        let (left, top, width, height) = alpha_bounds(layer, 60);
        let (left, top, width, height) = (left as i32, top as i32, width as i32, height as i32);
        let x = match side {
            "left" => -left,
            "right" => WIDTH - left - width,
            _ => ((WIDTH - width) as f32 / 2.).round_ties_even() as i32 - left,
        };
        let y = match part {
            1 => 0,
            2 | 3 => upper,
            4 | 5 => HEIGHT / 2 + 8,
            _ => HEIGHT - height,
        };
        paste_rgba(&mut ring, layer, x, y - top);
    }
    ring
}

fn cover(image: &RgbaImage, width: u32, height: u32) -> RgbaImage {
    if image.is_empty() {
        return RgbaImage::new(width, height);
    }
    let (x, y, w, h) = alpha_bounds(image, 0);
    let scale = (width as f64 / w as f64).max(height as f64 / h as f64);
    let image = resize_lanczos(
        &crop_imm(image, x, y, w, h).to_image(),
        ((w as f64 * scale).round() as u32).max(width),
        ((h as f64 * scale).round() as u32).max(height),
    );
    crop_imm(
        &image,
        (image.width() - width) / 2,
        ((image.height() - height) as f64 * 0.04).round() as u32,
        width,
        height,
    )
    .to_image()
}

fn honor_choices<'a>(
    character: i64,
    chapters: &[Value],
    events: &[Value],
    groups: &[Value],
    honors: &'a [Value],
) -> Vec<&'a Value> {
    let mut names = HashSet::new();
    for chapter in chapters.iter().filter(|row| {
        number(&row["gameCharacterId"]) == character
            && row["worldBloomChapterType"] == "game_character"
    }) {
        if let Some(event) = events
            .iter()
            .find(|row| number(&row["id"]) == number(&chapter["eventId"]))
        {
            let name = string(&event["name"]);
            if !name.is_empty() {
                names.insert(format!("{name} 総合"));
                names.insert(format!(
                    "{name} チャプター{}",
                    number(&chapter["chapterNo"])
                ));
            }
        }
    }
    let groups: HashSet<_> = groups
        .iter()
        .filter(|row| row["honorType"] == "event" && names.contains(string(&row["name"])))
        .map(|row| number(&row["id"]))
        .collect();
    let mut seen = HashSet::new();
    honors
        .iter()
        .filter(|row| {
            groups.contains(&number(&row["groupId"]))
                && string(&row["assetbundleName"]).starts_with("honor_top_000100")
                && seen.insert(number(&row["id"]))
        })
        .collect()
}

async fn draw_honors(
    ctx: &RenderRequestContext,
    choices: Vec<Value>,
    server: i32,
) -> Vec<RgbaImage> {
    let mut images = Vec::new();
    for honor in choices {
        if let Ok(Some(image)) = generatehonor(ctx, &honor, images.is_empty(), &[], server).await {
            images.push(image);
        }
        if images.len() == 3 {
            break;
        }
    }
    images
}

async fn fallback_honors(
    ctx: &RenderRequestContext,
    character: i64,
    server: i32,
    random: &RandomState,
) -> Vec<RgbaImage> {
    let load = |file| ctx.app.master_data.load_master_data(file, server);
    let (Ok(chapters), Ok(events), Ok(groups), Ok(honors)) = (
        load("worldBlooms.json"),
        load("events.json"),
        load("honorGroups.json"),
        load("honors.json"),
    ) else {
        return Vec::new();
    };
    let mut choices = honor_choices(
        character,
        rows(&chapters),
        rows(&events),
        rows(&groups),
        rows(&honors),
    );
    choices.sort_by_key(|row| random.hash_one(number(&row["id"])));
    draw_honors(
        ctx,
        choices.into_iter().map(
            |row| json!({"honorId":number(&row["id"]),"profileHonorType":"normal","honorLevel":1}),
        ).collect(),
        server,
    )
    .await
}

#[async_trait]
impl Renderer for WorldBloomFrame {
    fn name(&self) -> &'static str {
        "world_bloom_frame"
    }

    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput> {
        let payload: Payload = parse_payload(payload)?;
        let ids = expand_character_ids(&payload.character_ids)?;
        let frame_id = if payload.frame_id.is_null() && !payload.color.is_null() {
            let color = payload
                .color
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| payload.color.to_string());
            frame_id_for_color(&color)?
        } else {
            number(&payload.frame_id)
        };
        let server = payload.pjsk_type;
        let load = |file| ctx.app.master_data.load_master_data(file, server);
        let frames = load("playerFrames.json")?;
        let groups = load("playerFrameGroups.json")?;
        let parts = load("playerFrameParts.json")?;
        let cards = load("cards.json")?;
        let group = resolve_frame_group(frame_id, rows(&frames), rows(&groups))?;
        let parts = resolve_frame_parts(number(&group["id"]), ids, rows(&parts))?;
        let group_name = string(&group["assetbundleName"]);
        let random = RandomState::new();
        let (card, trained) = choose_card(rows(&cards), &payload, ids[0], &random)?;
        let category = format!(
            "startapp/character/member_cutout/{}",
            string(&card["assetbundleName"])
        );
        let filename = if trained {
            "after_training.png"
        } else {
            "normal.png"
        };
        let mut assets = tokio::task::JoinSet::new();
        for (index, (part, side)) in LAYERS.into_iter().enumerate() {
            let category = format!(
                "startapp/player_frame/{group_name}/{frame_id}/{}/vertical",
                parts[part - 1]
            );
            let filename = format!("frame_parts{part}_{side}.png");
            let ctx = ctx.clone();
            assets.spawn(async move {
                let image = get_pjsk_asset_resized(&ctx, &category, &filename, server, None)
                    .await?
                    .ok_or_else(|| {
                        render_error(format!(
                            "边框 part{part} {side} 资源缺失：{category}/{filename}"
                        ))
                    })?;
                Ok::<_, DrawError>((index, image))
            });
        }
        let illustration = get_pjsk_asset_resized(ctx, &category, filename, server, None)
            .await?
            .ok_or_else(|| render_error(format!("立绘资源缺失：{category}/{filename}")))?;
        let mut layers = vec![RgbaImage::new(0, 0); LAYERS.len()];
        while let Some(result) = assets.join_next().await {
            let (index, image) = result.map_err(render_error)??;
            layers[index] = image;
        }
        let rarity = string(&card["cardRarityType"]).to_owned();
        let character = match number(&card["characterId"]) {
            0 => ids[0],
            id => id,
        };
        let leader = number(&payload.leader_card_id);
        let is_leader = leader != 0 && number(&card["id"]) == leader;
        let level = if is_leader && number(&payload.leader_level) > 0 {
            number(&payload.leader_level)
        } else {
            match rarity.as_str() {
                "rarity_1" => 20,
                "rarity_2" => 30,
                "rarity_3" => 50,
                _ => 60,
            }
        };
        let rank = if is_leader && !payload.leader_master_rank.is_null() {
            number(&payload.leader_master_rank)
        } else {
            1 + (random.hash_one("rank") % 5) as i64
        };
        let default_name = ctx
            .app
            .getcharaname(character, None, server)
            .ok()
            .flatten()
            .unwrap_or_default();
        let profile_name = if is_leader {
            string(&payload.profile_name).trim()
        } else {
            ""
        };
        let name = [
            string(&payload.player_name).trim(),
            profile_name,
            &default_name,
            "Player",
        ]
        .into_iter()
        .find(|name| !name.is_empty())
        .unwrap_or("Player")
        .to_owned();
        let mut honors = Vec::new();
        if is_leader {
            let mut given = rows(&payload.honors).to_vec();
            given.sort_by_key(|row| row["seq"].as_i64().unwrap_or(9));
            honors = draw_honors(ctx, given, server).await;
        }
        if honors.is_empty() {
            honors = fallback_honors(ctx, character, server, &random).await;
        }
        let gfx = ctx.gfx()?;
        let static_dir = ctx.app.paths.static_dir.clone();
        tokio::task::spawn_blocking(move || {
            let icon = |name: &str, size| {
                gfx.cache
                    .open_pjsk_image(static_dir.join(format!("chara/{name}.png")), Some(size))
                    .ok()
            };
            let stars = if rarity == "rarity_birthday" {
                icon("rarity_birthday", (28, 27)).map(|img| (*img).clone())
            } else if let Some(count) = rarity
                .strip_prefix("rarity_")
                .and_then(|s| s.parse::<u32>().ok())
                .filter(|n| (1..=4).contains(n))
            {
                icon(
                    if trained {
                        "rarity_star_afterTraining"
                    } else {
                        "rarity_star_normal"
                    },
                    (24, 24),
                )
                .map(|star| {
                    let mut row = RgbaImage::new(25 * (count - 1) + 24, 24);
                    for i in 0..count {
                        paste_rgba(&mut row, &star, (i * 25) as i32, 0);
                    }
                    row
                })
            } else {
                None
            };
            let master = (rank > 0)
                .then(|| icon(&format!("train_rank_{rank}"), (54, 54)))
                .flatten();
            let image = compose(
                &gfx,
                &compose_ring(&layers),
                &illustration,
                stars.as_ref(),
                master.as_deref(),
                level,
                &name,
                &honors,
            );
            image_to_png(&image, false, &gfx.fonts)
                .map(RenderOutput::single)
                .map_err(render_error)
        })
        .await
        .map_err(render_error)?
    }
}

#[allow(clippy::too_many_arguments)]
fn compose(
    gfx: &GfxContext,
    ring: &RgbaImage,
    illustration: &RgbaImage,
    stars: Option<&RgbaImage>,
    master: Option<&RgbaImage>,
    level: i64,
    name: &str,
    honors: &[RgbaImage],
) -> RgbaImage {
    let mut canvas = RgbaImage::new(WIDTH as u32, HEIGHT as u32);
    paste_round_image(
        &gfx.cache,
        &mut canvas,
        &cover(illustration, 313, 464),
        (14, 6),
        (313, 464),
        16,
    );
    let scrim = RgbaImage::from_fn(WIDTH as u32, (HEIGHT - 352) as u32, |_, y| {
        rgba([20, 26, 44], (150 * y.min(90) / 90) as u8)
    });
    paste_rgba(&mut canvas, &scrim, 0, 352);
    rounded_panel_overlay(
        gfx,
        &mut canvas,
        (24, 442, 316, 633),
        20,
        rgba([247, 248, 251], 245),
        Some(rgba([214, 221, 233], 255)),
    );
    if let Some(stars) = stars {
        paste_rgba(&mut canvas, stars, 44, 398 - stars.height() as i32 / 2);
    }
    if let Some(master) = master {
        paste_rgba(&mut canvas, master, 261, 381);
    }
    let stroke = Some((2, rgba([40, 48, 70], 220)));
    gfx.fonts.draw_text(
        &mut canvas,
        (44., 428.),
        "Lv.",
        RODIN,
        18.,
        Rgba([255; 4]),
        TextAnchor::Lm,
        stroke,
    );
    gfx.fonts.draw_text(
        &mut canvas,
        (74., 427.),
        &level.to_string(),
        RODIN,
        26.,
        Rgba([255; 4]),
        TextAnchor::Lm,
        stroke,
    );
    let name = fit_text(&gfx.fonts, name, BOLD, 30., 273.);
    gfx.fonts.draw_text(
        &mut canvas,
        (170., 468.),
        &name,
        BOLD,
        30.,
        rgba([52, 62, 88], 255),
        TextAnchor::Mm,
        None,
    );
    if let Some(main) = honors.first() {
        paste_rgba(
            &mut canvas,
            &resize_lanczos(main, 250, 52),
            (WIDTH - 250) / 2,
            492,
        );
    }
    let subs = honors.len().saturating_sub(1).min(2) as i32;
    for (i, honor) in honors.iter().skip(1).take(2).enumerate() {
        paste_rgba(
            &mut canvas,
            &resize_lanczos(honor, 118, 52),
            (WIDTH - (130 * subs - 12)) / 2 + 130 * i as i32,
            558,
        );
    }
    let mask = gfx
        .cache
        .get_rounded_rect_mask(((WIDTH - 10) as u32, (HEIGHT - 10) as u32), 30);
    for (x, y, pixel) in canvas.enumerate_pixels_mut() {
        let alpha = if x >= 5 && y >= 5 && x < WIDTH as u32 - 5 && y < HEIGHT as u32 - 5 {
            mask[(x - 5, y - 5)][0]
        } else {
            0
        };
        pixel[3] = (u16::from(pixel[3]) * u16::from(alpha) / 255) as u8;
    }
    paste_rgba(&mut canvas, ring, 0, 0);
    resize_lanczos(&canvas, (WIDTH * 2) as u32, (HEIGHT * 2) as u32)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_parts_and_resolves_frame() {
        let input: Payload = parse_payload(json!({"character_ids":["3"],"color":" TK "})).unwrap();
        let ids = expand_character_ids(&input.character_ids).unwrap();
        assert_eq!(ids, [3; 6]);
        assert!(expand_character_ids(&[27]).is_err());
        let id = frame_id_for_color(string(&input.color)).unwrap();
        let frames = json!([{"id":id,"partsCount":6,"playerFrameGroupId":12}]);
        let groups = json!([{"id":12,"assetbundleName":"wb"}]);
        let parts = json!([{"id":42,"playerFrameGroupId":12,"gameCharacterId":3}]);
        let group = resolve_frame_group(id, rows(&frames), rows(&groups)).unwrap();
        assert_eq!(
            resolve_frame_parts(number(&group["id"]), ids, rows(&parts)).unwrap(),
            vec![42; 6]
        );
    }

    #[tokio::test]
    async fn composes_six_offline_parts_into_transparent_png() {
        let fixture = RenderFixture::new();
        fixture.write(
            "ondemand/jp/playerFrames.json",
            r#"[{"id":20001,"partsCount":6,"playerFrameGroupId":12}]"#,
        );
        fixture.write(
            "ondemand/jp/playerFrameGroups.json",
            r#"[{"id":12,"assetbundleName":"wb"}]"#,
        );
        fixture.write(
            "ondemand/jp/playerFrameParts.json",
            r#"[{"id":42,"playerFrameGroupId":12,"gameCharacterId":3}]"#,
        );
        fixture.write(
            "ondemand/jp/cards.json",
            r#"[{"id":1,"characterId":3,"cardRarityType":"rarity_4","assetbundleName":"test"}]"#,
        );
        fixture.png(
            "ondemand/jp/startapp/character/member_cutout/test/after_training.png",
            (80, 120),
        );
        for (part, side) in LAYERS {
            fixture.png(&format!("ondemand/jp/startapp/player_frame/wb/20001/42/vertical/frame_parts{part}_{side}.png"),(12,16));
        }
        let ctx = fixture.context();
        let output = WorldBloomFrame.render(&ctx,json!({
            "frame_id":20001,"character_ids":[3],"card_id":1,"trained":true,"player_name":"Player"
        })).await.unwrap();
        let image = image::load_from_memory_with_format(&output.images[0], image::ImageFormat::Png)
            .unwrap()
            .into_rgba8();
        assert_eq!(image.dimensions(), (682, 1336));
        assert_eq!(image[(40, 10)][3], 0);
        assert_eq!(image[(8, 10)][3], 255);
        assert_eq!(image[(200, 200)][3], 255);
    }
}
