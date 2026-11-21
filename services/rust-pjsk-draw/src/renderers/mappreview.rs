use std::{
    fs,
    io::{ErrorKind, Write},
    path::{Path, PathBuf},
    sync::{Arc, LazyLock, Mutex, Weak},
    time::Duration,
};

use crate::chart::{
    ChartMeta, MusicMeta, Score, SusCanvas, SusWithSkillCanvas, legacy_sus_svg, rasterize,
};
use crate::core::{RenderOutput, RenderRequestContext, Renderer, Result};
use crate::gfx::RenderContextGfxExt;
use anyhow::{Context, Result as AnyResult};
use async_trait::async_trait;
use image::{
    DynamicImage, ImageFormat, Rgb, RgbImage, RgbaImage, codecs::jpeg::JpegEncoder, imageops,
};
use reqwest::Client;
use serde_json::{Value, json};

use crate::renderers::common::{number, render_error, rows, string};

#[derive(Clone, Copy)]
pub(crate) enum MapPreview {
    Map,
    Skill,
}

#[derive(Clone, Copy)]
enum Source {
    Moe,
    Viewer,
    Sdvx,
}

impl Source {
    fn name(self) -> &'static str {
        match self {
            Self::Moe => "moe",
            Self::Viewer => "sekai_viewer",
            Self::Sdvx => "sdvx_in_charts",
        }
    }

    fn directory(self) -> &'static str {
        match self {
            Self::Moe => "moe",
            Self::Viewer => "SekaiViewer",
            Self::Sdvx => "sdvxInCharts",
        }
    }

    fn path(self, ctx: &RenderRequestContext, id: i64, file: &str, region: i32) -> PathBuf {
        ctx.app
            .paths
            .ondemand_dir
            .join(ctx.app.server_name(region))
            .join(format!("charts/{}/{id}/{file}", self.directory()))
    }
}

#[async_trait]
impl Renderer for MapPreview {
    fn name(&self) -> &'static str {
        match self {
            Self::Map => "map_preview",
            Self::Skill => "skill_preview",
        }
    }

    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput> {
        let id = number(&payload["music_id"]);
        let difficulty = string(&payload["difficulty"]);
        let region = number(&payload["pjsk_type"]) as i32;
        let sources: &[Source] = match self {
            Self::Skill => &[Source::Moe],
            Self::Map => match payload.get("get_type").map(number).unwrap_or(1) {
                1 => &[Source::Moe, Source::Viewer],
                2 => &[Source::Viewer, Source::Moe],
                3 => &[Source::Sdvx, Source::Viewer, Source::Moe],
                _ => &[],
            },
        };
        for &source in sources {
            let path = match source {
                Source::Moe => {
                    ensure_moe_chart(ctx, id, difficulty, matches!(self, Self::Skill), region)
                        .await?
                }
                _ => match external_chart(ctx, id, difficulty, region, source).await {
                    Ok(path) => path,
                    Err(error) => {
                        tracing::debug!(id, difficulty, source = source.name(), %error, "Chart source unavailable");
                        None
                    }
                },
            };
            if let Some(path) = path {
                match tokio::fs::read(path).await {
                    Ok(bytes) => return Ok(output(vec![bytes], source.name())),
                    Err(error) if error.kind() == ErrorKind::NotFound => {}
                    Err(error) => return Err(error.into()),
                }
            }
        }
        Ok(output(vec![], ""))
    }
}

fn output(images: Vec<Vec<u8>>, source: &str) -> RenderOutput {
    RenderOutput::with_meta(
        images,
        [("source".into(), json!(source))].into_iter().collect(),
    )
}

fn note_size(difficulty: &str) -> Option<f64> {
    Some(match difficulty {
        "easy" => 2.,
        "normal" => 1.5,
        "hard" => 1.25,
        "expert" => 1.,
        "master" | "append" => 0.875,
        _ => return None,
    })
}

type CacheLocks = std::collections::HashMap<PathBuf, Weak<tokio::sync::Mutex<()>>>;
static CACHE_LOCKS: LazyLock<Mutex<CacheLocks>> = LazyLock::new(Mutex::default);

