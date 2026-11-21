use crate::core::{DrawError, RenderRequestContext, Result};
use crate::gfx::{RenderContextGfxExt, TextAnchor, resize_lanczos};
use image::{Rgba, RgbaImage, imageops};
use serde_json::Value;

use crate::components::{
    normalize_master_list, string,
    ui::{open_image, paste_alpha},
};

pub async fn generatehonor(
    ctx: &RenderRequestContext,
    honor: &Value,
    ismain: bool,
    user_honor_missions: &[Value],
    pjsk_type: i32,
) -> Result<Option<RgbaImage>> {
    let kind = honor
        .get("profileHonorType")
        .and_then(Value::as_str)
        .unwrap_or("normal");
    if kind == "bonds" {
        return render_bonds(ctx, honor, ismain, pjsk_type).await.map(Some);
    }
    if kind != "normal" {
        return Ok(None);
    }
    let store = &ctx.app.master_data;
    let honors = store.load_master_data("honors.json", pjsk_type)?;
    let groups = store.load_master_data("honorGroups.json", pjsk_type)?;
    let row = find_honor(&honors, honor)?;
    let group = normalize_master_list(&groups)
        .into_iter()
        .find(|group| group["id"] == row["groupId"]);
    let level = honor["honorLevel"].as_i64().unwrap_or_default();
    let live = row.get("assetbundleName").is_none() || row.get("honorRarity").is_none();
    let (art, background, honor_type) = if live {
        let art = normalize_master_list(&row["levels"])
            .into_iter()
            .find(|art| art["level"] == honor["honorLevel"])
            .unwrap_or(&Value::Null);
        (art, "", "")
    } else {
        (
            row,
            group.map_or("", |group| string(&group["backgroundAssetbundleName"])),
            group.map_or("", |group| string(&group["honorType"])),
        )
    };
    let rarity = string(&art["honorRarity"]);
    let bundle = string(&art["assetbundleName"]);
    let (category, rank_name) = match (honor_type, live, ismain) {
        ("rank_match", _, true) => ("rank_live/honor", "main.png"),
        ("rank_match", _, false) => ("rank_live/honor", "sub.png"),
        (_, true, _) => ("honor", "scroll.png"),
        (_, _, true) => ("honor", "rank_main.png"),
        _ => ("honor", "rank_sub.png"),
    };
    let degree = if ismain {
        "degree_main.png"
    } else {
        "degree_sub.png"
    };
    let background_bundle = if background.is_empty() {
        bundle
    } else {
        background
    };
    let Some(mut pic) = ctx
        .app
        .get_asset(
            &format!("startapp/{category}/{background_bundle}"),
            degree,
            pjsk_type,
            ctx.policy,
            true,
        )
        .await?
        .map(|image| (*image).clone())
    else {
        return Ok(None);
    };
    let rank_category = format!("startapp/{category}/{bundle}");
    let rank = async {
        let mut rank = None;
        if rank_name != "rank_sub.png" {
            rank = ctx
                .app
                .get_asset(&rank_category, rank_name, pjsk_type, ctx.policy, true)
                .await?;
        }
        if rank.is_none() && !ismain && !background.is_empty() {
            rank = ctx
                .app
                .get_asset(&rank_category, "rank_main.png", pjsk_type, ctx.policy, true)
                .await?;
        }
        Ok::<_, DrawError>(rank)
    }
    .await;
    let rank = if ismain && !background.is_empty() {
        rank?
    } else {
        rank.ok().flatten()
    };
    paste_frame(ctx, &mut pic, ismain, rarity)?;
    if let Some(rank) = rank {
        if live {
            paste_live_rank(
                ctx,
                &mut pic,
                &rank,
                ismain,
                &row["honorMissionType"],
                user_honor_missions,
            )?;
        } else if ismain {
            let x = if rank.width() >= pic.width().saturating_sub(20) {
                0
            } else {
                190
            };
            paste_alpha(&mut pic, &rank, (x, 0));
        } else if background.is_empty() {
            paste_alpha(&mut pic, &rank, (34, 42));
        } else {
            let scale = (f64::from(pic.width().saturating_sub(12)) / f64::from(rank.width()))
                .min(f64::from(pic.height() / 2) / f64::from(rank.height()))
                .min(1.0);
            let w = (f64::from(rank.width()) * scale).round_ties_even().max(1.0) as u32;
            let h = (f64::from(rank.height()) * scale)
                .round_ties_even()
                .max(1.0) as u32;
            let pos = (
                (pic.width() as i32 - w as i32).div_euclid(2),
                pic.height() as i32 - h as i32 - 6,
            );
            paste_alpha(&mut pic, &resize_lanczos(&rank, w, h), pos);
        }
    }
    if matches!(honor_type, "character" | "achievement") {
        paste_levels(ctx, &mut pic, if level > 10 { level - 10 } else { level })?;
    }
    Ok(Some(pic))
}

