use std::{io::Write, path::Path, time::SystemTime};

use crate::components::ui::{draw_round_panel, get_pjsk_music_jacket_cached, paste_round_image};
use crate::core::{AssetSourcePolicy, RenderOutput, RenderRequestContext, Renderer, Result};
use crate::gfx::{GfxContext, PJSK_WATERMARK_TEXT, RenderContextGfxExt, TextAnchor, paste_rgba};
use async_trait::async_trait;
use image::{ImageEncoder, Rgba, RgbaImage, codecs::png::PngEncoder};
use serde_json::Value;

use crate::renderers::common::*;

pub(crate) struct Song;
const PJSK_STYLE_ACCENT: Rgba<u8> = Rgba([0, 204, 187, 255]);
const INK: Rgba<u8> = Rgba([64, 48, 72, 255]);
const MUTED: Rgba<u8> = Rgba([130, 104, 138, 255]);
const WHITE: Rgba<u8> = Rgba([255; 4]);
const DIFFICULTIES: [&str; 5] = ["easy", "normal", "hard", "expert", "master"];

fn payload_ids(payload: &Value) -> Result<(i64, i32)> {
    let id = number(&payload["music_id"]);
    if id <= 0 {
        return Err(crate::core::DrawError::InvalidPayload(
            "music_id must be positive".into(),
        ));
    }
    Ok((id, number(&payload["pjsk_type"]) as i32))
}

fn modified(path: &Path) -> Option<SystemTime> {
    path.metadata().ok()?.modified().ok()
}

fn fresh(cached: SystemTime, difficulty: SystemTime, published: i64, now: i64) -> bool {
    cached > difficulty
        && (now < published
            || cached
                .duration_since(SystemTime::UNIX_EPOCH)
                .is_ok_and(|age| age.as_millis() as i128 >= i128::from(published)))
}

fn difficulty_rows(data: &Value, id: i64) -> [Value; 5] {
    let matching: Vec<_> = rows(data)
        .iter()
        .filter(|row| number(&row["musicId"]) == id)
        .collect();
    std::array::from_fn(|i| {
        matching
            .iter()
            .find(|row| row["musicDifficulty"] == DIFFICULTIES[i])
            .copied()
            .or_else(|| {
                matching
                    .get(i)
                    .copied()
                    .filter(|row| row["musicDifficulty"].is_null())
            })
            .cloned()
            .unwrap_or_default()
    })
}

async fn duration(
    ctx: &RenderRequestContext,
    vocal: Option<&Value>,
    filler: f64,
    server: i32,
) -> Option<f64> {
    let asset = vocal?.get("assetbundleName")?.as_str()?;
    let path = ctx
        .app
        .ensure_asset_path(
            &format!("ondemand/music/long/{asset}"),
            &format!("{asset}.mp3"),
            server,
            ctx.policy != AssetSourcePolicy::LocalOnly,
        )
        .await?;
    tokio::task::spawn_blocking(move || {
        mp3_duration::from_path(path)
            .ok()
            .map(|length| length.as_secs_f64() - filler)
    })
    .await
    .ok()
    .flatten()
}

