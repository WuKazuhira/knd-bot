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
const HEADER_H: i32 = 38;
const ROW_H: i32 = 160;
const CARD_X0: i32 = PAD + 42;
const CARD_STEP: i32 = 86;
const INFO_X: i32 = 510;
const INK: [u8; 3] = [88, 92, 118];
const WARN: [u8; 3] = [235, 64, 72];
const BONUS: [u8; 3] = [206, 116, 40];
const SKILL: [u8; 3] = [152, 72, 168];
static ROW_LIMIT: LazyLock<Semaphore> = LazyLock::new(|| Semaphore::new(*CARD_RENDER_LIMIT));

#[derive(Clone, Copy)]
struct Theme {
    accent: [u8; 3],
    bg_top: [u8; 3],
    bg_bottom: [u8; 3],
    glow_a: [u8; 3],
    glow_b: [u8; 3],
}

fn attr_theme(attr: &str) -> Theme {
    match attr {
        "cool" => Theme {
            accent: [62, 128, 210],
            bg_top: [244, 249, 255],
            bg_bottom: [232, 242, 255],
            glow_a: [165, 210, 255],
            glow_b: [175, 225, 250],
        },
        "cute" => Theme {
            accent: [228, 92, 146],
            bg_top: [255, 245, 250],
            bg_bottom: [246, 238, 255],
            glow_a: [255, 190, 220],
            glow_b: [215, 195, 255],
        },
        "happy" => Theme {
            accent: [232, 132, 44],
            bg_top: [255, 249, 240],
            bg_bottom: [255, 240, 232],
            glow_a: [255, 212, 155],
            glow_b: [255, 195, 175],
        },
        "mysterious" => Theme {
            accent: [142, 86, 204],
            bg_top: [249, 245, 255],
            bg_bottom: [238, 240, 255],
            glow_a: [210, 180, 255],
            glow_b: [180, 205, 255],
        },
        "pure" => Theme {
            accent: [54, 168, 114],
            bg_top: [244, 253, 248],
            bg_bottom: [234, 248, 246],
            glow_a: [170, 238, 200],
            glow_b: [175, 225, 235],
        },
        _ => Theme {
            accent: INK,
            bg_top: [255, 246, 250],
            bg_bottom: [236, 244, 255],
            glow_a: [255, 190, 220],
            glow_b: [170, 210, 255],
        },
    }
}

fn target_column(target: &str, mode: Mode<'_>) -> usize {
    if mode.bonus() {
        return 0;
    }
    match target {
        "power" => 1,
        "bonus" => 2,
        "skill" => 3,
        _ => 0,
    }
}

/// Returns non-empty column indices in display order with the prioritized `target` column first.
fn visible_column_indices(target: &str, mode: Mode<'_>) -> Vec<usize> {
    let labels = mode.labels();
    let primary = target_column(target, mode);
    let mut order = Vec::with_capacity(5);
    if !labels[primary].is_empty() {
        order.push(primary);
    }
    for (idx, label) in labels.iter().enumerate() {
        if idx != primary && !label.is_empty() {
            order.push(idx);
        }
    }
    order
}

