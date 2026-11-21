use std::collections::{HashMap, HashSet};

use crate::components::{
    profile_header::{PjskHeaderData, draw_pjsk_profile_header},
    ui::{draw_round_panel, get_pjsk_music_jacket_cached, paste_round_image},
};
use crate::core::{RenderOutput, RenderRequestContext, Renderer, Result};
use crate::gfx::{RenderContextGfxExt, TextAnchor::*, paste_rgba};
use async_trait::async_trait;
use serde_json::Value;

use crate::renderers::common::*;

pub(crate) struct DiffRank;
const PAD: i32 = 36;
const INK: [u8; 3] = [44, 36, 58];
const MUTED: [u8; 3] = [118, 112, 132];
const ACCENT: [u8; 3] = [0, 204, 187];
const RESULT_ICONS: [&str; 4] = [
    "icon_notClear.png",
    "icon_clear.png",
    "icon_fullCombo.png",
    "icon_allPerfect.png",
];

fn rank_value(rank: &str) -> f64 {
    rank.strip_suffix(".?")
        .unwrap_or(rank)
        .parse()
        .unwrap_or(f64::NEG_INFINITY)
}

fn ranked_groups(data: &Value) -> Vec<(String, Vec<i64>)> {
    let mut groups: Vec<_> = data
        .as_object()
        .into_iter()
        .flatten()
        .map(|(rank, ids)| (rank.clone(), rows(ids).iter().map(number).collect()))
        .collect();
    // The request's JSON object may have lost its insertion order.
    groups.sort_by(|(a, _), (b, _)| {
        rank_value(b)
            .total_cmp(&rank_value(a))
            .then_with(|| a.cmp(b))
    });
    groups
}