fn find_honor<'a>(rows: &'a Value, honor: &Value) -> Result<&'a Value> {
    normalize_master_list(rows)
        .into_iter()
        .find(|row| row["id"] == honor["honorId"] && row["id"].is_number())
        .ok_or_else(|| DrawError::Render(format!("找不到对应honor资源 {}", honor["honorId"])))
}

fn paste_frame(
    ctx: &RenderRequestContext,
    pic: &mut RgbaImage,
    ismain: bool,
    rarity: &str,
) -> Result<()> {
    let gfx = ctx.gfx()?;
    let size = if ismain { 'm' } else { 's' };
    let number = match rarity {
        "low" => 1,
        "middle" => 2,
        "high" => 3,
        _ => 4,
    };
    let frame = open_image(
        &gfx.cache,
        ctx.app
            .paths
            .static_dir
            .join(format!("pics/frame_degree_{size}_{number}.png")),
        None,
    )?;
    paste_alpha(pic, &frame, (if number == 1 { 8 } else { 0 }, 0));
    Ok(())
}

fn paste_levels(ctx: &RenderRequestContext, pic: &mut RgbaImage, level: i64) -> Result<()> {
    let gfx = ctx.gfx()?;
    let static_dir = &ctx.app.paths.static_dir;
    for (name, count) in [
        ("icon_degreeLv.png", level.min(5)),
        ("icon_degreeLv6.png", level.saturating_sub(5)),
    ] {
        if count <= 0 {
            continue;
        }
        let icon = open_image(&gfx.cache, static_dir.join("pics").join(name), None)?;
        for i in 0..count.min(i64::from(pic.width()).div_euclid(16) + 1) {
            paste_alpha(pic, &icon, (54 + 16 * i as i32, 63));
        }
    }
    Ok(())
}

fn paste_live_rank(
    ctx: &RenderRequestContext,
    pic: &mut RgbaImage,
    rank: &RgbaImage,
    ismain: bool,
    mission_type: &Value,
    missions: &[Value],
) -> Result<()> {
    paste_alpha(pic, rank, (if ismain { 218 } else { 40 }, 3));
    let progress = missions
        .iter()
        .find(|mission| &mission["honorMissionType"] == mission_type)
        .and_then(|mission| mission["progress"].as_i64())
        .ok_or_else(|| DrawError::Render("未找到玩家对应的progress".into()))?;
    let gfx = ctx.gfx()?;
    let text = progress.to_string();
    let font = "SourceHanSansCN-Bold.otf";
    let (width, height) = gfx.fonts.measure_text(font, 20.0, &text);
    let center_x = if ismain { 270.0 } else { 90.0 };
    gfx.fonts.draw_text(
        pic,
        (
            (center_x - width / 2.0).trunc(),
            (58.0 - height / 2.0).trunc(),
        ),
        &text,
        font,
        20.0,
        Rgba([255; 4]),
        TextAnchor::La,
        None,
    );
    if ismain {
        let static_dir = &ctx.app.paths.static_dir;
        let filled = open_image(
            &gfx.cache,
            static_dir.join("pics/live_master_honor_star_1.png"),
            None,
        )?;
        let empty = open_image(
            &gfx.cache,
            static_dir.join("pics/live_master_honor_star_2.png"),
            None,
        )?;
        let count = progress.div_euclid(10).rem_euclid(10) as usize + 1;
        for (i, (x, y)) in [
            (223, 68),
            (216, 56),
            (208, 42),
            (216, 27),
            (223, 13),
            (295, 68),
            (304, 56),
            (311, 42),
            (303, 27),
            (295, 13),
        ]
        .into_iter()
        .enumerate()
        {
            paste_alpha(pic, if i < count { &filled } else { &empty }, (x, y - 8));
        }
    }
    Ok(())
}

