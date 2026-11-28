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
    let group_id = row["groupId"].as_i64().unwrap_or_default();
    let is_medal_displayed = group
        .and_then(|group| group["isMedalDisplayed"].as_bool())
        .unwrap_or(false);
    let rarity = string(&art["honorRarity"]);
    let bundle = string(&art["assetbundleName"]);
    let rarity_num = rarity_number(rarity);
    let (category, rank_name) = match (
        honor_type == "rank_match",
        string(&art["assetbundleName"]).starts_with("honor_top_000100") && live,
        ismain,
    ) {
        (true, _, true) => ("rank_live/honor", "main.png"),
        (true, _, false) => ("rank_live/honor", "sub.png"),
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
    let (layered_bg, layered_word) = if !live && honor_type == "character" && group_id > 0 {
        load_character_honor_layers(ctx, honor, group_id, degree, rarity_num, ismain, pjsk_type)
            .await
    } else {
        (None, None)
    };
    let Some(mut pic) = (match layered_bg {
        Some(image) => Some((*image).clone()),
        None => ctx
            .app
            .get_asset(
                &format!("startapp/{category}/{background_bundle}"),
                degree,
                pjsk_type,
                ctx.policy,
                true,
            )
            .await?
            .map(|image| (*image).clone()),
    }) else {
        return Ok(None);
    };
    if let Some(word) = layered_word {
        let pos = (
            (pic.width() as i32 - word.width() as i32) / 2,
            (pic.height() as i32 - word.height() as i32) / 2,
        );
        paste_alpha(&mut pic, &word, pos);
    }
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
    let medal_tier = honor_medal_tier(row, level, !live && is_medal_displayed);
    if medal_tier > 0
        && let Ok(Some(medal)) = ctx
            .app
            .get_asset_cached(
                "startapp/honor_medal/medal",
                &format!("icon_degree_medal{medal_tier}.png"),
                pjsk_type,
                ctx.policy,
                true,
            )
            .await
    {
        paste_alpha(&mut pic, &medal, (0, 0));
    }
    Ok(Some(pic))
}

fn rarity_number(rarity: &str) -> u8 {
    match rarity {
        "low" => 1,
        "middle" => 2,
        "high" => 3,
        _ => 4,
    }
}

fn honor_medal_tier(row: &Value, level: i64, is_medal_displayed: bool) -> i64 {
    let has_levels = row
        .get("levels")
        .and_then(Value::as_array)
        .is_some_and(|levels| !levels.is_empty());
    if !has_levels || !is_medal_displayed || level < 11 {
        return 0;
    }
    ((level - 1) / 10).min(9)
}

fn resolve_honor_layer<'a>(
    rows: Option<&'a Value>,
    group_id: i64,
    selected_id: Option<i64>,
) -> Option<&'a str> {
    let rows = rows?;
    let matched = normalize_master_list(rows)
        .into_iter()
        .filter(|row| {
            row["honorGroupId"].as_i64() == Some(group_id)
                && !string(&row["assetbundleName"]).is_empty()
        })
        .collect::<Vec<_>>();
    if let Some(selected_id) = selected_id.filter(|&id| id > 0)
        && let Some(selected) = matched
            .iter()
            .find(|row| row["id"].as_i64() == Some(selected_id))
    {
        return Some(string(&selected["assetbundleName"]));
    }
    matched
        .into_iter()
        .min_by_key(|row| {
            (
                row["seq"].as_i64().unwrap_or(i64::MAX),
                row["id"].as_i64().unwrap_or(i64::MAX),
            )
        })
        .map(|row| string(&row["assetbundleName"]))
}

