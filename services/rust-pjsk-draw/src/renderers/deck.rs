use std::{collections::HashSet, sync::LazyLock};

use crate::components::{
    card::{CARD_RENDER_LIMIT, CardTileOptions, cardthumnail, paste_card_thumbnail_tile},
    normalize_master_list,
    profile_header::{PjskHeaderData, draw_pjsk_profile_header},
    ui::{
        draw_round_panel, get_pjsk_asset_resized, get_pjsk_music_jacket_cached, paste_round_image,
    },
};
use crate::core::{RenderOutput, RenderRequestContext, Renderer, Result};
use crate::gfx::{GfxContext, RenderContextGfxExt, TextAnchor::*, paste_rgba};
use async_trait::async_trait;
use image::RgbaImage;
use serde_json::Value;
use tokio::sync::Semaphore;

use crate::renderers::common::*;

pub(crate) struct Deck;

const WIDTH: i32 = 1100;
const PAD: i32 = 30;
const TABLE_Y: i32 = 396;
const HEADER_H: i32 = 40;
const ROW_H: i32 = 160;
const INFO_X: i32 = PAD + 475;
const INK: [u8; 3] = [88, 92, 118];
const WARN: [u8; 3] = [255, 50, 50];
const BONUS: [u8; 3] = [200, 120, 50];
const SKILL: [u8; 3] = [160, 80, 160];
const COLUMNS: [i32; 5] = [0, 110, 220, 330, 430];
static ROW_LIMIT: LazyLock<Semaphore> = LazyLock::new(|| Semaphore::new(*CARD_RENDER_LIMIT));

#[derive(Clone, Copy)]
struct Mode<'a>(&'a str);

impl Mode<'_> {
    fn bonus(self) -> bool {
        matches!(self.0, "bonus" | "wl_bonus")
    }
    fn challenge(self) -> bool {
        matches!(self.0, "challenge" | "challenge_all")
    }
    fn score(self) -> bool {
        self.challenge() || self.0 == "no_event"
    }

    fn labels(self) -> [&'static str; 5] {
        if self.bonus() {
            ["加成", "", "", "", ""]
        } else if self.challenge() {
            ["分数", "综合力", "", "实效", ""]
        } else if self.score() {
            ["分数", "综合力", "", "实效", "歌曲分"]
        } else {
            ["PT", "综合力", "加成", "实效", "歌曲分"]
        }
    }

    fn title(self) -> &'static str {
        match self.0 {
            "event" => "活动组卡",
            "wl" => "WL活动组卡",
            "wl_fake" => "WL模拟组卡",
            "unit_attr" => "模拟活动组卡",
            "custom_event" => "自定义混活组卡",
            "no_event" => "长草组卡",
            "challenge" => "挑战组卡",
            "challenge_all" => "挑战组卡(全角色)",
            "bonus" => "加成组卡",
            "wl_bonus" => "WL加成组卡",
            _ => "组卡",
        }
    }
}