async fn cache_lock(path: &Path) -> tokio::sync::OwnedMutexGuard<()> {
    let lock = {
        let mut locks = CACHE_LOCKS.lock().unwrap();
        locks.retain(|_, lock| lock.strong_count() > 0);
        if let Some(lock) = locks.get(path).and_then(Weak::upgrade) {
            lock
        } else {
            let lock = Arc::new(tokio::sync::Mutex::new(()));
            locks.insert(path.to_owned(), Arc::downgrade(&lock));
            lock
        }
    };
    lock.lock_owned().await
}

pub(crate) async fn ensure_moe_chart(
    ctx: &RenderRequestContext,
    id: i64,
    difficulty: &str,
    with_skill: bool,
    pjsk_type: i32,
) -> Result<Option<PathBuf>> {
    let Some(size) = note_size(difficulty) else {
        return Ok(None);
    };
    let suffix = if with_skill { "_skill" } else { "" };
    let path = Source::Moe.path(ctx, id, &format!("{difficulty}{suffix}.jpg"), pjsk_type);
    if path.is_file() {
        return Ok(Some(path));
    }
    let guard = cache_lock(&path).await;
    if path.is_file() {
        return Ok(Some(path));
    }

    match generate_moe(
        ctx,
        id,
        difficulty,
        with_skill,
        pjsk_type,
        size,
        path.clone(),
        guard,
    )
    .await
    {
        Ok(()) => Ok(Some(path)),
        Err(error) => {
            tracing::debug!(id, difficulty, %error, "Moe chart unavailable");
            Ok(None)
        }
    }
}

/// Produces the deprecated local PNG preview.
pub async fn sus2img(
    ctx: &RenderRequestContext,
    id: i64,
    difficulty: &str,
    region: i32,
) -> Result<Option<PathBuf>> {
    if note_size(difficulty).is_none() {
        return Ok(None);
    }
    let path = ctx
        .app
        .paths
        .ondemand_dir
        .join(ctx.app.server_name(region))
        .join(format!("charts/sus/{id}/{difficulty}.png"));
    let guard = cache_lock(&path).await;
    if path.is_file() {
        return Ok(Some(path));
    }
    let Some(sus) = ctx
        .app
        .ensure_asset_path(
            &format!("startapp/music/music_score/{id:04}_01"),
            difficulty,
            region,
            true,
        )
        .await
    else {
        return Ok(None);
    };
    let gfx = ctx.gfx()?;
    let notes = ctx.app.paths.static_dir.join("pics/notes");
    let output = path.clone();
    tokio::task::spawn_blocking(move || -> AnyResult<()> {
        let _guard = guard;
        let svg = legacy_sus_svg(&fs::read_to_string(sus)?, &notes)?;
        let image = rasterize(&svg, &gfx.fonts, 0.25)?;
        write_cache(&output, |file| {
            image.write_to(file, ImageFormat::Png)?;
            Ok(())
        })
    })
    .await
    .map_err(render_error)?
    .map_err(render_error)?;
    Ok(Some(path))
}