#[async_trait]
impl Renderer for DiffRank {
    fn name(&self) -> &'static str {
        "diffrank"
    }

    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput> {
        let gfx = ctx.gfx()?;
        let server = number(&payload["pjsk_type"]) as i32;
        let difficulty = DIFFICULTIES
            .iter()
            .position(|(name, _)| payload["difficulty"] == *name)
            .unwrap_or(4);
        let groups = ranked_groups(&payload["music_data"]);
        let per_row = if payload["one_row_count"].is_null() {
            groups
                .iter()
                .map(|(_, ids)| ids.len())
                .max()
                .unwrap_or(1)
                .clamp(1, 4)
        } else {
            number(&payload["one_row_count"]).clamp(1, 4) as usize
        };

        let mut seen = HashSet::new();
        let tasks: Vec<_> = groups
            .iter()
            .flat_map(|(_, ids)| ids.iter().copied())
            .filter(|id| seen.insert(*id))
            .map(|id| {
                let ctx = ctx.clone();
                async move {
                    let image = get_pjsk_music_jacket_cached(
                        &ctx,
                        id,
                        server,
                        Some((120, 120)),
                        None,
                        true,
                    )
                    .await
                    .ok()
                    .flatten();
                    (id, image)
                }
            })
            .collect();
        let jackets: HashMap<_, _> = load_ordered(tasks, 12)
            .await?
            .into_iter()
            .filter_map(|(id, image)| image.map(|image| (id, image)))
            .collect();

        let columns = groups.len().clamp(1, 2);
        let block_w = 164 + per_row as i32 * 96 + (per_row as i32 - 1) * 14;
        let block_height = move |count: usize| 28 + 110 * count.div_ceil(per_row).max(1) as i32;
        let row_heights: Vec<_> = groups
            .chunks(columns)
            .map(|row| {
                row.iter()
                    .map(|(_, ids)| block_height(ids.len()))
                    .max()
                    .unwrap()
            })
            .collect();
        let rank_w = if groups.is_empty() {
            0
        } else {
            columns as i32 * block_w + (columns as i32 - 1) * 22
        };
        let rank_h = (row_heights.iter().sum::<i32>()
            + row_heights.len().saturating_sub(1) as i32 * 18)
            .max(1);
        let width = 760.max(rank_w + 2 * PAD);
        let has_header = !payload["header"].is_null();
        let header_h = if has_header { 258 } else { 150 };
        let title_y = header_h + 24;
        let rank_y = title_y + 86;
        let footer_y = rank_y + rank_h + 22;
        let height = footer_y + 144;
        let w = width as f32;
        let h = height as f32;
        let mut image = make_style_gradient_bg(
            (width as u32, height as u32),
            [255, 246, 250],
            [236, 244, 255],
            &[
                SoftGlow {
                    center: (w * 0.15, h / 15.),
                    radii: (w * 0.35, h * 4. / 15.),
                    color: rgba([255, 190, 220], 76),
                },
                SoftGlow {
                    center: (w * 0.875, h * 0.725),
                    radii: (w * 0.375, h * 0.475),
                    color: rgba([170, 210, 255], 68),
                },
                SoftGlow {
                    center: (w * 2. / 3., h * 5. / 28.),
                    radii: (w / 3., h * 9. / 28.),
                    color: rgba([210, 190, 255], 34),
                },
            ],
        );
        let server_label = payload["server_label"]
            .as_str()
            .filter(|text| !text.is_empty())
            .unwrap_or("JP")
            .to_uppercase();
        if has_header {
            let cards = ctx.app.master_data.load_master_data("cards.json", server)?;
            draw_pjsk_profile_header(
                ctx,
                &mut image,
                (PAD, 24, width - PAD, header_h - 10),
                &PjskHeaderData::from_payload(&payload["header"]),
                "DIFFICULTY RANK",
                server,
                Some(&card_assets(&cards)),
                Some(&[("SERVER", &server_label)]),
                true,
                false,
            )
            .await?;
        }

        let icons = RESULT_ICONS.map(|name| {
            gfx.cache
                .open_pjsk_image(
                    ctx.app.paths.static_dir.join("pics").join(name),
                    Some((28, 28)),
                )
                .ok()
        });
        render_jpeg(gfx, 90, true, move |gfx| {
            let line = Some(rgba([255; 3], 245));
            if !has_header {
                draw_round_panel(
                    &gfx.cache,
                    &mut image,
                    (PAD, 20, width - PAD, header_h - 8),
                    26,
                    rgba([255; 3], 226),
                    line,
                    true,
                );
                let (status, tip) = if payload["is_private"] == true {
                    ("成绩已隐藏", "发送“给看”可查看歌曲成绩")
                } else {
                    ("数据已无法获取", "未读取到玩家打歌数据，将仅展示难度排序")
                };
                rounded_panel_overlay(
                    gfx,
                    &mut image,
                    (PAD + 28, 46, PAD + 88, 106),
                    18,
                    rgba([255, 246, 251], 255),
                    Some(rgba([245, 218, 232], 255)),
                );
                label(
                    gfx,
                    &mut image,
                    (PAD + 58, 76),
                    "♪",
                    (RODIN, 36., ACCENT, Mm),
                );
                label(
                    gfx,
                    &mut image,
                    (PAD + 112, 50),
                    status,
                    (BOLD, 27., INK, La),
                );
                label(
                    gfx,
                    &mut image,
                    (PAD + 114, 92),
                    tip,
                    (MEDIUM, 16., MUTED, La),
                );
                rounded_panel_overlay(
                    gfx,
                    &mut image,
                    (width - PAD - 144, 42, width - PAD - 24, 74),
                    16,
                    rgba([88, 92, 118], 220),
                    None,
                );
                label(
                    gfx,
                    &mut image,
                    (width - PAD - 84, 58),
                    &server_label,
                    (RODIN, 16., [255; 3], Mm),
                );
                label(
                    gfx,
                    &mut image,
                    (width - PAD - 24, header_h - 38),
                    "DIFFICULTY RANK",
                    (RODIN, 16., MUTED, Ra),
                );
            }
            draw_round_panel(
                &gfx.cache,
                &mut image,
                (PAD, title_y, width - PAD, title_y + 64),
                24,
                rgba([255; 3], 226),
                line,
                true,
            );
            let title = fit_text(
                &gfx.fonts,
                string(&payload["title"]).trim(),
                BOLD,
                29.,
                500.,
            );
            label(
                gfx,
                &mut image,
                (PAD + 28, title_y + 24),
                &title,
                (BOLD, 29., INK, Lm),
            );
            label(
                gfx,
                &mut image,
                (PAD + 30, title_y + 48),
                "按定数从高到低排列，定数非官方，仅供参考",
                (MEDIUM, 13., MUTED, Lm),
            );
            let mode_x =
                PAD + 36 + (gfx.fonts.measure_text(BOLD, 29., &title).0 as i32 + 20).min(520);
            rounded_panel_overlay(
                gfx,
                &mut image,
                (mode_x, title_y + 13, mode_x + 94, title_y + 45),
                16,
                rgba(ACCENT, 255),
                None,
            );
            let mode = payload["mode_text"]
                .as_str()
                .filter(|text| !text.is_empty())
                .unwrap_or("CLEAR");
            label(
                gfx,
                &mut image,
                (mode_x + 47, title_y + 29),
                mode,
                (RODIN, 17., [255; 3], Mm),
            );

            let (name, color) = DIFFICULTIES[difficulty];
            let mut y = rank_y;
            for (row, row_h) in groups.chunks(columns).zip(row_heights) {
                for (column, (rank, ids)) in row.iter().enumerate() {
                    let x = (width - rank_w) / 2 + column as i32 * (block_w + 22);
                    draw_round_panel(
                        &gfx.cache,
                        &mut image,
                        (x, y, x + block_w - 8, y + block_height(ids.len()) - 8),
                        24,
                        rgba([255; 3], 234),
                        line,
                        true,
                    );
                    rounded_panel_overlay(
                        gfx,
                        &mut image,
                        (x + 18, y + 22, x + 94, y + 64),
                        21,
                        rgba(color, 255),
                        None,
                    );
                    label(
                        gfx,
                        &mut image,
                        (x + 56, y + 42),
                        rank,
                        (RODIN, 25., [255; 3], Mm),
                    );
                    label(
                        gfx,
                        &mut image,
                        (x + 56, y + 88),
                        &name.to_uppercase(),
                        (RODIN, 13., MUTED, Mm),
                    );
                    for (i, id) in ids.iter().enumerate() {
                        let px = x + 132 + (i % per_row) as i32 * 110;
                        let py = y + 20 + (i / per_row) as i32 * 110;
                        rounded_panel_overlay(
                            gfx,
                            &mut image,
                            (px - 3, py - 3, px + 99, py + 99),
                            18,
                            rgba([255; 3], 235),
                            None,
                        );
                        if let Some(jacket) = jackets.get(id) {
                            paste_round_image(
                                &gfx.cache,
                                &mut image,
                                jacket,
                                (px, py),
                                (96, 96),
                                15,
                            );
                        } else {
                            rounded_panel_overlay(
                                gfx,
                                &mut image,
                                (px, py, px + 95, py + 95),
                                15,
                                rgba([230; 3], 255),
                                None,
                            );
                            label(
                                gfx,
                                &mut image,
                                (px + 48, py + 48),
                                &id.to_string(),
                                (BOLD, 36., [80; 3], Mm),
                            );
                        }
                        if let Some(status) =
                            payload["music_result"][id.to_string()][difficulty].as_u64()
                            && let Some(Some(icon)) = icons.get(status as usize)
                        {
                            paste_rgba(&mut image, icon, px + 71, py + 71);
                        }
                    }
                }
                y += row_h + 18;
            }
            draw_round_panel(
                &gfx.cache,
                &mut image,
                (PAD, footer_y, width - PAD, footer_y + 100),
                22,
                rgba([255; 3], 205),
                line,
                true,
            );
            label(
                gfx,
                &mut image,
                (PAD + 24, footer_y + 26),
                "定数来源：https://profile.pjsekai.moe/   ※三服共用 JP 定数，非官方",
                (MEDIUM, 17., ACCENT, Lm),
            );
            label(
                gfx,
                &mut image,
                (PAD + 24, footer_y + 56),
                &format!(
                    "定数更新时间：{}   ※定数每次统计时可能会改变",
                    string(&payload["constants_time_text"])
                ),
                (MEDIUM, 15., MUTED, Lm),
            );
            let suite = string(&payload["suite_update_text"]);
            if !suite.is_empty() {
                label(
                    gfx,
                    &mut image,
                    (PAD + 24, footer_y + 82),
                    &format!("用户数据上传时间：{suite}"),
                    (MEDIUM, 15., MUTED, Lm),
                );
            }
            image
        })
        .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[tokio::test]
    async fn renders_sorted_groups_with_result_badge_offline() {
        let fixture = RenderFixture::new();
        fixture.png(
            "ondemand/jp/startapp/thumbnail/music_jacket/jacket_s_001.png",
            (32, 32),
        );
        fixture.png("static/pics/icon_allPerfect.png", (28, 28));
        let data = json!({"9.9":[2],"10.?":[1]});
        assert_eq!(ranked_groups(&data)[0].0, "10.?");
        let output = DiffRank
            .render(
                &fixture.context(),
                json!({"music_data":data,"music_result":{"1":[0,0,0,0,3]},"is_private":true}),
            )
            .await
            .unwrap();
        let image =
            image::load_from_memory_with_format(&output.images[0], image::ImageFormat::Jpeg)
                .unwrap()
                .into_rgb8();
        assert_eq!(image.dimensions(), (760, 564));
        assert!(image.get_pixel(270, 310)[1] < 90);
        assert!(image.get_pixel(337, 375)[1] < 90);
    }
}