#[async_trait]
impl Renderer for Song {
    fn name(&self) -> &'static str {
        "pjskinfo"
    }

    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput> {
        let (id, server) = payload_ids(&payload)?;
        let server_name = ctx.app.server_name(server);
        let root = ctx.app.paths.ondemand_dir.join(server_name);
        let directory = root.join("pics/pjskinfo");
        let cache = directory.join(format!("pjskinfo_v7_{id}.png"));
        let master = &ctx.app.master_data;
        let render_started = SystemTime::now();
        let now = chrono::DateTime::<chrono::Utc>::from(render_started).timestamp_millis();
        let realtime = root.join("realtime/musicDifficulties.json");
        if let (Some(cached), Some(difficulty)) = (
            modified(&cache),
            modified(&realtime).or_else(|| modified(&root.join("musicDifficulties.json"))),
        ) && cached > difficulty
        {
            let first = master.master_data_first_by_id("musics.json", server, "id")?;
            let first = first
                .get(&id.to_string())
                .ok_or_else(|| render_error("找不到对应曲目"))?;
            if fresh(cached, difficulty, number(&first["publishedAt"]), now)
                && let Ok(bytes) = tokio::fs::read(&cache).await
            {
                return Ok(RenderOutput::single(bytes));
            }
        }
        let songs = master.master_data_by_id("musics.json", server, "id")?;
        let music = songs.get(&id.to_string()).cloned().unwrap_or_default();
        let difficulties = master.load_master_data("musicDifficulties.json", server)?;
        let charts = difficulty_rows(&difficulties, id);
        let vocal_data = master.load_master_data("musicVocals.json", server)?;
        let mut vocals: Vec<_> = rows(&vocal_data)
            .iter()
            .filter(|row| number(&row["musicId"]) == id)
            .cloned()
            .collect();
        let (jacket, length) = tokio::join!(
            get_pjsk_music_jacket_cached(ctx, id, server, Some((540, 540)), None, false),
            duration(
                ctx,
                vocals.first(),
                music["fillerSec"].as_f64().unwrap_or_default(),
                server
            ),
        );
        let outside = master
            .master_data_by_id("outsideCharacters.json", server, "id")
            .ok();
        vocals.sort_by_key(|v| number(&v["seq"]));
        let gfx = ctx.gfx()?;
        let server_name = server_name.to_uppercase();
        let static_dir = ctx.app.paths.static_dir.clone();
        tokio::task::spawn_blocking(move || {
            let leak = now < number(&music["publishedAt"]);
            let image = compose(
                &gfx,
                &static_dir,
                id,
                &server_name,
                &music,
                jacket?,
                length,
                &charts,
                &vocals,
                outside.as_deref(),
                leak,
            );
            let rgb = image::DynamicImage::ImageRgba8(image).into_rgb8();
            let mut bytes = Vec::new();
            PngEncoder::new(&mut bytes)
                .write_image(
                    rgb.as_raw(),
                    rgb.width(),
                    rgb.height(),
                    image::ExtendedColorType::Rgb8,
                )
                .map_err(render_error)?;
            std::fs::create_dir_all(&directory).map_err(render_error)?;
            // Publish only complete images, even when two requests miss the same cache.
            static NEXT_FILE: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
            let serial = NEXT_FILE.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            let temp = directory.join(format!(
                ".pjskinfo-{id}-{}-{serial}.tmp",
                std::process::id()
            ));
            let mut file = std::fs::File::create_new(&temp).map_err(render_error)?;
            let result = file
                .write_all(&bytes)
                // Changes to difficulty data or release status during a render invalidate this image.
                .and_then(|()| file.set_modified(render_started))
                .and_then(|()| std::fs::rename(&temp, &cache));
            if result.is_err() {
                let _ = std::fs::remove_file(temp);
            }
            result.map_err(render_error)?;
            Ok(RenderOutput::single(bytes))
        })
        .await
        .map_err(render_error)?
    }
}