async fn generate_moe(
    ctx: &RenderRequestContext,
    id: i64,
    difficulty: &str,
    skill: bool,
    region: i32,
    note_size: f64,
    path: PathBuf,
    guard: tokio::sync::OwnedMutexGuard<()>,
) -> AnyResult<()> {
    let app = &ctx.app;
    let sus = app
        .ensure_asset_path(
            &format!("startapp/music/music_score/{id:04}_01"),
            difficulty,
            region,
            true,
        )
        .await
        .context("missing SUS asset")?;
    let master_data = app.master_data.clone();
    let diff = difficulty.to_owned();
    let (meta, bundle) = tokio::task::spawn_blocking(move || -> AnyResult<_> {
        let musics = master_data.load_master_data("musics.json", region)?;
        let music = rows(&musics)
            .iter()
            .find(|item| number(&item["id"]) == id)
            .context("missing music metadata")?;
        let difficulties = master_data.load_master_data("musicDifficulties.json", region)?;
        let level = rows(&difficulties)
            .iter()
            .find(|item| number(&item["musicId"]) == id && item["musicDifficulty"] == diff)
            .and_then(|item| u32::try_from(number(&item["playLevel"])).ok())
            .filter(|&level| level != 0);
        let (composer, arranger) = (string(&music["composer"]), string(&music["arranger"]));
        let artist = if arranger.contains(composer) || composer == "-" {
            arranger.to_owned()
        } else if composer.contains(arranger) || arranger == "-" {
            composer.to_owned()
        } else {
            format!("{composer} / {arranger}")
        };
        let bundle = music["assetbundleName"]
            .as_str()
            .filter(|name| !name.is_empty())
            .context("missing jacket bundle")?
            .to_owned();
        Ok((
            ChartMeta {
                title: string(&music["title"]).to_owned(),
                artist,
                difficulty: diff,
                play_level: level,
            },
            bundle,
        ))
    })
    .await??;

    let jacket = app
        .ensure_asset_path(
            &format!("startapp/music/jacket/{bundle}"),
            &format!("{bundle}.png"),
            region,
            true,
        )
        .await
        .context("missing jacket asset")?;
    let note_dir = app.paths.ondemand_dir.join("pics/notes");
    let note_dir = if note_dir.is_dir() {
        note_dir
    } else {
        app.paths.static_dir.join("pics/notes")
    };
    let gfx = ctx.gfx()?;
    let music_meta_path = app.paths.ondemand_dir.join("realtime/music_metas.json");
    tokio::task::spawn_blocking(move || {
        let _guard = guard;
        let score = Score::parse(&fs::read_to_string(sus)?)?;
        let canvas = SusCanvas {
            score: &score,
            meta: &meta,
            notes_dir: &note_dir,
            jacket: Some(&jacket),
            note_size,
            words: &[],
        };
        let svg = if skill {
            let music_meta = load_music_meta(&music_meta_path, id, &meta.difficulty)?;
            SusWithSkillCanvas {
                canvas,
                music_meta: music_meta.as_ref(),
                display_skill_extra: true,
            }
            .to_svg()?
        } else {
            canvas.to_svg()?
        };
        let image = rasterize(&svg, &gfx.fonts, 1.3)?;
        write_cache(&path, |file| {
            JpegEncoder::new_with_quality(file, 60).encode_image(&image)?;
            Ok(())
        })
    })
    .await?
}