#[async_trait]
impl Renderer for Deck {
    fn name(&self) -> &'static str {
        "deck"
    }

    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput> {
        let gfx = ctx.gfx()?;
        let paths = &ctx.app.paths;
        let server = number(&payload["pjsk_type"]) as i32;
        let store = &ctx.app.master_data;
        let cards = store.load_master_data("cards.json", server)?;
        let decks = rows(&payload["result_decks"]);
        let table_height = if decks.is_empty() {
            60
        } else {
            HEADER_H + decks.len() as i32 * ROW_H
        };
        let height = TABLE_Y + table_height + 120;
        let h = height as f32;
        let mut image = make_style_gradient_bg(
            (WIDTH as u32, height as u32),
            [255, 246, 250],
            [236, 244, 255],
            &[
                SoftGlow {
                    center: (WIDTH as f32 / 6., h / 15.),
                    radii: (WIDTH as f32 / 3., h * 4. / 15.),
                    color: rgba([255, 190, 220], 72),
                },
                SoftGlow {
                    center: (WIDTH as f32 * 0.875, h * 0.75),
                    radii: (WIDTH as f32 * 0.375, h * 5. / 12.),
                    color: rgba([170, 210, 255], 64),
                },
            ],
        );
        let profile = &payload["profile_data"];
        let header = PjskHeaderData {
            is_private: payload["is_private"] == true,
            user_decks: rows(&profile["userDecks"]).iter().map(number).collect(),
            user_profile_honors: rows(&profile["userProfileHonors"]).to_vec(),
            user_honor_missions: rows(&profile["userHonorMissions"]).to_vec(),
            ..PjskHeaderData::from_payload(profile)
        };
        draw_pjsk_profile_header(
            ctx,
            &mut image,
            (PAD, 12, WIDTH - PAD, 268),
            &header,
            "DECK RECOMMEND",
            server,
            Some(&card_assets(&cards)),
            None,
            true,
            false,
        )
        .await?;

        let options = &payload["options"];
        let additional = &payload["additional"];
        let music_id = options
            .get("music_id")
            .filter(|id| !id.is_null())
            .map_or(10000, number);
        let (cover, music_title) = if music_id == 10000 {
            (
                gfx.cache
                    .open_pjsk_image(paths.static_dir.join("pics/rt.png"), Some((52, 52)))
                    .ok()
                    .map(|img| (*img).clone()),
                "おまかせ".into(),
            )
        } else {
            let cover =
                get_pjsk_music_jacket_cached(ctx, music_id, server, Some((52, 52)), None, true)
                    .await
                    .ok()
                    .flatten();
            let music = store.master_data_by_id("musics.json", server, "id")?;
            let title = music
                .get(&music_id.to_string())
                .and_then(|row| row["title"].as_str())
                .map_or_else(|| format!("ID:{music_id}"), str::to_owned);
            (cover, title)
        };
        let event_id = number(&options["event_id"]);
        let (banner, event_name) = if matches!(string(&payload["recommend_type"]), "event" | "wl") {
            event_banner(ctx, server, event_id).await
        } else {
            (None, String::new())
        };
        let (unit, attr) = event_filters(ctx, server, event_id, options, additional);
        let wl_id = number(&options["world_bloom_character_id"]);
        let wl_icon = character_portrait(&gfx, &paths.static_dir, wl_id);
        let tasks: Vec<_> = decks
            .iter()
            .map(|deck| {
                let ctx = ctx.clone();
                let cards = cards.clone();
                let deck = deck.clone();
                async move {
                    let _permit = ROW_LIMIT.acquire().await.expect("deck semaphore is open");
                    let tasks: Vec<_> = rows(&deck["cards"])
                        .iter()
                        .take(5)
                        .map(|row| {
                            let (id, trained) = (
                                number(&row["card_id"]),
                                row["default_image"] == "special_training",
                            );
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
                    load_ordered(tasks, 5).await
                }
            })
            .collect();
        let thumbnails = load_ordered(tasks, *CARD_RENDER_LIMIT)
            .await?
            .into_iter()
            .collect::<Result<Vec<_>>>()?;

        let static_dir = paths.static_dir.clone();
        render_jpeg(gfx, 88, true, move |gfx| {
            let mode = Mode(string(&payload["recommend_type"]));
            let options = &payload["options"];
            let additional = &payload["additional"];
            let mut title = mode.title().to_owned();
            if event_id != 0 && matches!(mode.0, "event" | "wl" | "bonus" | "wl_bonus") {
                title.push_str(&format!(" #{event_id}"));
            }
            title.push_str(match options["live_type"].as_str().unwrap_or("multi") {
                "multi" => " (多人)",
                "solo" => " (单人)",
                "auto" => " (AUTO)",
                _ => "",
            });
            label(gfx, &mut image, (40, 290), &title, (BOLD, 26., INK, La));
            let mut icon_x = 54 + gfx.fonts.measure_text(BOLD, 26., &title).0 as i32;
            if wl_id != 0 {
                draw_round_panel(
                    &gfx.cache,
                    &mut image,
                    (icon_x, 284, icon_x + 178, 330),
                    18,
                    rgba([255, 246, 251], 230),
                    Some(rgba([245, 205, 225], 255)),
                    false,
                );
                if let Some(icon) = wl_icon {
                    paste_rgba(
                        &mut image,
                        &crate::gfx::resize_lanczos(&icon, 36, 36),
                        icon_x + 8,
                        289,
                    );
                }
                label(
                    gfx,
                    &mut image,
                    (icon_x + 52, 296),
                    "当前组卡角色",
                    (BOLD, 13., [150, 70, 110], Lm),
                );
                let chapter = number(&options["world_bloom_chapter_no"]);
                let chapter = if chapter > 0 {
                    format!("第{chapter}章")
                } else {
                    "WL单榜".into()
                };
                label(
                    gfx,
                    &mut image,
                    (icon_x + 52, 315),
                    &chapter,
                    (MEDIUM, 12., [90, 80, 100], Lm),
                );
                icon_x += 190;
            }
            let mut icon_right = WIDTH - PAD;
            if let Some(banner) = banner {
                let x = WIDTH - PAD - 360;
                icon_right = x - 12;
                draw_round_panel(
                    &gfx.cache,
                    &mut image,
                    (x, 284, x + 360, 356),
                    12,
                    rgba([255; 3], 225),
                    None,
                    true,
                );
                let banner = image::DynamicImage::ImageRgba8(banner)
                    .thumbnail(210, 60)
                    .into_rgba8();
                paste_rgba(
                    &mut image,
                    &banner,
                    x + 8,
                    284 + (72 - banner.height() as i32) / 2,
                );
                label(
                    gfx,
                    &mut image,
                    (x + 226, 296),
                    &format!("Event #{event_id}"),
                    (BOLD, 18., INK, La),
                );
                let name = fit_text(&gfx.fonts, &event_name, MEDIUM, 13., 122.);
                label(
                    gfx,
                    &mut image,
                    (x + 226, 324),
                    &name,
                    (MEDIUM, 13., [80; 3], La),
                );
            }
            for (key, name, size) in [
                (&unit, format!("pics/logo_{unit}.png"), (96, 42)),
                (&attr, format!("chara/icon_attribute_{attr}.png"), (42, 42)),
            ] {
                if key.is_empty() || !key.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_') {
                    continue;
                }
                if let Ok(icon) = gfx.cache.open_pjsk_image(static_dir.join(name), None) {
                    let icon = image::DynamicImage::ImageRgba8((*icon).clone())
                        .thumbnail(size.0, size.1)
                        .into_rgba8();
                    if icon_x + icon.width() as i32 <= icon_right {
                        paste_rgba(&mut image, &icon, icon_x, 288);
                        icon_x += icon.width() as i32 + 8;
                    }
                }
            }

            let diff = options["music_diff"].as_str().unwrap_or("master");
            let color = DIFFICULTIES
                .iter()
                .find(|(name, _)| *name == diff)
                .map_or([100; 3], |&(_, color)| color);
            let music = format!("{music_title} ({})", diff.to_uppercase());
            let mut x = 40;
            if let Some(cover) = cover {
                rounded_panel_overlay(
                    gfx,
                    &mut image,
                    (40, 330, 97, 387),
                    12,
                    rgba(color, 255),
                    None,
                );
                paste_round_image(&gfx.cache, &mut image, &cover, (43, 333), (52, 52), 9);
                x += 68;
            }
            let music = fit_text(&gfx.fonts, &music, MEDIUM, 16., 620.);
            label(gfx, &mut image, (x, 356), &music, (MEDIUM, 16., color, Lm));
            let music_right = x + gfx.fonts.measure_text(MEDIUM, 16., &music).0 as i32;
            let missing = string(&options["music_diff_missing"]);
            if !missing.is_empty() {
                label(
                    gfx,
                    &mut image,
                    (music_right + 12, 356),
                    &format!("⚠无{}难度", missing.to_uppercase()),
                    (MEDIUM, 13., WARN, Lm),
                );
            }
            let mut settings: Vec<_> = ["unit_filter", "attr_filter"]
                .into_iter()
                .map(|key| string(&additional[key]))
                .filter(|value| !value.is_empty())
                .map(|value| format!("仅{value}"))
                .collect();
            if let Some(boost) = additional.get("boost").filter(|boost| !boost.is_null()) {
                settings.push(format!(
                    "{boost}火(x{})",
                    options.get("boost_bonus").unwrap_or(boost)
                ));
            }
            label(
                gfx,
                &mut image,
                (WIDTH - PAD - 10, 356),
                &settings.join("  "),
                (MEDIUM, 14., [120, 110, 130], Rm),
            );

            let decks = rows(&payload["result_decks"]);
            if decks.is_empty() {
                label(
                    gfx,
                    &mut image,
                    (PAD + 10, TABLE_Y + 15),
                    "未找到符合条件的卡组",
                    (BOLD, 22., WARN, La),
                );
            } else {
                rounded_panel_overlay(
                    gfx,
                    &mut image,
                    (PAD, TABLE_Y, WIDTH - PAD, TABLE_Y + HEADER_H),
                    6,
                    rgba(INK, 255),
                    None,
                );
                label(
                    gfx,
                    &mut image,
                    (PAD + 15, TABLE_Y + 12),
                    "卡组",
                    (BOLD, 15., [255; 3], La),
                );
                for (offset, text) in COLUMNS.into_iter().zip(mode.labels()) {
                    label(
                        gfx,
                        &mut image,
                        (INFO_X + offset, TABLE_Y + 12),
                        text,
                        (BOLD, 15., [255; 3], La),
                    );
                }
                for (i, (deck, thumbs)) in decks.iter().zip(thumbnails).enumerate() {
                    draw_row(gfx, &static_dir, &mut image, i, deck, &thumbs, mode);
                }
            }
            let timing: Vec<_> = payload["cost_times"]
                .as_object()
                .into_iter()
                .flatten()
                .map(|(alg, cost)| {
                    let name = match alg.as_str() {
                        "allium" => "Allium（进程内）".into(),
                        "http" => "Allium（HTTP）".into(),
                        "dfs" => "暴力搜索".into(),
                        "sa" => "模拟退火".into(),
                        "ga" => "遗传算法".into(),
                        _ => alg.to_uppercase(),
                    };
                    let cost = decimal(cost);
                    if cost < 1. {
                        format!("{name}({:.0}ms)", cost * 1000.)
                    } else {
                        format!("{name}({cost:.2}s)")
                    }
                })
                .collect();
            let footer = TABLE_Y + table_height + 10;
            label(
                gfx,
                &mut image,
                (PAD + 5, footer),
                &format!("组卡计算: {}", timing.join("  ")),
                (MEDIUM, 13., [0, 204, 187], La),
            );
            label(
                gfx,
                &mut image,
                (PAD + 5, footer + 20),
                "结果仅供参考，请以实际游戏内数据为准",
                (MEDIUM, 13., [0, 204, 187], La),
            );
            image
        })
        .await
    }
}

