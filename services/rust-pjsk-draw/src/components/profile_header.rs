use std::collections::HashMap;

use crate::core::{DrawError, RenderRequestContext, Result};
use crate::gfx::{RenderContextGfxExt, TextAnchor, draw_rounded_rect, resize_lanczos};
use chrono::{DateTime, Utc};
use futures_util::future::join_all;
use image::{Rgba, RgbaImage};
use serde_json::Value;

use crate::components::{
    generatehonor, integer, truthy,
    ui::{Bounds, draw_round_panel, get_pjsk_asset_resized, paste_alpha, paste_round_image},
};

const BOLD: &str = "SourceHanSansCN-Bold.otf";
pub(crate) const MEDIUM: &str = "SourceHanSansCN-Medium.otf";
const RODIN: &str = "FOT-RodinNTLGPro-DB.ttf";

#[derive(Clone, Debug, PartialEq)]
pub struct PjskHeaderData {
    pub userid: String,
    pub name: String,
    pub rank: i64,
    pub is_private: bool,
    pub user_decks: Vec<i64>,
    pub special_training: Vec<bool>,
    pub user_profile_honors: Vec<Value>,
    pub user_honor_missions: Vec<Value>,
    pub suite_update_time: Option<Value>,
}

impl Default for PjskHeaderData {
    fn default() -> Self {
        Self::from_payload(&Value::Null)
    }
}

impl PjskHeaderData {
    pub fn from_payload(payload: &Value) -> Self {
        let list = |key| payload[key].as_array().map_or(&[][..], Vec::as_slice);
        Self {
            userid: payload
                .get("userid")
                .map_or_else(String::new, |id| match id {
                    Value::Null => "None".into(),
                    Value::Bool(true) => "True".into(),
                    Value::Bool(false) => "False".into(),
                    Value::String(id) => id.clone(),
                    _ => id.to_string(),
                }),
            name: payload["name"]
                .as_str()
                .filter(|name| !name.is_empty())
                .unwrap_or("???")
                .to_owned(),
            rank: integer(&payload["rank"]).unwrap_or_default(),
            is_private: truthy(&payload["is_private"]),
            user_decks: list("user_decks")
                .iter()
                .map(|id| id.as_i64().unwrap_or_default())
                .collect(),
            special_training: list("special_training").iter().map(truthy).collect(),
            user_profile_honors: list("user_profile_honors").to_vec(),
            user_honor_missions: list("user_honor_missions").to_vec(),
            suite_update_time: payload
                .get("suite_update_time")
                .filter(|time| !time.is_null())
                .cloned(),
        }
    }
}

fn epoch_seconds(timestamp: &Value) -> Option<i64> {
    let value = match timestamp {
        Value::Bool(value) => i32::from(*value) as f64,
        Value::String(value) => value.trim().parse().ok()?,
        _ => timestamp.as_f64()?,
    };
    if !value.is_finite() {
        return None;
    }
    let seconds = value.trunc();
    Some(if seconds > 10_000_000_000.0 {
        (seconds / 1000.0).trunc() as i64
    } else {
        seconds as i64
    })
}

pub fn format_relative_time(timestamp: &Value) -> String {
    relative_time_at(timestamp, Utc::now().timestamp())
}

fn relative_time_at(timestamp: &Value, now: i64) -> String {
    let Some(timestamp) = epoch_seconds(timestamp) else {
        return "未知".into();
    };
    let diff = i128::from(now) - i128::from(timestamp);
    match diff {
        ..0 => "未来".into(),
        0..60 => "刚刚".into(),
        60..3600 => format!("{}分钟前", diff / 60),
        3600..86400 => format!("{}小时前", diff / 3600),
        86400..2592000 => format!("{}天前", diff / 86400),
        _ => format!("{}个月前", diff / 2592000),
    }
}

pub fn format_absolute_time(timestamp: &Value) -> String {
    epoch_seconds(timestamp)
        .and_then(|time| DateTime::from_timestamp(time, 0))
        .map_or_else(
            || "未知".into(),
            |time| {
                time.with_timezone(&chrono_tz::Asia::Shanghai)
                    .format("%m-%d %H:%M")
                    .to_string()
            },
        )
}