async fn load_character_honor_layers(
    ctx: &RenderRequestContext,
    honor: &Value,
    group_id: i64,
    degree: &str,
    rarity_num: u8,
    ismain: bool,
    pjsk_type: i32,
) -> (
    Option<std::sync::Arc<crate::core::AssetImage>>,
    Option<std::sync::Arc<crate::core::AssetImage>>,
) {
    let store = &ctx.app.master_data;
    let bgs = store.load_master_data("honorBackgrounds.json", pjsk_type).ok();
    let Some(bg_bundle) =
        resolve_honor_layer(bgs.as_deref(), group_id, honor["honorBackgroundId"].as_i64())
    else {
        return (None, None);
    };
    let word_bundle = if ismain {
        let words = store.load_master_data("honorWords.json", pjsk_type).ok();
        resolve_honor_layer(words.as_deref(), group_id, honor["honorWordId"].as_i64())
            .map(str::to_owned)
    } else {
        None
    };
    let bg_path = format!("startapp/honor_background/{bg_bundle}");
    let bg_fut = ctx
        .app
        .get_asset(&bg_path, degree, pjsk_type, ctx.policy, true);
    let word_fut = async {
        match word_bundle.as_deref() {
            Some(word_bundle) => ctx
                .app
                .get_asset(
                    "startapp/honor_word",
                    &format!("{word_bundle}_{rarity_num:02}.png"),
                    pjsk_type,
                    ctx.policy,
                    true,
                )
                .await
                .ok()
                .flatten(),
            None => None,
        }
    };
    let (bg_res, word_res) = tokio::join!(bg_fut, word_fut);
    let Ok(Some(bg)) = bg_res else {
        return (None, None);
    };
    // Avoid rendering an empty textless background if main-slot word art is unavailable.
    if ismain && word_bundle.is_some() && word_res.is_none() {
        return (None, None);
    }
    (Some(bg), word_res)
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
    let number = rarity_number(rarity);
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::renderers::common::RenderFixture;
    use serde_json::json;

    #[test]
    fn computes_medal_tier_and_resolves_layers() {
        let leveled = json!({"id": 4, "levels": [{"level": 11}]});
        let flat = json!({"id": 99, "levels": []});
        assert_eq!(honor_medal_tier(&leveled, 10, true), 0);
        assert_eq!(honor_medal_tier(&leveled, 11, false), 0);
        assert_eq!(honor_medal_tier(&flat, 11, true), 0);
        assert_eq!(honor_medal_tier(&leveled, 11, true), 1);
        assert_eq!(honor_medal_tier(&leveled, 20, true), 1);
        assert_eq!(honor_medal_tier(&leveled, 21, true), 2);
        assert_eq!(honor_medal_tier(&leveled, 120, true), 9);

        let rows = json!([
            {"id": 10102, "seq": 2, "honorGroupId": 1, "assetbundleName": "honor_bg_style_02_01"},
            {"id": 10101, "seq": 1, "honorGroupId": 1, "assetbundleName": "honor_bg_style_01_01"},
            {"id": 10201, "seq": 1, "honorGroupId": 2, "assetbundleName": "honor_bg_style_01_02"}
        ]);
        assert_eq!(
            resolve_honor_layer(Some(&rows), 1, None),
            Some("honor_bg_style_01_01")
        );
        assert_eq!(
            resolve_honor_layer(Some(&rows), 1, Some(10102)),
            Some("honor_bg_style_02_01")
        );
        // Cross-group selection falls back to lowest seq in honor's own group.
        assert_eq!(
            resolve_honor_layer(Some(&rows), 1, Some(10201)),
            Some("honor_bg_style_01_01")
        );
    }

    fn save_rgba(fixture: &RenderFixture, path: &str, size: (u32, u32), pixel: [u8; 4]) {
        let mut bytes = std::io::Cursor::new(Vec::new());
        RgbaImage::from_pixel(size.0, size.1, Rgba(pixel))
            .write_to(&mut bytes, image::ImageFormat::Png)
            .unwrap();
        fixture.write(path, bytes.into_inner());
    }

    #[tokio::test]
    async fn renders_character_honor_layers_and_medal_offline() {
        let fixture = RenderFixture::new();
        fixture.write(
            "ondemand/jp/honors.json",
            r#"[{"id":4,"groupId":1,"honorRarity":"highest","assetbundleName":"honor_0004","levels":[{"level":11}]}]"#,
        );
        fixture.write(
            "ondemand/jp/honorGroups.json",
            r#"[{"id":1,"name":"一歌ファン","honorType":"character","isMedalDisplayed":true}]"#,
        );
        fixture.write(
            "ondemand/jp/honorBackgrounds.json",
            r#"[{"id":10101,"seq":1,"honorGroupId":1,"assetbundleName":"honor_bg_style_01_01"},{"id":10102,"seq":2,"honorGroupId":1,"assetbundleName":"honor_bg_style_02_01"}]"#,
        );
        fixture.write(
            "ondemand/jp/honorWords.json",
            r#"[{"id":10101,"seq":1,"honorGroupId":1,"assetbundleName":"honor_word_01_01"},{"id":10102,"seq":2,"honorGroupId":1,"assetbundleName":"honor_word_01_02"}]"#,
        );
        save_rgba(
            &fixture,
            "ondemand/jp/startapp/honor/honor_0004/degree_main.png",
            (380, 80),
            [10, 10, 10, 255],
        );
        save_rgba(
            &fixture,
            "ondemand/jp/startapp/honor_background/honor_bg_style_02_01/degree_main.png",
            (380, 80),
            [30, 120, 200, 255],
        );
        save_rgba(
            &fixture,
            "ondemand/jp/startapp/honor_background/honor_bg_style_02_01/degree_sub.png",
            (180, 80),
            [40, 130, 210, 255],
        );
        save_rgba(
            &fixture,
            "ondemand/jp/startapp/honor_word/honor_word_01_02_04.png",
            (100, 40),
            [250, 210, 60, 255],
        );
        save_rgba(
            &fixture,
            "ondemand/jp/startapp/honor_medal/medal/icon_degree_medal1.png",
            (20, 20),
            [255, 180, 0, 255],
        );
        save_rgba(&fixture, "static/pics/frame_degree_m_4.png", (380, 80), [0, 0, 0, 0]);
        save_rgba(&fixture, "static/pics/frame_degree_s_4.png", (180, 80), [0, 0, 0, 0]);
        save_rgba(&fixture, "static/pics/icon_degreeLv.png", (16, 16), [220, 40, 80, 255]);
        save_rgba(&fixture, "static/pics/icon_degreeLv6.png", (16, 16), [220, 40, 80, 255]);

        let ctx = fixture.context();
        let main = generatehonor(
            &ctx,
            &json!({
                "honorId": 4,
                "honorLevel": 11,
                "honorBackgroundId": 10102,
                "honorWordId": 10102
            }),
            true,
            &[],
            0,
        )
        .await
        .unwrap()
        .unwrap();
        assert_eq!(main.dimensions(), (380, 80));
        assert_eq!(main.get_pixel(5, 5).0, [255, 180, 0, 255]); // medal at top-left
        assert_eq!(main.get_pixel(190, 40).0, [250, 210, 60, 255]); // centered word
        assert_eq!(main.get_pixel(60, 70).0, [220, 40, 80, 255]); // level 11 -> 1 star
        assert_eq!(main.get_pixel(300, 20).0, [30, 120, 200, 255]); // layered bg

        // Sub honor still displays bg + medal + star, omitting the word overlay.
        let sub = generatehonor(
            &ctx,
            &json!({
                "honorId": 4,
                "honorLevel": 11,
                "honorBackgroundId": 10102,
                "honorWordId": 10102
            }),
            false,
            &[],
            0,
        )
        .await
        .unwrap()
        .unwrap();
        assert_eq!(sub.dimensions(), (180, 80));
        assert_eq!(sub.get_pixel(5, 5).0, [255, 180, 0, 255]);
        assert_eq!(sub.get_pixel(90, 40).0, [40, 130, 210, 255]);

        // Uncustomized selection falls back cleanly to classic art when seq=1 layer assets are absent.
        let fallback = generatehonor(
            &ctx,
            &json!({"honorId": 4, "honorLevel": 11}),
            true,
            &[],
            0,
        )
        .await
        .unwrap()
        .unwrap();
        assert_eq!(fallback.get_pixel(5, 5).0, [255, 180, 0, 255]);
        assert_eq!(fallback.get_pixel(190, 40).0, [10, 10, 10, 255]);
    }
}
