use std::{collections::HashMap, path::Path};

use crate::components::{
    card::cardthumnail,
    honor::generatehonor,
    normalize_master_list,
    profile_bg::{get_user_bg_path, get_user_bg_settings},
};
use crate::core::{RenderOutput, RenderRequestContext, Renderer, Result};
use crate::gfx::{GfxContext, RenderContextGfxExt, TextAnchor::*, paste_rgba, resize_lanczos};
use async_trait::async_trait;
use image::{RgbaImage, imageops};
use serde_json::Value;

use crate::renderers::common::*;

pub(crate) struct Profile;

const FRAME: [u8; 3] = [86, 86, 116];
const BORDER: [u8; 3] = [137, 137, 169];
const TEAL: [u8; 3] = [0, 204, 187];
const BLACK: [u8; 3] = [0; 3];
const WHITE: [u8; 3] = [255; 3];

const UNIT_BACKGROUNDS: [&[&str]; 6] = [
    &["bg_area_5.png", "bg_area_17.png", "bg_light_sound.png"],
    &["bg_area_7.png", "bg_area_18.png", "bg_idol.png"],
    &["bg_area_8.png", "bg_area_19.png", "bg_street.png"],
    &["bg_area_9.png", "bg_area_20.png", "bg_theme_park.png"],
    &["bg_area_10.png", "bg_area_21.png", "bg_school_refusal.png"],
    &["bg_virtual_singer.png"],
];
const COMMON_BACKGROUNDS: [&str; 10] = [
    "bg_area_1.png",
    "bg_area_2.png",
    "bg_area_3.png",
    "bg_area_4.png",
    "bg_area_11.png",
    "bg_area_12.png",
    "bg_area_13.png",
    "bg_area_25.png",
    "bg_area_26.png",
    "bg_area_27.png",
];
const CHARACTERS: [&[(i64, &str)]; 7] = [
    &[(21, "miku"), (22, "rin"), (23, "len"), (24, "luka")],
    &[(25, "meiko"), (26, "kaito")],
    &[(1, "ick"), (2, "saki"), (3, "hnm"), (4, "shiho")],
    &[(5, "mnr"), (6, "hrk"), (7, "airi"), (8, "szk")],
    &[(9, "khn"), (10, "an"), (11, "akt"), (12, "toya")],
    &[(13, "tks"), (14, "emu"), (15, "nene"), (16, "rui")],
    &[(17, "knd"), (18, "mfy"), (19, "ena"), (20, "mzk")],
];