#[allow(clippy::too_many_arguments)]
pub async fn draw_pjsk_profile_header(
    ctx: &RenderRequestContext,
    img: &mut RgbaImage,
    xy: Bounds,
    data: &PjskHeaderData,
    module_label: &str,
    pjsk_type: i32,
    card_asset_map: Option<&HashMap<i64, String>>,
    extra_badges: Option<&[(&str, &str)]>,
    show_cutout: bool,
    compact: bool,
) -> Result<()> {
    let gfx = ctx.gfx()?;
    let fonts = &gfx.fonts;
    let (x1, y1, x2, y2) = xy;
    draw_round_panel(
        &gfx.cache,
        img,
        xy,
        if compact { 24 } else { 30 },
        Rgba([255, 255, 255, 218]),
        Some(Rgba([255, 255, 255, 230])),
        true,
    );
    header_chip(
        img,
        (x1 + 22, y1 + 22, x1 + 118, y1 + 30),
        4.0,
        [255, 128, 178],
        None,
    );
    fonts.draw_text(
        img,
        ((x2 - 24) as f32, (y1 + 26) as f32),
        module_label,
        RODIN,
        if compact { 16.0 } else { 18.0 },
        Rgba([150, 135, 165, 255]),
        TextAnchor::Ra,
        None,
    );

    let avatar_size = if compact { 104 } else { 132 };
    let avatar_x = x1 + 28;
    let avatar_y = y1 + (y2 - y1 - avatar_size).div_euclid(2) + if compact { 8 } else { 6 };
    draw_round_panel(
        &gfx.cache,
        img,
        (
            avatar_x,
            avatar_y,
            avatar_x + avatar_size,
            avatar_y + avatar_size,
        ),
        if compact { 18 } else { 22 },
        Rgba([255, 255, 255, 245]),
        Some(Rgba([255; 4])),
        true,
    );
    let asset = data
        .user_decks
        .first()
        .and_then(|id| card_asset_map.and_then(|map| map.get(id)))
        .filter(|name| !name.is_empty());
    let suffix = if data.special_training.first().copied().unwrap_or(false) {
        "after_training"
    } else {
        "normal"
    };
    if let Some(asset) = asset {
        if let Ok(Some(avatar)) = get_pjsk_asset_resized(
            ctx,
            "startapp/thumbnail/chara",
            &format!("{asset}_{suffix}.png"),
            pjsk_type,
            None,
        )
        .await
        {
            let side = (avatar_size - 14) as u32;
            paste_round_image(
                &gfx.cache,
                img,
                &avatar,
                (avatar_x + 7, avatar_y + 7),
                (side, side),
                16,
            );
        }
        if show_cutout && !compact {
            let path = format!("startapp/character/member_cutout_trm/{asset}");
            let file = format!("{suffix}.png");
            let mut cutout = ctx
                .app
                .get_asset(&path, &file, pjsk_type, ctx.policy, false)
                .await;
            if matches!(cutout, Ok(None) | Err(DrawError::LocalOnlyAssetNotFound(_))) {
                cutout = ctx
                    .app
                    .get_asset(
                        &format!("{path}/{suffix}"),
                        &file,
                        pjsk_type,
                        ctx.policy,
                        false,
                    )
                    .await;
            }
            if let Ok(Some(cutout)) = cutout {
                let scale = 0.38_f64.min(230.0 / f64::from(cutout.height().max(1)));
                let w = (f64::from(cutout.width()) * scale) as u32;
                let h = (f64::from(cutout.height()) * scale) as u32;
                if w > 0 && h > 0 {
                    paste_alpha(
                        img,
                        &resize_lanczos(&cutout, w, h),
                        (x2 - w as i32 - 36, y1 + 22),
                    );
                }
            }
        }
    }

    let info_x = avatar_x + avatar_size + 28;
    let name_size = if compact { 30.0 } else { 36.0 };
    let name = fonts.fit_text_ellipsis(
        BOLD,
        name_size,
        &data.name,
        (x2 - info_x - if compact { 180 } else { 280 }) as f32,
    );
    fonts.draw_text(
        img,
        (info_x as f32, (y1 + if compact { 38 } else { 52 }) as f32),
        &name,
        BOLD,
        name_size,
        Rgba([44, 36, 58, 255]),
        TextAnchor::La,
        None,
    );

    let chip_y = y1 + if compact { 82 } else { 106 };
    let rank_w = if compact { 118 } else { 132 };
    header_chip(
        img,
        (info_x, chip_y, info_x + rank_w, chip_y + 36),
        18.0,
        [244, 238, 255],
        Some([224, 214, 246]),
    );
    fonts.draw_text(
        img,
        ((info_x + rank_w / 2) as f32, (chip_y + 18) as f32),
        &format!("Rank {}", data.rank),
        RODIN,
        if compact { 18.0 } else { 20.0 },
        Rgba([88, 92, 118, 255]),
        TextAnchor::Mm,
        None,
    );

    let id_text = format!(
        "ID {}",
        if data.is_private {
            "保密"
        } else {
            &data.userid
        }
    );
    let id_w = 126.max(fonts.measure_text(RODIN, 15.0, &id_text).0 as i32 + 34);
    let id_x = info_x + rank_w + 14;
    header_chip(
        img,
        (id_x, chip_y, id_x + id_w, chip_y + 36),
        18.0,
        [238, 248, 255],
        Some([215, 232, 248]),
    );
    fonts.draw_text(
        img,
        ((id_x + 16) as f32, (chip_y + 18) as f32),
        &id_text,
        RODIN,
        15.0,
        Rgba([82, 92, 110, 255]),
        TextAnchor::Lm,
        None,
    );

    let mut badge_x = id_x + id_w + 12;
    let pink = [255, 246, 251];
    let pink_border = Some([245, 218, 232]);
    let pink_text = Rgba([132, 92, 116, 255]);
    for (label, value) in extra_badges.unwrap_or_default() {
        let text = format!("{label} {value}");
        let width = 92.max(fonts.measure_text(RODIN, 15.0, &text).0 as i32 + 28);
        if badge_x + width > x2 - 32 {
            break;
        }
        header_chip(
            img,
            (badge_x, chip_y, badge_x + width, chip_y + 36),
            18.0,
            pink,
            pink_border,
        );
        fonts.draw_text(
            img,
            ((badge_x + width / 2) as f32, (chip_y + 18) as f32),
            &text,
            RODIN,
            15.0,
            pink_text,
            TextAnchor::Mm,
            None,
        );
        badge_x += width + 10;
    }
    let update_y = y1 + if compact { 126 } else { 154 };
    if let Some(time) = data.suite_update_time.as_ref().filter(|time| truthy(time))
        && update_y + 28 < y2
    {
        let text = format!(
            "数据更新 {} · {}",
            format_relative_time(time),
            format_absolute_time(time)
        );
        let width = 230
            .max(fonts.measure_text(MEDIUM, 14.0, &text).0 as i32 + 28)
            .min(x2 - info_x - 32);
        header_chip(
            img,
            (info_x, update_y, info_x + width, update_y + 28),
            14.0,
            pink,
            pink_border,
        );
        fonts.draw_text(
            img,
            ((info_x + 14) as f32, (update_y + 14) as f32),
            &text,
            MEDIUM,
            14.0,
            pink_text,
            TextAnchor::Lm,
            None,
        );
    }

    if !compact {
        let honors = data.user_profile_honors.iter().filter_map(|honor| {
            let main = match honor["seq"].as_i64() {
                Some(1) => true,
                Some(2 | 3) => false,
                _ => return None,
            };
            Some(async move {
                (
                    main,
                    generatehonor(ctx, honor, main, &data.user_honor_missions, pjsk_type).await,
                )
            })
        });
        let mut x = info_x;
        for (main, result) in join_all(honors).await {
            let Ok(Some(honor)) = result else { continue };
            let width = if main { 188 } else { 90 };
            if x + width > x2 - 30 {
                break;
            }
            paste_alpha(img, &resize_lanczos(&honor, width as u32, 40), (x, y2 - 52));
            x += width + 10;
        }
    }
    Ok(())
}