async fn event_banner(
    ctx: &RenderRequestContext,
    server: i32,
    id: i64,
) -> (Option<RgbaImage>, String) {
    if id == 0 {
        return (None, String::new());
    }
    let Ok(events) = ctx
        .app
        .master_data
        .master_data_by_id("events.json", server, "id")
    else {
        return (None, String::new());
    };
    let Some(event) = events.get(&id.to_string()) else {
        return (None, String::new());
    };
    let asset = string(&event["assetbundleName"]);
    let name = string(&event["name"]).to_owned();
    if !asset.is_empty() {
        let story = format!("ondemand/event_story/{asset}/screen_image");
        let screen = format!("ondemand/event/{asset}/screen");
        for (category, file) in [
            (&story, "banner_event_story.png"),
            (&screen, "banner.png"),
            (&story, "story_title.png"),
        ] {
            if let Ok(Some(banner)) =
                get_pjsk_asset_resized(ctx, category, file, server, None).await
            {
                return (Some(banner), name);
            }
        }
    }
    (None, name)
}

fn event_filters(
    ctx: &RenderRequestContext,
    server: i32,
    id: i64,
    options: &Value,
    additional: &Value,
) -> (String, String) {
    let explicit = |event: &str, filter: &str| {
        [&options[event], &options[filter], &additional[filter]]
            .into_iter()
            .map(string)
            .map(str::trim)
            .find(|value| !value.is_empty())
            .unwrap_or_default()
            .to_owned()
    };
    let mut unit = explicit("event_unit", "unit_filter");
    let mut attr = explicit("event_attr", "attr_filter");
    let store = &ctx.app.master_data;
    if id != 0
        && (unit.is_empty() || attr.is_empty())
        && let Ok(bonuses) = store.load_master_data("eventDeckBonuses.json", server)
    {
        let bonuses: Vec<_> = normalize_master_list(&bonuses)
            .into_iter()
            .filter(|row| number(&row["eventId"]) == id)
            .collect();
        if attr.is_empty() {
            let attrs: HashSet<_> = bonuses
                .iter()
                .map(|row| string(&row["cardAttr"]))
                .filter(|value| !value.is_empty())
                .collect();
            if attrs.len() == 1 {
                attr = attrs.into_iter().next().unwrap().to_owned();
            }
        }
        if unit.is_empty()
            && let Ok(units) = store.master_data_by_id("gameCharacterUnits.json", server, "id")
        {
            let matches: HashSet<_> = bonuses
                .iter()
                .filter_map(|row| units.get(&number(&row["gameCharacterUnitId"]).to_string()))
                .map(|row| string(&row["unit"]))
                .filter(|value| !value.is_empty())
                .collect();
            if matches.len() == 1 {
                unit = matches.into_iter().next().unwrap().to_owned();
            }
        }
    }
    (unit, attr)
}