#[async_trait]
impl Renderer for Profile {
    fn name(&self) -> &'static str {
        "profile"
    }

    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput> {
        let gfx = ctx.gfx()?;
        let paths = ctx.app.paths.clone();
        let server = number(&payload["pjsk_type"]) as i32;
        let server_name = ctx.app.server_name(server).to_owned();
        let userid = match &payload["userid"] {
            Value::String(id) => id.clone(),
            Value::Null => String::new(),
            id => id.to_string(),
        };
        let profile = &payload["profile"];
        let settings = get_user_bg_settings(&paths, &userid, &server_name);
        let vertical = settings["vertical"] == true;
        let size = if vertical { (800, 1650) } else { (1600, 1100) };
        let blur = settings["blur"]
            .as_f64()
            .unwrap_or(1.)
            .clamp(0., 50.) as f32
            * 2.;
        let alpha = settings["alpha"]
            .as_i64()
            .unwrap_or(180)
            .clamp(0, 255) as u8;
        let ids = rows(&profile["userDecks"]);
        let training = rows(&profile["special_training"]);
        let cards = ctx
            .app
            .master_data
            .load_master_data_async("cards.json", server)
            .await?;
        let leader = ids
            .first()
            .and_then(|id| {
                normalize_master_list(&cards)
                    .into_iter()
                    .find(|card| number(&card["id"]) == number(id))
            })
            .map_or(21, |card| number(&card["characterId"]));
        let user_file = !userid.is_empty() && !userid.contains(['/', '\\']);
        let custom = get_user_bg_path(&paths, &userid, &server_name);
        let suite_timestamp = if user_file && profile["isNewData"] != true {
            paths
                .ondemand_dir
                .join("suite")
                .join(&server_name)
                .join(format!("{userid}.json"))
                .metadata()
                .and_then(|meta| meta.modified())
                .ok()
                .map(chrono::DateTime::<chrono::Utc>::from)
                .map(|time| {
                    time.with_timezone(&chrono_tz::Asia::Shanghai)
                        .format("%Y-%m-%d %H:%M:%S")
                        .to_string()
                })
        } else {
            None
        };

        let deck: Vec<_> = ids
            .iter()
            .take(5)
            .enumerate()
            .map(|(i, id)| {
                let id = number(id);
                let trained = training
                    .get(i)
                    .is_some_and(|flag| *flag == true || number(flag) != 0);
                let ctx = ctx.clone();
                let cards = cards.clone();
                async move {
                    cardthumnail(&ctx, id, trained, Some(&cards), false, false, server)
                        .await
                        .ok()
                        .flatten()
                }
            })
            .collect();
        let missions = std::sync::Arc::new(rows(&profile["userHonorMissions"]).to_vec());
        let honor_rows = rows(&profile["userProfileHonors"]);
        let honors: Vec<_> = (1..=3)
            .map(|seq| {
                let honor = honor_rows
                    .iter()
                    .rev()
                    .find(|honor| number(&honor["seq"]) == seq)
                    .cloned();
                let ctx = ctx.clone();
                let missions = missions.clone();
                async move {
                    generatehonor(&ctx, &honor?, seq == 1, &missions, server)
                        .await
                        .ok()
                        .flatten()
                }
            })
            .collect();
        let (deck, honors) = tokio::try_join!(load_ordered(deck, 5), load_ordered(honors, 3))?;
        render_jpeg(gfx, 90, true, move |gfx| {
            let mut image = background(
                &paths.static_dir,
                custom.as_deref(),
                leader,
                size,
                blur,
                alpha,
            );
            let profile = &payload["profile"];
            let display_id = if payload["is_private"] == true {
                "保密"
            } else {
                &userid
            };
            draw_identity(
                gfx,
                &mut image,
                &paths.static_dir,
                profile,
                display_id,
                vertical,
                &deck,
                &honors,
            );
            draw_stats(gfx, &mut image, &paths.static_dir, profile, vertical);
            if let Some(time) = suite_timestamp {
                label(
                    gfx,
                    &mut image,
                    (68, 10),
                    &format!("数据上传时间：{time}"),
                    (BOLD, 22., [100; 3], La),
                );
            }
            image
        })
        .await
    }
}

fn background(
    static_dir: &Path,
    custom: Option<&Path>,
    leader: i64,
    (w, h): (u32, u32),
    blur: f32,
    alpha: u8,
) -> RgbaImage {
    let unit = if (1..=20).contains(&leader) {
        (leader as usize - 1) / 4
    } else {
        5
    };
    let dir = static_dir.join("pics/bg");
    let candidates: Vec<_> = UNIT_BACKGROUNDS[unit]
        .iter()
        .chain(&COMMON_BACKGROUNDS)
        .map(|name| dir.join(name))
        .filter(|path| path.is_file())
        .collect();
    let source = custom.and_then(|path| image::open(path).ok()).or_else(|| {
        if candidates.is_empty() {
            return None;
        }
        let start = chrono::Utc::now().timestamp_subsec_nanos() as usize % candidates.len();
        candidates
            .iter()
            .cycle()
            .skip(start)
            .take(candidates.len())
            .find_map(|path| image::open(path).ok())
    });
    let mut image = source.map_or_else(
        || RgbaImage::from_pixel(w, h, rgba([200, 210, 220], 255)),
        |img| {
            image::DynamicImage::ImageRgb8(img.to_rgb8())
                .resize_to_fill(w, h, imageops::FilterType::Lanczos3)
                .into_rgba8()
        },
    );
    if blur > 0. {
        image = imageops::blur(&image, blur);
    }
    for pixel in image.pixels_mut() {
        for channel in 0..3 {
            pixel[channel] = ((u16::from(pixel[channel]) * u16::from(255 - alpha)
                + 255 * u16::from(alpha)
                + 127)
                / 255) as u8;
        }
    }
    image
}