pub fn bondsbackground(
    ctx: &RenderRequestContext,
    chara1: i64,
    chara2: i64,
    ismain: bool,
) -> Result<RgbaImage> {
    let gfx = ctx.gfx()?;
    let dir = ctx.app.paths.static_dir.join("bonds");
    let suffix = if ismain { "" } else { "_sub" };
    let x = if ismain { 190 } else { 90 };
    let mut left =
        (*open_image(&gfx.cache, dir.join(format!("{chara1}{suffix}.png")), None)?).clone();
    let right = open_image(&gfx.cache, dir.join(format!("{chara2}{suffix}.png")), None)?;
    for y in 0..80.min(left.height()) {
        for x in x..380.min(left.width()) {
            left.put_pixel(
                x,
                y,
                right
                    .get_pixel_checked(x, y)
                    .copied()
                    .unwrap_or(Rgba([0; 4])),
            );
        }
    }
    Ok(left)
}

async fn render_bonds(
    ctx: &RenderRequestContext,
    honor: &Value,
    ismain: bool,
    pjsk_type: i32,
) -> Result<RgbaImage> {
    let gfx = ctx.gfx()?;
    let rows = ctx
        .app
        .master_data
        .load_master_data("bondsHonors.json", pjsk_type)?;
    let row = find_honor(&rows, honor)?;
    let first = row["gameCharacterUnitId1"]
        .as_i64()
        .ok_or_else(|| DrawError::Render("Missing first bonds character".into()))?;
    let second = row["gameCharacterUnitId2"]
        .as_i64()
        .ok_or_else(|| DrawError::Render("Missing second bonds character".into()))?;
    let (left, right) = if honor["bondsHonorViewType"] == "reverse" {
        (second, first)
    } else {
        (first, second)
    };
    let mut pic = bondsbackground(ctx, left, right, ismain)?;
    for (i, id) in [left, right].into_iter().enumerate() {
        let name = format!("chr_sd_{id:02}_01");
        let sprite = open_image(
            &gfx.cache,
            ctx.app
                .paths
                .ondemand_dir
                .join("chara")
                .join(&name)
                .join(format!("{name}.png")),
            None,
        )?;
        if ismain {
            paste_alpha(&mut pic, &sprite, (i as i32 * 220, -40));
        } else {
            let sprite = imageops::resize(&*sprite, 120, 102, imageops::FilterType::CatmullRom);
            paste_alpha(&mut pic, &sprite, (if i == 0 { -5 } else { 60 }, -20));
        }
    }
    if !ismain {
        let mask = open_image(
            &gfx.cache,
            ctx.app.paths.static_dir.join("pics/mask_degree_sub.png"),
            None,
        )?;
        if mask.dimensions() != pic.dimensions() {
            return Err(DrawError::Render(
                "Bonds mask and background dimensions differ".into(),
            ));
        }
        for (pixel, mask) in pic.pixels_mut().zip(mask.pixels()) {
            pixel[3] = mask[3];
        }
    }
    paste_frame(ctx, &mut pic, ismain, string(&row["honorRarity"]))?;
    if ismain {
        let word_id = honor["bondsHonorWordId"]
            .as_i64()
            .unwrap_or_default()
            .rem_euclid(100);
        let filename = format!("honorname_{first:02}{second:02}_{word_id:02}_01.png");
        if let Ok(Some(word)) = ctx
            .app
            .get_asset(
                "startapp/bonds_honor/word",
                &filename,
                pjsk_type,
                ctx.policy,
                true,
            )
            .await
        {
            paste_alpha(
                &mut pic,
                &word,
                (
                    (380 - word.width() as i32) / 2,
                    (80 - word.height() as i32) / 2,
                ),
            );
        }
    }
    paste_levels(
        ctx,
        &mut pic,
        honor["honorLevel"].as_i64().unwrap_or_default(),
    )?;
    Ok(pic)
}