fn header_chip(
    img: &mut RgbaImage,
    (x1, y1, x2, y2): Bounds,
    radius: f32,
    fill: [u8; 3],
    outline: Option<[u8; 3]>,
) {
    let rgba = |[r, g, b]: [u8; 3]| Rgba([r, g, b, 255]);
    draw_rounded_rect(
        img,
        (x1 as f32, y1 as f32, x2 as f32 + 1.0, y2 as f32 + 1.0),
        radius,
        Some(rgba(fill)),
        outline.map(|color| (rgba(color), 1.0)),
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn payload_defaults_and_preserves_player_details() {
        let data = PjskHeaderData::from_payload(&json!({
            "userid": 123456, "name": "", "rank": 42, "is_private": "yes",
            "user_decks": [17, 25], "special_training": [false, true],
            "user_profile_honors": [{"seq": 1}], "user_honor_missions": null,
            "suite_update_time": "1700000000000",
        }));
        assert_eq!(data.userid, "123456");
        assert_eq!(data.name, "???");
        assert_eq!(data.rank, 42);
        assert!(data.is_private);
        assert_eq!(data.user_decks, [17, 25]);
        assert_eq!(data.special_training, [false, true]);
        assert_eq!(data.user_profile_honors, [json!({"seq":1})]);
        assert!(data.user_honor_missions.is_empty());
        assert_eq!(data.suite_update_time, Some(json!("1700000000000")));
        assert_eq!(PjskHeaderData::default().userid, "");
    }

    #[test]
    fn time_formats_seconds_milliseconds_and_boundaries() {
        let now = 1_700_000_000;
        for (diff, expected) in [
            (-1, "未来"),
            (59, "刚刚"),
            (60, "1分钟前"),
            (3600, "1小时前"),
            (86400, "1天前"),
            (2592000, "1个月前"),
        ] {
            assert_eq!(relative_time_at(&json!((now - diff) * 1000), now), expected);
        }
        assert_eq!(relative_time_at(&json!("bad"), now), "未知");
        assert_eq!(format_absolute_time(&json!("1700000000000")), "11-15 06:13");
        assert_eq!(format_absolute_time(&json!(now)), "11-15 06:13");
    }
}