fn paste_static(
    gfx: &GfxContext,
    image: &mut RgbaImage,
    dir: &Path,
    file: &str,
    (x, y): (i32, i32),
    size: (u32, u32),
) {
    if let Ok(icon) = gfx.cache.open_pjsk_image(dir.join(file), Some(size)) {
        paste_rgba(image, &icon, x, y);
    }
}

#[allow(clippy::too_many_arguments)]
fn draw_identity(
    gfx: &GfxContext,
    image: &mut RgbaImage,
    dir: &Path,
    profile: &Value,
    userid: &str,
    vertical: bool,
    deck: &[Option<RgbaImage>],
    honors: &[Option<RgbaImage>],
) {
    let (ax, ay, side, inset) = if vertical {
        (85, 35, 130, 5)
    } else {
        (117, 50, 151, 6)
    };
    rounded_panel_overlay(
        gfx,
        image,
        (ax, ay, ax + side, ay + side),
        8,
        rgba(FRAME, 0),
        Some(rgba(FRAME, 255)),
    );
    let thumb_size = side - 2 * inset;
    rounded_panel_overlay(
        gfx,
        image,
        (ax + inset, ay + inset, ax + side - inset, ay + side - inset),
        6,
        rgba(FRAME, 255),
        None,
    );
    if let Some(Some(thumb)) = deck.first() {
        let side = thumb_size as u32;
        paste_rgba(
            image,
            &resize_lanczos(thumb, side, side),
            ax + inset,
            ay + inset,
        );
    }
    let (name_x, name_y, id_y, name_size) = if vertical {
        (240, 43, 93, 40.)
    } else {
        (295, 45, 116, 45.)
    };
    let name = profile["name"]
        .as_str()
        .filter(|name| !name.is_empty())
        .unwrap_or("???");
    let name = fit_text(
        &gfx.fonts,
        name,
        BOLD,
        name_size,
        if vertical { 520. } else { 475. },
    );
    label(
        gfx,
        image,
        (name_x, name_y),
        &name,
        (BOLD, name_size, BLACK, La),
    );
    label(
        gfx,
        image,
        (name_x, id_y),
        &format!("ID: {userid}"),
        (RODIN, if vertical { 18. } else { 20. }, BLACK, La),
    );

    let (x, y, w, h) = if vertical {
        (240, 123, 180, 42)
    } else {
        (307, 150, 208, 50)
    };
    rounded_panel_overlay(
        gfx,
        image,
        (x, y, x + w, y + h),
        (h / 2) as u32,
        rgba(FRAME, 0),
        Some(rgba(FRAME, 255)),
    );
    rounded_panel_overlay(
        gfx,
        image,
        (x + 3, y + 3, x + w - 3, y + h - 3),
        (h / 2 - 3) as u32,
        rgba(FRAME, 255),
        None,
    );
    let icon = if vertical { 30 } else { 35 };
    paste_static(
        gfx,
        image,
        dir,
        "pics/icon_playerRank.png",
        (x + 8, y + (h - icon) / 2),
        (icon as u32, icon as u32),
    );
    label(
        gfx,
        image,
        (x + 48, y + h / 2),
        "Lv.",
        (RODIN, if vertical { 18. } else { 20. }, WHITE, Lm),
    );
    label(
        gfx,
        image,
        (x + 78, y + h / 2),
        &number(&profile["rank"]).to_string(),
        (RODIN, if vertical { 26. } else { 28. }, WHITE, Lm),
    );

    let slots = if vertical {
        [(206, 192, 206, 42), (422, 192, 86, 42), (518, 192, 86, 42)]
    } else {
        [(119, 231, 234, 49), (390, 231, 96, 49), (523, 233, 96, 49)]
    };
    for ((x, y, w, h), honor) in slots.into_iter().zip(honors) {
        rounded_panel_overlay(
            gfx,
            image,
            (x, y, x + w, y + h),
            (h / 2) as u32,
            rgba([176, 189, 200], 255),
            Some(rgba([144, 163, 176], 255)),
        );
        if let Some(honor) = honor {
            let (delta, w, h) = if vertical {
                (3, w - 6, h - 2)
            } else {
                (-5, w + 10, h + 2)
            };
            paste_rgba(
                image,
                &resize_lanczos(honor, w as u32, h as u32),
                x + delta,
                y - 1 + if vertical { 2 } else { 0 },
            );
        }
    }
    let (x, y, w, h, sig_y, sig_h) = if vertical {
        (100, 255, 600, 42, 310, 70)
    } else {
        (114, 309, 481, 49, 379, 90)
    };
    for (y, h) in [(y, h), (sig_y, sig_h)] {
        rounded_panel_overlay(
            gfx,
            image,
            (x, y, x + w, y + h),
            8,
            rgba(BORDER, 0),
            Some(rgba(BORDER, 255)),
        );
    }
    let icon = if vertical { 26 } else { 30 };
    paste_static(
        gfx,
        image,
        dir,
        "pics/icon_X.png",
        (x + 9, y + 8),
        (icon, icon),
    );
    let twitter = string(&profile["twitterId"]);
    if !twitter.is_empty() {
        label(
            gfx,
            image,
            (x + if vertical { 40 } else { 48 }, y + h / 2),
            &format!("@{twitter}"),
            (RODIN, if vertical { 18. } else { 20. }, BLACK, Lm),
        );
    }
    let chars: Vec<_> = string(&profile["word"])
        .chars()
        .take(if vertical { 56 } else { 66 })
        .collect();
    for (i, line) in chars.chunks(if vertical { 28 } else { 22 }).enumerate() {
        label(
            gfx,
            image,
            (
                x + if vertical { 12 } else { 18 },
                sig_y
                    + if vertical {
                        10 + i as i32 * 26
                    } else {
                        13 + i as i32 * 28
                    },
            ),
            &line.iter().collect::<String>(),
            (MEDIUM, if vertical { 20. } else { 22. }, BLACK, La),
        );
    }

    let (x, y, step, rank_side) = if vertical {
        (76, 395, 130, 24)
    } else {
        (114, 489, 138, 28)
    };
    let ranks = rows(&profile["deck_master_ranks"]);
    for (i, thumb) in deck.iter().enumerate() {
        if let Some(thumb) = thumb {
            let x = x + i as i32 * step;
            paste_rgba(image, &resize_lanczos(thumb, 128, 128), x, y);
            let rank = ranks.get(i).map_or(0, number);
            if rank > 0 {
                paste_static(
                    gfx,
                    image,
                    dir,
                    &format!("chara/train_rank_{rank}.png"),
                    (x + 128 - rank_side, y + 128 - rank_side),
                    (rank_side as u32, rank_side as u32),
                );
            }
        }
    }
}