fn load_music_meta(path: &Path, id: i64, difficulty: &str) -> AnyResult<Option<MusicMeta>> {
    let data = match fs::File::open(path) {
        Ok(file) => serde_json::from_reader::<_, Value>(std::io::BufReader::new(file))?,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    rows(&data)
        .iter()
        .find(|item| number(&item["music_id"]) == id && item["difficulty"] == difficulty)
        .map(|item| serde_json::from_value(item.clone()))
        .transpose()
        .map_err(Into::into)
}

fn write_cache(path: &Path, write: impl FnOnce(&mut fs::File) -> AnyResult<()>) -> AnyResult<()> {
    let parent = path.parent().context("chart cache path has no parent")?;
    fs::create_dir_all(parent)?;
    let mut temp = tempfile::NamedTempFile::new_in(parent)?;
    write(temp.as_file_mut())?;
    temp.flush()?;
    match temp.persist_noclobber(path) {
        Ok(_) => Ok(()),
        Err(error) if error.error.kind() == ErrorKind::AlreadyExists && path.is_file() => Ok(()),
        Err(error) => Err(error.into()),
    }
}

static HTTP: LazyLock<Client> = LazyLock::new(|| {
    Client::builder()
        .timeout(Duration::from_secs(10))
        .build()
        .expect("chart HTTP client")
});

async fn download(url: &str) -> AnyResult<Vec<u8>> {
    let response = match HTTP.get(url).send().await {
        Ok(response) => response,
        Err(_) => HTTP.get(url).send().await?,
    };
    Ok(response.error_for_status()?.bytes().await?.to_vec())
}

async fn external_chart(
    ctx: &RenderRequestContext,
    id: i64,
    difficulty: &str,
    region: i32,
    source: Source,
) -> AnyResult<Option<PathBuf>> {
    if note_size(difficulty).is_none()
        || matches!(source, Source::Sdvx) && !matches!(difficulty, "master" | "expert" | "append")
    {
        return Ok(None);
    }
    let path = source.path(ctx, id, &format!("{difficulty}.png"), region);
    if path.is_file() {
        return Ok(Some(path));
    }
    let guard = cache_lock(&path).await;
    if path.is_file() {
        return Ok(Some(path));
    }
    let (layers, resize, white) = match source {
        Source::Viewer => {
            let base = ctx.app.config.chart_preview_base_url.trim_end_matches('/');
            if base.is_empty() {
                return Ok(None);
            }
            let base = format!("{base}/{id:04}/{difficulty}");
            let png = download(&format!("{base}.png")).await?;
            let size = if matches!(difficulty, "master" | "append") {
                let svg =
                    String::from_utf8_lossy(&download(&format!("{base}.svg")).await?).into_owned();
                let first_missing = (1..)
                    .find(|bar| !svg.contains(&format!("{bar:03}</text>")))
                    .unwrap_or(1_i32);
                Some(((160 * ((first_missing - 2) / 4) + 32) as u32, 1300))
            } else {
                None
            };
            (vec![png], size, true)
        }
        Source::Sdvx => {
            let data = ctx.app.master_data.clone();
            let ordinal = tokio::task::spawn_blocking(move || -> Result<_> {
                let musics = data.load_master_data("musics.json", region)?;
                let mut order: Vec<_> = rows(&musics).iter().collect();
                order.sort_by_key(|music| number(&music["publishedAt"]));
                Ok(order
                    .iter()
                    .position(|music| number(&music["id"]) == id)
                    .map_or(0, |index| index + 1))
            })
            .await??;
            let suffix = match difficulty {
                "append" => "apd",
                "master" => "mst",
                _ => "exp",
            };
            let data = download(&format!(
                "https://sdvx.in/prsk/obj/data{ordinal:03}{suffix}.png"
            ))
            .await?;
            let bg_url = format!("https://sdvx.in/prsk/bg/{ordinal:03}bg.png");
            let bar_url = format!("https://sdvx.in/prsk/bg/{ordinal:03}bar.png");
            let (bg, bar) = tokio::try_join!(download(&bg_url), download(&bar_url))?;
            (vec![bg, data, bar], None, false)
        }
        Source::Moe => unreachable!(),
    };

    let output = path.clone();
    tokio::task::spawn_blocking(move || -> AnyResult<()> {
        let _guard = guard;
        let mut images = layers
            .iter()
            .map(|bytes| image::load_from_memory(bytes).map(DynamicImage::into_rgba8));
        let mut chart = images.next().context("missing chart image")??;
        for layer in images {
            paste_rgba(&mut chart, &layer?);
        }
        let mut flat = RgbImage::new(chart.width(), chart.height());
        let background = if white { 255 } else { 0 };
        for (pixel, source) in flat.pixels_mut().zip(chart.pixels()) {
            *pixel = Rgb(std::array::from_fn(|i| {
                blend(source[i], background, source[3])
            }));
        }
        if let Some((width, height)) = resize {
            flat = imageops::resize(&flat, width, height, imageops::FilterType::CatmullRom);
        }
        write_cache(&output, |file| {
            flat.write_to(file, ImageFormat::Png)?;
            Ok(())
        })
    })
    .await
    .map_err(render_error)??;
    Ok(Some(path))
}

fn blend(source: u8, dest: u8, alpha: u8) -> u8 {
    let alpha = u16::from(alpha);
    ((u16::from(source) * alpha + u16::from(dest) * (255 - alpha) + 127) / 255) as u8
}

fn paste_rgba(dest: &mut RgbaImage, source: &RgbaImage) {
    for (x, y, pixel) in source.enumerate_pixels() {
        if let Some(dest) = dest.get_pixel_mut_checked(x, y) {
            for i in 0..4 {
                dest[i] = blend(pixel[i], dest[i], pixel[3]);
            }
        }
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::renderers::common::RenderFixture;

    pub(crate) fn moe_fixture(fixture: &RenderFixture) -> PathBuf {
        fixture.write("ondemand/tw/musics.json", br#"[{"id":42,"title":"Offline chart","composer":"A","arranger":"A","assetbundleName":"jacket_s_042"}]"#);
        fixture.write(
            "ondemand/tw/musicDifficulties.json",
            br#"[{"musicId":42,"musicDifficulty":"master","playLevel":30}]"#,
        );
        fixture.png(
            "ondemand/tw/startapp/music/jacket/jacket_s_042/jacket_s_042.png",
            (16, 16),
        );
        for file in [
            "notes_normal_left.png",
            "notes_normal_middle.png",
            "notes_normal_right.png",
            "notes_normal.png",
        ] {
            fixture.png(&format!("static/pics/notes/{file}"), (8, 16));
        }
        fixture.write("ondemand/realtime/music_metas.json", br#"[{"music_id":42,"difficulty":"master","skill_score_solo":[0.1],"skill_score_multi":[0.12]}]"#);
        fixture.write(
            "ondemand/tw/startapp/music/music_score/0042_01/master",
            "#BPM01: 120\n#00008: 01\n#MEASUREHS 00\n#00012:12\n#00010:41\n#00516:12\n#00918:12\n",
        )
    }

    #[tokio::test]
    async fn generates_moe_variants_and_keeps_the_disk_cache() {
        let fixture = RenderFixture::new();
        let sus = moe_fixture(&fixture);
        let ctx = fixture.context();
        let payload = json!({"music_id":"42","pjsk_type":1,"difficulty":"master"});
        let (map, map_again) = tokio::join!(
            MapPreview::Map.render(&ctx, payload.clone()),
            MapPreview::Map.render(&ctx, payload.clone())
        );
        let map = map.unwrap();
        assert_eq!(map.meta["source"], "moe");
        assert_eq!(map.images, map_again.unwrap().images);
        let skill = MapPreview::Skill
            .render(&ctx, payload.clone())
            .await
            .unwrap();
        assert_eq!(skill.meta["source"], "moe");
        assert_ne!(map.images, skill.images);
        for (file, image) in [
            ("master.jpg", &map.images[0]),
            ("master_skill.jpg", &skill.images[0]),
        ] {
            assert_eq!(crate::core::guess_image_media_type(image), "image/jpeg");
            let path = Source::Moe.path(&ctx, 42, file, 1);
            assert_eq!(image::image_dimensions(&path).unwrap(), (936, 2932));
            assert_eq!(fs::read(path).unwrap(), *image);
        }

        let legacy = sus2img(&ctx, 42, "master", 1).await.unwrap().unwrap();
        assert_eq!(image::image_dimensions(&legacy).unwrap(), (423, 2046));
        assert!(legacy.ends_with("charts/sus/42/master.png"));
        fs::remove_file(sus).unwrap();
        let cached = MapPreview::Map.render(&ctx, payload).await.unwrap();
        assert_eq!(cached.images, map.images);
    }

    #[tokio::test]
    async fn follows_python_source_order_and_returns_empty_metadata_on_miss() {
        let fixture = RenderFixture::new();
        let ctx = fixture.context();
        fixture.png("ondemand/jp/charts/SekaiViewer/42/master.png", (2, 2));
        fixture.png("ondemand/jp/charts/sdvxInCharts/42/master.png", (3, 3));
        fixture.write("ondemand/jp/charts/moe/42/master.jpg", b"cached moe");
        for (get_type, expected_source) in [(1, "moe"), (2, "sekai_viewer"), (3, "sdvx_in_charts")]
        {
            let payload = json!({"music_id":42,"difficulty":"master","get_type":get_type});
            let result = MapPreview::Map.render(&ctx, payload).await.unwrap();
            assert_eq!(result.meta["source"], expected_source);
            assert_eq!(result.images.len(), 1);
        }

        for renderer in [MapPreview::Map, MapPreview::Skill] {
            let result = renderer
                .render(&ctx, json!({"music_id":99,"difficulty":"expert"}))
                .await
                .unwrap();
            assert_eq!(result.meta["source"], "");
            assert!(result.images.is_empty());
        }
    }
}