fn draw_row(
    gfx: &GfxContext,
    static_dir: &std::path::Path,
    image: &mut RgbaImage,
    i: usize,
    deck: &Value,
    thumbs: &[Option<RgbaImage>],
    mode: Mode<'_>,
) {
    let y = TABLE_Y + HEADER_H + i as i32 * ROW_H;
    let color = if i % 2 == 0 {
        [255; 3]
    } else {
        [250, 247, 252]
    };
    rounded_panel_overlay(
        gfx,
        image,
        (PAD, y + 4, WIDTH - PAD - 1, y + ROW_H - 5),
        18,
        rgba(color, 226),
        Some(rgba([255; 3], 230)),
    );
    let placeholder = RgbaImage::from_pixel(80, 80, rgba([215, 215, 225], 180));
    for (index, (card, thumb)) in rows(&deck["cards"]).iter().zip(thumbs).enumerate() {
        let x = PAD + 15 + index as i32 * 88;
        paste_card_thumbnail_tile(
            gfx,
            static_dir,
            image,
            thumb.as_ref().unwrap_or(&placeholder),
            (x, y + 8),
            CardTileOptions {
                size: 80,
                master_rank: if thumb.is_some() {
                    number(&card["master_rank"])
                } else {
                    0
                },
                ..Default::default()
            },
        );
        let id = number(&card["card_id"]).to_string();
        let w = gfx.fonts.measure_text(MEDIUM, 11., &id).0 as i32 + 4;
        rounded_panel_overlay(
            gfx,
            image,
            (x + 80 - w, y + 8, x + 79, y + 21),
            0,
            rgba([255; 3], 180),
            None,
        );
        label(gfx, image, (x + 78, y + 8), &id, (MEDIUM, 11., [0; 3], Ra));
        let level = number(&card["skill_level"]);
        if level > 0 {
            label(
                gfx,
                image,
                (x, y + 92),
                &format!("SLv.{level}"),
                (MEDIUM, 12., [60; 3], La),
            );
        }
        if decimal(&card["skill_score_up"]) > 0. {
            label(
                gfx,
                image,
                (x + 80, y + 92),
                &format!("+{}", card["skill_score_up"]),
                (MEDIUM, 12., SKILL, Ra),
            );
        }
        for (offset, key, text) in [(0, "episode1_read", "前篇"), (40, "episode2_read", "后篇")]
        {
            let color = match card[key].as_bool() {
                Some(true) => [0, 180, 0],
                Some(false) => [220, 50, 50],
                None => [150; 3],
            };
            label(
                gfx,
                image,
                (x + offset, y + 110),
                text,
                (MEDIUM, 12., color, La),
            );
        }
    }
    let bonus = decimal(&deck["event_bonus_rate"]);
    let skill = decimal(&deck["multi_live_score_up"]);
    let live = number(&deck["live_score"]);
    let primary = if mode.bonus() {
        (format!("{bonus:.1}%"), BONUS)
    } else {
        (number(&deck["score"]).to_string(), [61, 74, 162])
    };
    let labels = mode.labels();
    let columns = [
        Some((labels[0], primary.0, primary.1)),
        (!mode.bonus()).then(|| {
            (
                "综合力",
                number(&deck["total_power"]).to_string(),
                [50, 130, 80],
            )
        }),
        (!mode.score() && !mode.bonus() && bonus > 0.)
            .then(|| ("加成", format!("{bonus:.1}%"), BONUS)),
        (skill > 0.).then(|| ("实效", format!("{skill:.1}%"), SKILL)),
        (live > 0).then(|| ("歌曲分", live.to_string(), [100, 100, 200])),
    ];
    for (offset, column) in COLUMNS.into_iter().zip(columns) {
        if let Some((title, value, color)) = column {
            let x = INFO_X + offset;
            let soft = color.map(|v| (f32::from(v) * 0.16 + 255. * 0.84) as u8);
            rounded_panel_overlay(
                gfx,
                image,
                (x, y + 10, x + 95, y + 67),
                14,
                rgba(soft, 185),
                Some(rgba(color, 82)),
            );
            label(
                gfx,
                image,
                (x + 10, y + 19),
                title,
                (MEDIUM, 13., [96, 90, 108], La),
            );
            label(gfx, image, (x + 10, y + 37), &value, (BOLD, 20., color, La));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[tokio::test]
    async fn renders_recommendation_card_row_offline() {
        let fixture = RenderFixture::new();
        fixture.write("ondemand/jp/cards.json",r#"[{"id":1,"characterId":3,"attr":"cool","cardRarityType":"rarity_1","assetbundleName":"test"}]"#);
        fixture.png(
            "ondemand/jp/startapp/thumbnail/chara/test_normal.png",
            (32, 32),
        );
        fixture.png("static/chara/cardFrame_rarity_1.png", (32, 32));
        fixture.png("static/chara/rarity_star_normal.png", (32, 32));
        fixture.png("static/chara/icon_attribute_cool.png", (32, 32));
        assert_eq!(Mode("bonus").labels(), ["加成", "", "", "", ""]);
        let card = json!({"card_id":1,"skill_level":4,"episode1_read":true});
        let output = Deck.render(&fixture.context(),json!({"recommend_type":"event","result_decks":[{"cards":[card,card,card,card,card],"score":10000,"total_power":20000,"event_bonus_rate":50,"multi_live_score_up":100,"live_score":12345}]})).await.unwrap();
        let image =
            image::load_from_memory_with_format(&output.images[0], image::ImageFormat::Jpeg)
                .unwrap()
                .into_rgb8();
        assert_eq!(image.dimensions(), (1100, 716));
        for x in [85, 173, 261, 349, 437] {
            assert!(image.get_pixel(x, 484)[1] < 100);
        }
    }
}