fn pill(
    gfx: &GfxContext,
    image: &mut RgbaImage,
    (x, y, w, h): (i32, i32, i32, i32),
    text: &str,
    size: f32,
) {
    rounded_panel_overlay(
        gfx,
        image,
        (x, y, x + w, y + h),
        (h / 2) as u32,
        rgba(TEAL, 255),
        None,
    );
    label(
        gfx,
        image,
        (x + w / 2, y + h / 2),
        text,
        (RODIN, size, WHITE, Mm),
    );
}

fn draw_stats(
    gfx: &GfxContext,
    image: &mut RgbaImage,
    dir: &Path,
    profile: &Value,
    vertical: bool,
) {
    for (i, (title, key)) in [
        ("CLEAR", "clear"),
        ("FULL COMBO", "full_combo"),
        ("ALL PERFECT", "full_perfect"),
    ]
    .into_iter()
    .enumerate()
    {
        let (x, y, rect_y, label_h, rect_h) = if vertical {
            (75, 545 + i as i32 * 95, 583 + i as i32 * 95, 30, 34)
        } else {
            (101, [631, 764, 897][i], [686, 819, 953][i], 36, 36)
        };
        pill(
            gfx,
            image,
            (x, y, 650, label_h),
            title,
            if vertical { 16. } else { 18. },
        );
        for (j, (name, mut color)) in DIFFICULTIES.into_iter().enumerate() {
            color = match name {
                "hard" => [230, 170, 0],
                "expert" => [238, 68, 102],
                "append" => [255, 105, 180],
                _ => color,
            };
            let rx = x + j as i32 * 111;
            rounded_panel_overlay(
                gfx,
                image,
                (rx, rect_y, rx + 95, rect_y + rect_h),
                6,
                rgba(color, 255),
                None,
            );
            label(
                gfx,
                image,
                (rx + 47, rect_y + 8),
                &name.to_uppercase(),
                (RODIN, if vertical { 12. } else { 14. }, WHITE, Mm),
            );
            let counts = &profile[key];
            let count = counts[j]
                .as_i64()
                .or_else(|| counts[name].as_i64())
                .unwrap_or_default();
            label(
                gfx,
                image,
                (rx + 47, rect_y + 25),
                &count.to_string(),
                (RODIN, if vertical { 18. } else { 20. }, WHITE, Mm),
            );
        }
    }

    let (sections, mvp, superstar, solo, rank_y) = if vertical {
        (
            [(75, 831, 315, 30), (410, 831, 315, 30)],
            (117, 873, 100, 38),
            (117, 921, 100, 38),
            (452, 873, 90, 38),
            979,
        )
    } else {
        (
            [(798, 48, 650, 36), (798, 223, 650, 36)],
            (836, 135, 108, 46),
            (1143, 135, 108, 46),
            (836, 308, 108, 46),
            402,
        )
    };
    for (bounds, title) in sections.into_iter().zip(["MULTI LIVE", "CHALLENGE LIVE"]) {
        pill(gfx, image, bounds, title, if vertical { 14. } else { 16. });
    }
    let font_size = if vertical { 22. } else { 24. };
    for (bounds, title, key) in [(mvp, "MVP", "mvpCount"), (superstar, "", "superStarCount")] {
        let (x, y, w, h) = bounds;
        pill(gfx, image, bounds, title, font_size);
        if title.is_empty() {
            for (offset, text) in [(h / 4, "SUPER"), (h * 3 / 4, "STAR")] {
                label(
                    gfx,
                    image,
                    (x + w / 2, y + offset),
                    text,
                    (RODIN, if vertical { 12. } else { 14. }, WHITE, Mm),
                );
            }
        }
        label(
            gfx,
            image,
            (x + w + if vertical { 12 } else { 8 }, y + h / 2),
            &format!("{}回", number(&profile[key])),
            (RODIN, font_size, BLACK, Lm),
        );
    }
    let (x, y, w, h) = solo;
    pill(gfx, image, solo, "SOLO", font_size);
    let side = if vertical { 55 } else { 70 };
    paste_static(
        gfx,
        image,
        dir,
        &format!("chara/chr_ts_{}.png", number(&profile["characterId"])),
        (
            x + w + if vertical { 12 } else { 8 },
            y + if vertical { -8 } else { -15 },
        ),
        (side, side),
    );
    let score = profile["highScore"]
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| {
            if profile["highScore"].is_null() {
                "0".into()
            } else {
                profile["highScore"].to_string()
            }
        });
    label(
        gfx,
        image,
        (x + w + if vertical { 74 } else { 88 }, y + h / 2),
        &score,
        (RODIN, font_size, BLACK, Lm),
    );

    pill(
        gfx,
        image,
        (
            if vertical { 75 } else { 799 },
            rank_y,
            650,
            if vertical { 30 } else { 36 },
        ),
        "CHARACTER RANK",
        if vertical { 14. } else { 16. },
    );
    let ranks: HashMap<_, _> = rows(&profile["characterRank"])
        .iter()
        .filter(|row| row.is_object())
        .map(|row| (number(&row["characterId"]), number(&row["characterRank"])))
        .collect();
    for (row, characters) in CHARACTERS.into_iter().enumerate() {
        let start_x = if vertical {
            (800 - (characters.len() as i32 * 165 - 10)) / 2
        } else {
            799
        };
        let y = if vertical { rank_y + 42 } else { 456 } + row as i32 * 83;
        for (col, (id, name)) in characters.iter().enumerate() {
            let x = start_x + col as i32 * 165;
            paste_static(
                gfx,
                image,
                dir,
                &format!("chara/chara_rank_icon/{name}.png"),
                (x, y),
                (155, 75),
            );
            label(
                gfx,
                image,
                (x + 116, y + 48),
                &ranks.get(id).copied().unwrap_or_default().to_string(),
                (RODIN, if vertical { 16. } else { 18. }, BLACK, Mm),
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[tokio::test]
    async fn renders_both_background_layouts_offline() {
        let fixture = RenderFixture::new();
        fixture.write("ondemand/jp/cards.json",r#"[{"id":1,"characterId":21,"attr":"cool","cardRarityType":"rarity_1","assetbundleName":"test"}]"#);
        fixture.write(
            "ondemand/jp/honors.json",
            r#"[{"id":1,"assetbundleName":"test","honorRarity":"low"}]"#,
        );
        fixture.write("ondemand/jp/honorGroups.json", "[]");
        for path in [
            "ondemand/jp/startapp/thumbnail/chara/test_normal.png",
            "ondemand/jp/startapp/honor/test/degree_main.png",
            "static/chara/cardFrame_rarity_1.png",
            "static/chara/rarity_star_normal.png",
            "static/chara/icon_attribute_cool.png",
            "static/chara/train_rank_3.png",
            "static/chara/chara_rank_icon/miku.png",
            "static/pics/frame_degree_m_1.png",
        ] {
            fixture.png(path, (32, 32));
        }
        fixture.png("static/pics/bg/bg_virtual_singer.png", (40, 20));
        let custom = fixture.write("static/profile_bg/jp/7.jpg", []);
        image::RgbImage::from_pixel(32, 32, image::Rgb([20, 75, 125]))
            .save(&custom)
            .unwrap();
        fixture.write(
            "static/profile_bg/settings.json",
            r#"{"jp:7":{"vertical":true,"blur":1,"alpha":50}}"#,
        );
        let ctx = fixture.context();
        for (userid, size) in [("7", (800, 1650)), ("8", (1600, 1100))] {
            let payload = json!({"userid":userid,"profile":{"word":"Hello","twitterId":"test","userDecks":[1,1,1,1,1],"deck_master_ranks":[3],"userProfileHonors":[{"seq":1,"honorId":1}],"clear":[1,2,3,4,5,6],"characterRank":[{"characterId":21,"characterRank":50}]}});
            let output = Profile.render(&ctx, payload).await.unwrap();
            let image =
                image::load_from_memory_with_format(&output.images[0], image::ImageFormat::Jpeg)
                    .unwrap()
                    .into_rgb8();
            assert_eq!(image.dimensions(), size);
            let points = if userid == "7" {
                [(140, 100), (140, 450), (280, 211), (110, 1050)]
            } else {
                [(170, 120), (170, 550), (240, 255), (830, 480)]
            };
            for (x, y) in points {
                assert!(image.get_pixel(x, y)[1] < 95, "missing asset at ({x}, {y})");
            }
            let expected = if userid == "7" {
                [66, 110, 150]
            } else {
                [218, 199, 236]
            };
            for (actual, expected) in image.get_pixel(20, 70).0.into_iter().zip(expected) {
                assert!(actual.abs_diff(expected) < 6, "{actual} != {expected}");
            }
        }
    }
}