/// Computes `(card_width, step)` so `count` metric cards fill the right panel evenly without gaps.
fn metric_column_layout(count: usize) -> (i32, i32) {
    let total_w = WIDTH - PAD - 14 - INFO_X; // 546px
    match count {
        0 | 1 => (200, 210),
        2 => (266, 280),
        3 => (174, 186),
        4 => (129, 139),
        _ => {
            let n = count as i32;
            let gap = 9;
            let card_w = (total_w - gap * (n - 1)) / n;
            (card_w, card_w + gap)
        }
    }
}

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
        } else if self.score() {
            ["分数", "综合力", "", "实效", ""]
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
        let options = &payload["options"];
        let additional = &payload["additional"];
        let event_id = number(&options["event_id"]);
        let (unit, attr) = event_filters(ctx, server, event_id, options, additional);
        let theme = attr_theme(&attr);
        let decks = rows(&payload["result_decks"]);
        let table_height = if decks.is_empty() {
            60
        } else {
            HEADER_H + decks.len() as i32 * ROW_H
        };
        let height = TABLE_Y + table_height + 108;
        let h = height as f32;
        let mut image = make_style_gradient_bg(
            (WIDTH as u32, height as u32),
            theme.bg_top,
            theme.bg_bottom,
            &[
                SoftGlow {
                    center: (WIDTH as f32 / 6., h / 15.),
                    radii: (WIDTH as f32 / 3., h * 4. / 15.),
                    color: rgba(theme.glow_a, 72),
                },
                SoftGlow {
                    center: (WIDTH as f32 * 0.875, h * 0.75),
                    radii: (WIDTH as f32 * 0.375, h * 5. / 12.),
                    color: rgba(theme.glow_b, 64),
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
        let (banner, event_name) = if matches!(string(&payload["recommend_type"]), "event" | "wl") {
            event_banner(ctx, server, event_id).await
        } else {
            (None, String::new())
        };
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

            let target = options["target"].as_str().unwrap_or("score");
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
            let settings: Vec<_> = ["unit_filter", "attr_filter"]
                .into_iter()
                .map(|key| string(&additional[key]))
                .filter(|value| !value.is_empty())
                .map(|value| format!("仅{value}"))
                .collect();
            if !settings.is_empty() {
                label(
                    gfx,
                    &mut image,
                    (WIDTH - PAD - 10, 356),
                    &settings.join("  "),
                    (MEDIUM, 14., [120, 110, 130], Rm),
                );
            }

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
                    10,
                    rgba(theme.accent, 235),
                    None,
                );
                label(
                    gfx,
                    &mut image,
                    (PAD + 16, TABLE_Y + 11),
                    "推荐卡组",
                    (BOLD, 15., [255; 3], La),
                );
                let col_order = visible_column_indices(target, mode);
                let (_, step) = metric_column_layout(col_order.len());
                let labels = mode.labels();
                let pt_header = additional
                    .get("boost")
                    .filter(|boost| !boost.is_null())
                    .map(|boost| format!("PT（{boost}火）"));
                for (slot, &col_idx) in col_order.iter().enumerate() {
                    let text = labels[col_idx];
                    let header_text = if text == "PT" {
                        pt_header.as_deref().unwrap_or(text)
                    } else {
                        text
                    };
                    label(
                        gfx,
                        &mut image,
                        (INFO_X + slot as i32 * step + 10, TABLE_Y + 11),
                        header_text,
                        (BOLD, 15., [255; 3], La),
                    );
                }
                let first_deck = decks.first();
                for (i, (deck, thumbs)) in decks.iter().zip(thumbnails).enumerate() {
                    draw_row(
                        gfx,
                        &static_dir,
                        &mut image,
                        i,
                        deck,
                        first_deck,
                        &thumbs,
                        mode,
                        target,
                        theme,
                    );
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
    first_deck: Option<&Value>,
    thumbs: &[Option<RgbaImage>],
    mode: Mode<'_>,
    target: &str,
    theme: Theme,
) {
    let y = TABLE_Y + HEADER_H + i as i32 * ROW_H;
    let is_top = i == 0;
    let row_bg = if is_top {
        theme
            .accent
            .map(|v| (f32::from(v) * 0.08 + 255. * 0.92) as u8)
    } else if i % 2 == 0 {
        [255; 3]
    } else {
        [250, 248, 253]
    };
    let row_outline = if is_top {
        Some(rgba(theme.accent, 145))
    } else {
        Some(rgba([230, 232, 244], 210))
    };
    rounded_panel_overlay(
        gfx,
        image,
        (PAD, y + 4, WIDTH - PAD - 1, y + ROW_H - 5),
        16,
        rgba(row_bg, 236),
        row_outline,
    );
    if is_top {
        rounded_panel_overlay(
            gfx,
            image,
            (PAD + 5, y + 12, PAD + 10, y + ROW_H - 13),
            3,
            rgba(theme.accent, 235),
            None,
        );
    }

    // 排名徽章 #1 / #2 ...
    let rank_bg = if is_top {
        rgba(theme.accent, 240)
    } else {
        rgba([232, 234, 244], 235)
    };
    let rank_fg = if is_top { [255; 3] } else { [92, 96, 118] };
    rounded_panel_overlay(
        gfx,
        image,
        (PAD + 14, y + 12, PAD + 37, y + 36),
        8,
        rank_bg,
        None,
    );
    label(
        gfx,
        image,
        (PAD + 25, y + 24),
        &format!("#{}", i + 1),
        (BOLD, 12., rank_fg, Mm),
    );

    let placeholder = RgbaImage::from_pixel(80, 80, rgba([215, 215, 225], 180));
    for (index, (card, thumb)) in rows(&deck["cards"]).iter().zip(thumbs).enumerate() {
        let x = CARD_X0 + index as i32 * CARD_STEP;
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
        let w = gfx.fonts.measure_text(MEDIUM, 10.5, &id).0 as i32 + 8;
        rounded_panel_overlay(
            gfx,
            image,
            (x + 79 - w, y + 9, x + 78, y + 24),
            5,
            rgba([24, 28, 42], 185),
            None,
        );
        label(
            gfx,
            image,
            (x + 75, y + 16),
            &id,
            (MEDIUM, 10.5, [255; 3], Rm),
        );
        let level = number(&card["skill_level"]);
        if level > 0 {
            label(
                gfx,
                image,
                (x, y + 93),
                &format!("SLv.{level}"),
                (MEDIUM, 12., [68, 72, 92], La),
            );
        }
        if decimal(&card["skill_score_up"]) > 0. {
            label(
                gfx,
                image,
                (x + 80, y + 93),
                &format!("+{}", card["skill_score_up"]),
                (MEDIUM, 12., SKILL, Ra),
            );
        }
        for (offset, key, text) in [(0, "episode1_read", "前篇"), (41, "episode2_read", "后篇")]
        {
            let (fg, bg) = match card[key].as_bool() {
                Some(true) => ([32, 148, 72], rgba([228, 248, 234], 235)),
                Some(false) => ([214, 48, 58], rgba([255, 232, 234], 235)),
                None => ([130, 134, 150], rgba([238, 240, 246], 210)),
            };
            rounded_panel_overlay(
                gfx,
                image,
                (x + offset, y + 113, x + offset + 38, y + 132),
                6,
                bg,
                None,
            );
            label(
                gfx,
                image,
                (x + offset + 19, y + 122),
                text,
                (MEDIUM, 11., fg, Mm),
            );
        }
    }
    let score = number(&deck["score"]);
    let total_power = number(&deck["total_power"]);
    let bonus = decimal(&deck["event_bonus_rate"]);
    let skill = decimal(&deck["multi_live_score_up"]);
    let live = number(&deck["live_score"]);
    let primary = if mode.bonus() {
        (format!("{bonus:.1}%"), BONUS)
    } else {
        (score.to_string(), [61, 74, 162])
    };
    let labels = mode.labels();
    let columns = [
        Some((labels[0], primary.0, primary.1)),
        (!mode.bonus()).then(|| ("综合力", total_power.to_string(), [50, 130, 80])),
        (!mode.score() && !mode.bonus() && bonus > 0.)
            .then(|| ("加成", format!("{bonus:.1}%"), BONUS)),
        (skill > 0.).then(|| ("实效", format!("{skill:.1}%"), SKILL)),
        (!mode.score() && live > 0).then(|| ("歌曲分", live.to_string(), [100, 100, 200])),
    ];
    let col_order = visible_column_indices(target, mode);
    let (card_w, step) = metric_column_layout(col_order.len());
    for (slot, &col_idx) in col_order.iter().enumerate() {
        if let Some((title, value, color)) = &columns[col_idx] {
            let x = INFO_X + slot as i32 * step;
            let is_primary = slot == 0;
            if is_primary {
                rounded_panel_overlay(
                    gfx,
                    image,
                    (x, y + 10, x + card_w, y + 70),
                    14,
                    rgba(theme.accent, 235),
                    Some(rgba(theme.accent, 255)),
                );
                label(
                    gfx,
                    image,
                    (x + 10, y + 19),
                    title,
                    (MEDIUM, 13., [242, 246, 255], La),
                );
                label(
                    gfx,
                    image,
                    (x + 10, y + 38),
                    value,
                    (BOLD, 20., [255; 3], La),
                );
            } else {
                let soft = color.map(|v| (f32::from(v) * 0.14 + 255. * 0.86) as u8);
                rounded_panel_overlay(
                    gfx,
                    image,
                    (x, y + 10, x + card_w, y + 70),
                    14,
                    rgba(soft, 195),
                    Some(rgba(*color, 72)),
                );
                label(
                    gfx,
                    image,
                    (x + 10, y + 19),
                    title,
                    (MEDIUM, 13., [96, 90, 108], La),
                );
                label(gfx, image, (x + 10, y + 38), value, (BOLD, 20., *color, La));
            }
        }
    }

    // 右侧下半区：最优说明或与 #1 的分差对比
    let strip_right = WIDTH - PAD - 14;
    rounded_panel_overlay(
        gfx,
        image,
        (INFO_X, y + 84, strip_right, y + 138),
        12,
        rgba([246, 247, 252], 220),
        Some(rgba([226, 229, 242], 210)),
    );
    if is_top {
        label(
            gfx,
            image,
            (INFO_X + 14, y + 111),
            "★ 最推荐卡组",
            (BOLD, 14., theme.accent, Lm),
        );
        label(
            gfx,
            image,
            (strip_right - 14, y + 111),
            "当前条件下综合表现最佳",
            (MEDIUM, 12.5, [112, 116, 136], Rm),
        );
    } else if let Some(top) = first_deck {
        let mut diffs = Vec::new();
        let d_skill = skill - decimal(&top["multi_live_score_up"]);
        let d_score = score - number(&top["score"]);
        let d_power = total_power - number(&top["total_power"]);
        let d_bonus = bonus - decimal(&top["event_bonus_rate"]);

        for &col_idx in &col_order {
            match col_idx {
                0 if !mode.bonus() && d_score != 0 => {
                    diffs.push(format!("{}: {d_score:+}", labels[0]));
                }
                1 if !mode.bonus() && d_power != 0 => {
                    diffs.push(format!("综合力: {d_power:+}"));
                }
                2 if (mode.bonus() || !mode.score()) && d_bonus.abs() >= 0.05 => {
                    diffs.push(format!("加成: {d_bonus:+.1}%"));
                }
                3 if d_skill.abs() >= 0.05 => {
                    diffs.push(format!("实效: {d_skill:+.1}%"));
                }
                _ => {}
            }
        }
        let summary = if diffs.is_empty() {
            "与 #1 各项关键数值持平".to_owned()
        } else {
            diffs.join("   |   ")
        };
        label(
            gfx,
            image,
            (INFO_X + 14, y + 111),
            "较 #1 对比",
            (BOLD, 13., [96, 100, 122], Lm),
        );
        label(
            gfx,
            image,
            (strip_right - 14, y + 111),
            &summary,
            (MEDIUM, 13., [118, 102, 126], Rm),
        );
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
        assert_eq!(image.dimensions(), (1100, 702));
        for x in [112, 198, 284, 370, 456] {
            assert!(image.get_pixel(x, 482)[1] < 100);
        }
    }
}