#[allow(clippy::too_many_arguments)]
fn compose(
    gfx: &GfxContext,
    static_dir: &Path,
    id: i64,
    server: &str,
    music: &Value,
    jacket: Option<RgbaImage>,
    length: Option<f64>,
    charts: &[Value; 5],
    vocals: &[Value],
    outside: Option<&crate::core::MasterIndex>,
    leak: bool,
) -> RgbaImage {
    use TextAnchor::{La, Mm, Ra};
    let mut canvas = make_style_gradient_bg(
        (1920, 1080),
        [255, 246, 250],
        [236, 244, 255],
        &[
            SoftGlow {
                center: (288., 45.),
                radii: (672., 315.),
                color: rgba([255, 190, 220], 76),
            },
            SoftGlow {
                center: (1680., 783.),
                radii: (720., 513.),
                color: rgba([170, 210, 255], 68),
            },
            SoftGlow {
                center: (1280., 180.),
                radii: (640., 360.),
                color: rgba([210, 190, 255], 32),
            },
        ],
    );
    let text = |canvas: &mut RgbaImage, x, y, value: &str, font, size, color, anchor| {
        gfx.fonts
            .draw_text(canvas, (x, y), value, font, size, color, anchor, None);
    };
    let badge =
        |canvas: &mut RgbaImage, (x, y, w, h): (i32, i32, i32, i32), value: &str, size, color| {
            rounded_panel_overlay(
                gfx,
                canvas,
                (x, y, x + w, y + h),
                (h / 2) as u32,
                color,
                None,
            );
            text(
                canvas,
                (x + w / 2) as f32,
                (y + h / 2) as f32,
                value,
                RODIN,
                size,
                WHITE,
                Mm,
            );
        };
    for (bounds, radius, alpha) in [
        ((70, 132, 670, 732), 36, 236),
        ((70, 758, 670, 1008), 30, 218),
        ((710, 82, 1848, 286), 34, 224),
        ((710, 318, 1848, 640), 34, 214),
        ((710, 656, 1848, 812), 30, 204),
        ((710, 836, 1848, 1020), 30, 218),
    ] {
        draw_round_panel(
            &gfx.cache,
            &mut canvas,
            bounds,
            radius,
            rgba([255; 3], alpha),
            Some(rgba([255; 3], 245)),
            true,
        );
    }
    text(
        &mut canvas,
        64.,
        42.,
        "PROJECT SEKAI",
        RODIN,
        44.,
        rgba([255; 3], 170),
        La,
    );
    text(
        &mut canvas,
        64.,
        92.,
        "MUSIC DATABASE",
        RODIN,
        24.,
        rgba([255; 3], 128),
        La,
    );
    if let Some(jacket) = jacket {
        paste_round_image(&gfx.cache, &mut canvas, &jacket, (100, 162), (540, 540), 28);
    } else {
        rounded_panel_overlay(
            gfx,
            &mut canvas,
            (100, 162, 640, 702),
            28,
            rgba([238, 234, 246], 255),
            None,
        );
        text(&mut canvas, 370., 432., "NO JACKET", RODIN, 34., MUTED, Mm);
    }
    text(
        &mut canvas,
        112.,
        790.,
        &format!("MUSIC ID  #{id}"),
        RODIN,
        34.,
        INK,
        La,
    );
    badge(
        &mut canvas,
        (112, 846, 110, 42),
        server,
        22.,
        rgba([88, 92, 118], 255),
    );
    let adjusted = charts[4]
        .get("playLevelAdjust")
        .is_some_and(|v| v.as_f64() != Some(0.0));
    if leak {
        badge(
            &mut canvas,
            (240, 846, 110, 42),
            "LEAK",
            22.,
            rgba([238, 67, 102], 255),
        );
    } else if !adjusted {
        badge(
            &mut canvas,
            (240, 846, 110, 42),
            "NEW",
            22.,
            PJSK_STYLE_ACCENT,
        );
    }
    let mut icon_x = 112;
    for category in rows(&music["categories"]) {
        let kind = category
            .as_str()
            .unwrap_or_else(|| string(&category["musicCategoryName"]));
        let kind = if kind == "mv" { "mv_3d" } else { kind };
        if !kind.is_empty()
            && kind != "image"
            && kind.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
            && let Ok(icon) = gfx
                .cache
                .open_pjsk_image(static_dir.join(format!("pics/{kind}.png")), Some((52, 52)))
        {
            paste_rgba(&mut canvas, &icon, icon_x, 924);
            icon_x += 62;
        }
    }
    text(&mut canvas, 752., 114., "MUSIC INFO", RODIN, 28., MUTED, La);
    let title = string(&music["title"]);
    let size = (30..=66)
        .rev()
        .find(|&size| gfx.fonts.measure_text(BOLD, size as f32, title).0 <= 1020.)
        .unwrap_or(30) as f32;
    text(
        &mut canvas,
        752.,
        154.,
        &fit_text(&gfx.fonts, title, BOLD, size, 1020.),
        BOLD,
        size,
        INK,
        La,
    );
    rounded_panel_overlay(
        gfx,
        &mut canvas,
        (752, 238, 880, 264),
        13,
        rgba([0, 204, 187], 42),
        None,
    );
    text(
        &mut canvas,
        816.,
        251.,
        "TITLE",
        RODIN,
        16.,
        rgba([0, 150, 140], 255),
        Mm,
    );
    let length = length.filter(|x| *x != 0.).map_or_else(
        || "No data".to_owned(),
        |s| format!("{s:.1}秒 ({}分{:.1}秒)", (s / 60.) as i64, s % 60.),
    );
    let release = format!(
        "{} (UTC+8)",
        shanghai_time(number(&music["publishedAt"]).max(1601438400000))
    );
    for (index, (label, value)) in [
        ("LYRICIST", string(&music["lyricist"])),
        ("COMPOSER", string(&music["composer"])),
        ("ARRANGER", string(&music["arranger"])),
        ("LENGTH", &length),
        ("RELEASE", &release),
    ]
    .into_iter()
    .enumerate()
    {
        let y = 352 + 55 * index as i32;
        if index % 2 == 0 {
            rounded_panel_overlay(
                gfx,
                &mut canvas,
                (738, y - 11, 1818, y + 35),
                18,
                rgba([255; 3], 118),
                None,
            );
        }
        let value = if value.is_empty() { "-" } else { value };
        let size = (20..=32)
            .rev()
            .find(|&size| gfx.fonts.measure_text(BOLD, size as f32, value).0 <= 840.)
            .unwrap_or(20) as f32;
        text(&mut canvas, 762., y as f32, label, RODIN, 25., MUTED, La);
        text(
            &mut canvas,
            932.,
            (y - 4) as f32,
            &fit_text(&gfx.fonts, value, BOLD, size, 840.),
            BOLD,
            size,
            INK,
            La,
        );
    }
    text(&mut canvas, 752., 680., "VOCAL", RODIN, 24., MUTED, La);
    if vocals.is_empty() {
        text(
            &mut canvas,
            752.,
            740.,
            "NO VOCAL DATA",
            RODIN,
            24.,
            MUTED,
            La,
        );
    }
    for (i, vocal) in vocals.iter().take(4).enumerate() {
        let (x, y) = (752 + 542 * (i % 2) as i32, 704 + 50 * (i / 2) as i32);
        rounded_panel_overlay(
            gfx,
            &mut canvas,
            (x, y, x + 524, y + 42),
            18,
            rgba([255; 3], 176),
            Some(rgba([255; 3], 232)),
        );
        let kind = string(&vocal["musicVocalType"]);
        let (label, color) = match kind {
            "original_song" => ("ORG", rgba([88, 92, 118], 255)),
            "virtual_singer" => ("VS", rgba([88, 92, 118], 255)),
            "sekai" => ("SEK", rgba([238, 67, 102], 255)),
            "another_vocal" => ("AV", PJSK_STYLE_ACCENT),
            "instrumental" => ("INS", PJSK_STYLE_ACCENT),
            _ => ("VOC", PJSK_STYLE_ACCENT),
        };
        badge(&mut canvas, (x + 10, y + 9, 58, 24), label, 13., color);
        let caption = vocal["caption"]
            .as_str()
            .filter(|s| !s.is_empty())
            .unwrap_or(if kind.is_empty() { "VOCAL" } else { kind });
        text(
            &mut canvas,
            (x + 82) as f32,
            (y + 10) as f32,
            &fit_text(&gfx.fonts, caption, BOLD, 18., 336.),
            BOLD,
            18.,
            INK,
            La,
        );
        let mut chars: Vec<_> = rows(&vocal["characters"]).iter().collect();
        chars.sort_by_key(|row| number(&row["seq"]));
        let mut ix = x + 482;
        for row in chars.into_iter().take(5).rev() {
            let id = number(&row["characterId"]);
            let icon = if row["characterType"] == "game_character" {
                character_portrait(gfx, static_dir, id)
                    .map(|portrait| circle_avatar(&portrait, 34, WHITE))
            } else {
                let color = rgba([88, 92, 118], 255);
                let mut icon = circle_avatar(&RgbaImage::new(0, 0), 34, color);
                let name = outside
                    .and_then(|data| data.get(&id.to_string()))
                    .map(|row| string(&row["name"]))
                    .unwrap_or_default();
                let fallback = id.to_string();
                let name = if name.is_empty() { &fallback } else { name };
                let initial: String = name
                    .trim()
                    .chars()
                    .next()
                    .unwrap_or('?')
                    .to_uppercase()
                    .collect();
                text(&mut icon, 17., 17., &initial, BOLD, 17., color, Mm);
                Some(icon)
            };
            if let Some(icon) = icon {
                paste_rgba(&mut canvas, &icon, ix, y + 4);
                ix -= 28;
            }
        }
    }
    if vocals.len() > 4 {
        text(
            &mut canvas,
            1818.,
            782.,
            &format!("+{} more", vocals.len() - 4),
            RODIN,
            16.,
            MUTED,
            Ra,
        );
    }
    text(&mut canvas, 752., 864., "CHARTS", RODIN, 24., MUTED, La);
    for (i, (chart, color)) in charts
        .iter()
        .zip([
            [102, 221, 17],
            [51, 187, 238],
            [254, 170, 0],
            [238, 67, 102],
            [187, 51, 238],
        ])
        .enumerate()
    {
        let x = 796 + 198 * i as i32;
        let color = rgba(color, 255);
        badge(
            &mut canvas,
            (x, 890, 150, 38),
            &DIFFICULTIES[i].to_uppercase(),
            18.,
            color,
        );
        let level = number(&chart["playLevel"]);
        text(
            &mut canvas,
            (x + 75) as f32,
            962.,
            &level.to_string(),
            BOLD,
            38.,
            color,
            Mm,
        );
        text(
            &mut canvas,
            (x + 75) as f32,
            994.,
            &format!("{} NOTES", number(&chart["totalNoteCount"])),
            RODIN,
            16.,
            INK,
            Mm,
        );
        if adjusted && !leak && i >= 3 {
            let value = chart["playLevelAdjust"].as_f64().map_or_else(
                || format!("{level}.?"),
                |adjust| format!("{:.1}", level as f64 + adjust),
            );
            text(
                &mut canvas,
                (x + 75) as f32,
                1014.,
                &format!("CONST {value}"),
                RODIN,
                14.,
                MUTED,
                Mm,
            );
        }
    }
    text(
        &mut canvas,
        1888.,
        1046.,
        PJSK_WATERMARK_TEXT,
        MEDIUM,
        22.,
        PJSK_STYLE_ACCENT,
        Ra,
    );
    canvas
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parses_id_and_invalidates_cache_at_release() {
        assert_eq!(payload_ids(&json!({"music_id":"74"})).unwrap(), (74, 0));
        let t = |seconds| SystemTime::UNIX_EPOCH + std::time::Duration::from_secs(seconds);
        assert!(fresh(t(2), t(1), 3000, 2999));
        assert!(!fresh(t(2), t(1), 3000, 3000));
        assert!(!fresh(t(2), t(2), 1000, 3000));
    }

    #[test]
    fn charts_match_difficulty_names() {
        let data = json!([{"musicId":74,"musicDifficulty":"master","playLevel":31},{"musicId":74,"musicDifficulty":"easy","playLevel":7}]);
        let charts = difficulty_rows(&data, 74);
        assert_eq!(charts[0]["playLevel"], 7);
        assert_eq!(charts[4]["playLevel"], 31);
    }

    #[tokio::test]
    async fn renders_png_and_reuses_disk_cache() {
        let fixture = RenderFixture::new();
        fixture.write(
            "ondemand/jp/musics.json",
            r#"[{"id":74,"title":"Test Song","publishedAt":1601438400000}]"#,
        );
        fixture.write(
            "ondemand/jp/musicDifficulties.json",
            r#"[{"musicId":74,"musicDifficulty":"master","playLevel":31}]"#,
        );
        fixture.write(
            "ondemand/jp/musicVocals.json",
            r#"[{"musicId":74,"caption":"Test Vocal","musicVocalType":"sekai","assetbundleName":"test_vocal"}]"#,
        );
        fixture.png(
            "ondemand/jp/startapp/thumbnail/music_jacket/jacket_s_074.png",
            (32, 32),
        );
        // MPEG-1 Layer III frame header for 128 kbps at 44.1 kHz.
        let mut frame = [0u8; 417];
        frame[..4].copy_from_slice(&[0xff, 0xfb, 0x90, 0x00]);
        fixture.write(
            "ondemand/jp/ondemand/music/long/test_vocal/test_vocal.mp3",
            frame.repeat(40),
        );
        let ctx = fixture.context();
        let vocal = json!({"assetbundleName":"test_vocal"});
        let seconds = duration(&ctx, Some(&vocal), 0.25, 0).await.unwrap();
        assert!((seconds - (40.0 * 1152.0 / 44100.0 - 0.25)).abs() < 0.001);
        let payload = json!({"music_id":74});
        let first = Song.render(&ctx, payload.clone()).await.unwrap();
        let cache = ctx
            .app
            .paths
            .ondemand_dir
            .join("jp/pics/pjskinfo/pjskinfo_v7_74.png");
        assert!(std::fs::read(&cache).unwrap() == first.images[0]);
        let image = image::load_from_memory_with_format(&first.images[0], image::ImageFormat::Png)
            .unwrap()
            .into_rgb8();
        assert_eq!(image.dimensions(), (1920, 1080));
        let mtime = modified(&cache);
        assert!(Song.render(&ctx, payload.clone()).await.unwrap().images == first.images);
        assert_eq!(modified(&cache), mtime);
        let realtime = fixture.write(
            "ondemand/jp/realtime/musicDifficulties.json",
            r#"[{"musicId":74,"musicDifficulty":"master","playLevel":32}]"#,
        );
        let old_cache_time = mtime.unwrap();
        std::fs::File::options()
            .write(true)
            .open(&realtime)
            .unwrap()
            .set_modified(old_cache_time)
            .unwrap();
        let refreshed = Song.render(&ctx, payload).await.unwrap();
        assert!(refreshed.images == first.images);
        assert!(modified(&cache).unwrap() > old_cache_time);
    }
}
