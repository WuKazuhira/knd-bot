use std::{path::Path, sync::Arc};

use crate::components::{
    card::{CardTileOptions, cardlarge, cardthumnail, render_card_thumbnail_tile},
    ui::{get_pjsk_asset_resized, get_pjsk_music_jacket_cached},
};
use crate::core::{DrawError, RenderOutput, RenderRequestContext, Renderer, Result};
use crate::gfx::{GfxContext, RenderContextGfxExt, TextAnchor, paste_rgba, resize_lanczos};
use async_trait::async_trait;
use image::{Rgba, RgbaImage};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::renderers::common::*;

pub(crate) struct CardInfo;

const STYLE: Rgba<u8> = Rgba([220, 100, 150, 255]);
const INK: Rgba<u8> = Rgba([0, 0, 0, 255]);
const WHITE: Rgba<u8> = Rgba([255; 4]);
const BORDER: Rgba<u8> = Rgba([240, 201, 220, 255]);
const COLUMN: u32 = 960;
const LEFT: u32 = 880;
const RIGHT: u32 = 860;
const GAP: u32 = 60;

#[derive(Default, Deserialize)]
#[serde(default, rename_all = "camelCase")]
struct CardInfoView {
    #[serde(rename = "pjsk_type")]
    pjsk_type: i32,
    id: Value,
    unit: Value,
    card_rarity_type: Value,
    attr: Value,
    is_limited: Value,
    card_parameters: Value,
    release_at: Value,
    chara_name: Value,
    prefix: Value,
    gacha_phrase: Value,
    card_skill_name: Value,
    card_skill_des: Value,
    assets: Value,
    event: Value,
    music: Value,
    gacha: Value,
}

impl CardInfoView {
    fn trained(&self) -> bool {
        matches!(string(&self.card_rarity_type), "rarity_3" | "rarity_4")
    }

    fn limited(&self) -> bool {
        self.is_limited
            .as_bool()
            .unwrap_or_else(|| number(&self.is_limited) != 0)
    }

    fn card(&self) -> Value {
        json!([{"id":number(&self.id),"cardRarityType":self.card_rarity_type,
            "assetbundleName":self.assets["card"],"attr":self.attr}])
    }
}

#[derive(Clone, Copy)]
enum Align {
    Start,
    Center,
    End,
}

impl Align {
    fn offset(self, length: u32, used: u32) -> i32 {
        let free = length.saturating_sub(used) as i32;
        match self {
            Self::Start => 0,
            Self::Center => free / 2,
            Self::End => free,
        }
    }
}

fn wrap_lines(text: &str, max_width: u32, measure: impl Fn(&str) -> f32) -> Vec<&str> {
    let mut lines = Vec::new();
    for line in text.split('\n') {
        let mut start = 0;
        if max_width > 0 {
            for (index, ch) in line.char_indices() {
                if index > start && measure(&line[start..index + ch.len_utf8()]) > max_width as f32
                {
                    lines.push(&line[start..index]);
                    start = index;
                }
            }
        }
        lines.push(&line[start..]);
    }
    lines
}

fn t2i(
    gfx: &GfxContext,
    text: &str,
    size: u32,
    color: Rgba<u8>,
    width: u32,
    align: Align,
) -> RgbaImage {
    let measure = |line: &str| gfx.fonts.measure_text(MEDIUM, size as f32, line).0;
    let lines = wrap_lines(text, width, measure);
    let metrics: Vec<_> = lines
        .iter()
        .map(|line| {
            let (w, h) = gfx.fonts.measure_text(MEDIUM, size as f32, line);
            (w.ceil() as u32, h.ceil() as u32)
        })
        .collect();
    let width = metrics.iter().map(|&(w, _)| w).max().unwrap_or(0);
    let gap = size / 4;
    let height = metrics.iter().map(|&(_, h)| h).sum::<u32>() + gap * (lines.len() as u32 - 1);
    let mut image = RgbaImage::new(width.max(1), height.max(1));
    let mut y = 0;
    for (line, (w, h)) in lines.into_iter().zip(metrics) {
        gfx.fonts.draw_text(
            &mut image,
            (align.offset(width, w) as f32, y as f32),
            line,
            MEDIUM,
            size as f32,
            color,
            TextAnchor::Lt,
            None,
        );
        y += h + gap;
    }
    image
}

fn text(gfx: &GfxContext, value: &str, size: u32, width: u32) -> RgbaImage {
    t2i(gfx, value, size, INK, width, Align::Start)
}

fn stack(images: &[RgbaImage], gap: u32, align: Align) -> RgbaImage {
    let w = images.iter().map(RgbaImage::width).max().unwrap_or(1);
    let h = images.iter().map(RgbaImage::height).sum::<u32>()
        + gap * images.len().saturating_sub(1) as u32;
    let mut canvas = RgbaImage::new(w, h.max(1));
    let mut y = 0;
    for image in images {
        paste_rgba(&mut canvas, image, align.offset(w, image.width()), y as i32);
        y += image.height() + gap;
    }
    canvas
}

fn row(images: &[RgbaImage], length: u32, gap: u32) -> RgbaImage {
    let sum = images.iter().map(RgbaImage::width).sum::<u32>();
    let spaces = images.len().saturating_sub(1).max(1) as u32;
    let w = if length >= sum {
        length
    } else {
        sum + gap * spaces
    };
    let h = images.iter().map(RgbaImage::height).max().unwrap_or(1);
    let space = if gap == 0 {
        (w - sum) / spaces
    } else {
        gap.min((w - sum) / spaces)
    };
    let mut canvas = RgbaImage::new(w.max(1), h);
    let mut x = 0;
    for image in images {
        paste_rgba(
            &mut canvas,
            image,
            x as i32,
            Align::Center.offset(h, image.height()),
        );
        x += image.width() + space;
    }
    canvas
}

fn panel(
    gfx: &GfxContext,
    image: &RgbaImage,
    (horizontal, top, bottom): (u32, u32, u32),
    border: Rgba<u8>,
) -> RgbaImage {
    let (w, h) = (
        image.width() + 2 * (horizontal + 3),
        image.height() + top + bottom + 6,
    );
    let radius = w.min(h) / 36;
    let mut canvas = RgbaImage::new(w, h);
    rounded_panel_overlay(
        gfx,
        &mut canvas,
        (0, 0, w as i32 - 1, h as i32 - 1),
        radius,
        border,
        None,
    );
    rounded_panel_overlay(
        gfx,
        &mut canvas,
        (3, 3, w as i32 - 4, h as i32 - 4),
        radius.saturating_sub(3),
        WHITE,
        None,
    );
    paste_rgba(
        &mut canvas,
        image,
        (horizontal + 3) as i32,
        (top + 3) as i32,
    );
    canvas
}

fn heading(gfx: &GfxContext, label: &str) -> RgbaImage {
    let mut image = RgbaImage::new(COLUMN, 70);
    rounded_panel_overlay(
        gfx,
        &mut image,
        (0, 0, COLUMN as i32 - 1, 69),
        25,
        STYLE,
        None,
    );
    gfx.fonts.draw_text(
        &mut image,
        (COLUMN as f32 / 2., 35.),
        label,
        MEDIUM,
        50.,
        WHITE,
        TextAnchor::Mm,
        None,
    );
    image
}

fn detail(
    gfx: &GfxContext,
    view: &CardInfoView,
    server: &str,
    thumbs: Vec<RgbaImage>,
    costumes: Vec<(&'static str, RgbaImage)>,
) -> RgbaImage {
    let field = |label: &str, value: RgbaImage| row(&[text(gfx, label, 40, 0), value], RIGHT, 0);
    let simple = |label: &str, value: &str| field(label, text(gfx, value, 40, 0));
    let translated = |data: &Value, width| {
        stack(
            &data
                .as_object()
                .into_iter()
                .flatten()
                .map(|(language, value)| {
                    t2i(
                        gfx,
                        &format!("{}\n({language})", string(value)),
                        40,
                        INK,
                        width,
                        Align::End,
                    )
                })
                .collect::<Vec<_>>(),
            10,
            Align::End,
        )
    };
    let params = &view.card_parameters;
    let power: i64 = params
        .as_object()
        .into_iter()
        .flatten()
        .map(|(_, v)| number(v))
        .fold(0, i64::saturating_add);
    let mut fields = vec![
        simple("综合力", &power.to_string()),
        stack(
            &[
                simple("演奏", &number(&params["param1"]).to_string()),
                simple("技巧", &number(&params["param2"]).to_string()),
                simple("耐力", &number(&params["param3"]).to_string()),
            ],
            25,
            Align::Center,
        ),
        simple("类型", if view.limited() { "限定" } else { "普通" }),
        field("技能名", translated(&view.card_skill_name, 586)),
        field("技能效果", translated(&view.card_skill_des, 650)),
    ];
    if view
        .gacha_phrase
        .as_object()
        .is_some_and(|data| !data.is_empty())
    {
        fields.push(field("招募语", translated(&view.gacha_phrase, 586)));
    }
    fields.extend([
        simple(
            "发布时间",
            &format!("{}({server})", string(&view.release_at)),
        ),
        field("缩略图", row(&thumbs, 0, 30)),
    ]);
    if !costumes.is_empty() {
        let tiles: Vec<_> = costumes
            .into_iter()
            .map(|(label, image)| stack(&[image, text(gfx, label, 40, 0)], 10, Align::Center))
            .collect();
        let rows: Vec<_> = tiles.chunks(2).map(|pair| row(pair, 0, 30)).collect();
        fields.push(field("衣装缩略图", stack(&rows, 30, Align::Center)));
    }
    fields.push(simple("ID", &number(&view.id).to_string()));
    let mut detail = stack(&fields, 46, Align::Center);
    let mut y = 0;
    let width = detail.width() as i32;
    for field in fields.iter().take(fields.len() - 1) {
        y += field.height() as i32;
        rounded_panel_overlay(
            gfx,
            &mut detail,
            (0, y + 22, width - 1, y + 24),
            0,
            rgba([239, 214, 228], 255),
            None,
        );
        y += 46;
    }
    panel(gfx, &detail, (50, 65, 75), BORDER)
}

fn fit_width(image: &RgbaImage, width: u32) -> RgbaImage {
    let height = (u64::from(width) * u64::from(image.height()) / u64::from(image.width().max(1)))
        .max(1) as u32;
    resize_lanczos(image, width, height)
}

fn placeholder(gfx: &GfxContext, label: &str, (w, h): (u32, u32)) -> RgbaImage {
    let mut image = RgbaImage::new(w, h);
    gfx.fonts.draw_text(
        &mut image,
        (w as f32 / 2., h as f32 / 2.),
        &format!("{label}资源缺失"),
        MEDIUM,
        28.,
        rgba([153; 3], 255),
        TextAnchor::Mm,
        None,
    );
    image
}

fn title(gfx: &GfxContext, static_dir: &Path, view: &CardInfoView) -> RgbaImage {
    let unit = view
        .unit
        .as_str()
        .filter(|s| !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric() || c == '_'))
        .unwrap_or("none");
    let logo = gfx
        .cache
        .open_pjsk_image(static_dir.join(format!("pics/logo_{unit}.png")), None)
        .map_or_else(|_| RgbaImage::new(266, 1), |logo| fit_width(&logo, 266));
    let name = stack(
        &[
            t2i(gfx, string(&view.prefix), 40, WHITE, 693, Align::Start),
            t2i(gfx, string(&view.chara_name), 40, WHITE, 0, Align::Start),
        ],
        5,
        Align::Center,
    );
    let spacing =
        (35 + (RIGHT as i32 - logo.width() as i32 - name.width() as i32) / 2).max(0) as u32;
    let inner = row(&[logo, name], RIGHT + 40, spacing);
    let mut canvas = RgbaImage::new(inner.width() + 60, inner.height() + 40);
    let (w, h) = (canvas.width() as i32, canvas.height() as i32);
    rounded_panel_overlay(gfx, &mut canvas, (0, 0, w - 1, h - 1), 26, STYLE, None);
    paste_rgba(&mut canvas, &inner, 30, 20);
    canvas
}

#[derive(Default)]
struct SectionAssets {
    gacha: Option<RgbaImage>,
    event: Option<RgbaImage>,
    music: Option<RgbaImage>,
}

async fn section_assets(ctx: &RenderRequestContext, view: &CardInfoView) -> SectionAssets {
    let server = view.pjsk_type;
    let gacha = async {
        let id = number(&view.gacha["id"]);
        if id == 0 {
            return None;
        }
        let name = format!("banner_gacha{id}");
        get_pjsk_asset_resized(
            ctx,
            &format!("startapp/home/banner/{name}"),
            &format!("{name}.png"),
            server,
            None,
        )
        .await
        .ok()
        .flatten()
    };
    let event = async {
        if number(&view.event["id"]) == 0 {
            return None;
        }
        let asset = string(&view.event["assetbundleName"]);
        get_pjsk_asset_resized(
            ctx,
            &format!("ondemand/event_story/{asset}/screen_image"),
            "banner_event_story.png",
            server,
            None,
        )
        .await
        .ok()
        .flatten()
    };
    let music = async {
        let id = number(&view.music["id"]);
        if id == 0 {
            return None;
        }
        get_pjsk_music_jacket_cached(ctx, id, server, Some((280, 280)), None, false)
            .await
            .ok()
            .flatten()
    };
    let (gacha, event, music) = tokio::join!(gacha, event, music);
    SectionAssets {
        gacha,
        event,
        music,
    }
}

fn gacha_section(gfx: &GfxContext, view: &CardInfoView, banner: Option<&RgbaImage>) -> RgbaImage {
    let gacha = &view.gacha;
    let group = number(&gacha["gachaCardRarityRateGroupId"]);
    let kind = if view.limited() && view.release_at == gacha["startAt"] && group != 3 {
        "期间限定"
    } else {
        match group {
            1 => "常规",
            3 => "fes限定",
            4 => "生日限定",
            _ => "",
        }
    };
    let name = stack(
        &[
            text(gfx, string(&gacha["name"]), 40, LEFT),
            text(gfx, &format!("{kind}  ID:{}", number(&gacha["id"])), 30, 0),
        ],
        10,
        Align::Center,
    );
    let times = row(
        &[
            text(
                gfx,
                &format!("开始时间：{}", string(&gacha["startAt"])),
                25,
                0,
            ),
            text(
                gfx,
                &format!("结束时间：{}", string(&gacha["endAt"])),
                25,
                0,
            ),
        ],
        LEFT,
        0,
    );
    let banner = banner.map_or_else(
        || placeholder(gfx, "卡池横幅", (LEFT, 248)),
        |image| fit_width(image, LEFT),
    );
    panel(
        gfx,
        &stack(&[banner, name, times], 40, Align::Center),
        (40, 30, 30),
        BORDER,
    )
}

fn event_section(
    gfx: &GfxContext,
    static_dir: &Path,
    event: &Value,
    banner: Option<&RgbaImage>,
) -> RgbaImage {
    let kind = match string(&event["eventType"]) {
        "marathon" => "马拉松(累积点数)",
        "cheerful_carnival" => "欢乐嘉年华(5v5)",
        _ => "",
    };
    let name = stack(
        &[
            text(gfx, string(&event["name"]), 40, LEFT),
            text(gfx, &format!("{kind}  ID:{}", number(&event["id"])), 30, 0),
        ],
        10,
        Align::Center,
    );
    let times = stack(
        &[
            text(
                gfx,
                &format!("开始时间：{}", string(&event["startAt"])),
                30,
                0,
            ),
            text(
                gfx,
                &format!("结束时间：{}", string(&event["aggregateAt"])),
                30,
                0,
            ),
        ],
        40,
        Align::Center,
    );
    let characters: Vec<_> = rows(&event["bonusechara"])
        .iter()
        .filter_map(|item| {
            let asset = string(&item["asset"]);
            if asset.is_empty()
                || !Path::new(asset)
                    .components()
                    .all(|part| matches!(part, std::path::Component::Normal(_)))
            {
                return None;
            }
            let portrait = gfx
                .cache
                .open_pjsk_image(static_dir.join("chara").join(asset), None)
                .ok()?;
            let color = match string(&item["unit"]) {
                "light_sound" => [68, 85, 221],
                "idol" => [136, 221, 68],
                "street" => [238, 17, 102],
                "theme_park" => [255, 153, 0],
                "school_refusal" => [136, 68, 153],
                _ => [0; 3],
            };
            Some(circle_avatar(&portrait, 65, rgba(color, 255)))
        })
        .collect();
    let attr = string(&event["bonuseattr"]);
    let attr_icon = if matches!(attr, "cool" | "cute" | "happy" | "mysterious" | "pure") {
        gfx.cache
            .open_pjsk_image(
                static_dir.join(format!("chara/icon_attribute_{attr}.png")),
                Some((60, 60)),
            )
            .ok()
    } else {
        None
    };
    let bonuses = stack(
        &[
            attr_icon.map_or_else(|| RgbaImage::new(60, 60), |image| (*image).clone()),
            row(&characters, 0, 10),
        ],
        10,
        Align::End,
    );
    let metadata = row(&[times, bonuses], LEFT, 60);
    let banner = banner.map_or_else(
        || placeholder(gfx, "活动横幅", (LEFT, 248)),
        |image| fit_width(image, LEFT),
    );
    panel(
        gfx,
        &stack(&[banner, name, metadata], 40, Align::Center),
        (40, 30, 30),
        rgba([161, 157, 158], 255),
    )
}

fn music_section(gfx: &GfxContext, music: &Value, jacket: Option<RgbaImage>) -> RgbaImage {
    let jacket = jacket.unwrap_or_else(|| placeholder(gfx, "歌曲封面", (280, 280)));
    let credits = stack(
        &[
            ("作词", "lyricist"),
            ("作曲", "composer"),
            ("编曲", "arranger"),
        ]
        .map(|(label, key)| text(gfx, &format!("{label}： {}", string(&music[key])), 40, 560)),
        5,
        Align::Start,
    );
    let name = stack(
        &[
            text(gfx, string(&music["title"]), 50, LEFT),
            text(gfx, &format!("ID:{}", number(&music["id"])), 30, 0),
        ],
        10,
        Align::Center,
    );
    let time = text(
        gfx,
        &format!("上线时间：{}", shanghai_time(number(&music["publishedAt"]))),
        40,
        0,
    );
    panel(
        gfx,
        &stack(
            &[row(&[jacket, credits], LEFT, 50), name, time],
            40,
            Align::Center,
        ),
        (40, 30, 30),
        rgba([161, 157, 158], 255),
    )
}

async fn costumes(
    ctx: &RenderRequestContext,
    view: &CardInfoView,
) -> Vec<(&'static str, RgbaImage)> {
    let mut jobs = tokio::task::JoinSet::new();
    for (index, (kind, label)) in [("hair", "发型"), ("head", "发饰"), ("body", "服装")]
        .into_iter()
        .enumerate()
    {
        for (order, asset) in rows(&view.assets["costume"][kind]).iter().enumerate() {
            let file = format!("{}.png", string(asset));
            let ctx = ctx.clone();
            let server = view.pjsk_type;
            jobs.spawn(async move {
                let image = get_pjsk_asset_resized(
                    &ctx,
                    "startapp/thumbnail/costume",
                    &file,
                    server,
                    Some((180, 180)),
                )
                .await
                .ok()
                .flatten()?;
                Some(((index, order), label, image))
            });
        }
    }
    let mut result = Vec::new();
    while let Some(job) = jobs.join_next().await {
        if let Ok(Some(image)) = job {
            result.push(image);
        }
    }
    result.sort_by_key(|(order, _, _)| *order);
    result
        .into_iter()
        .map(|(_, label, image)| (label, image))
        .collect()
}

#[async_trait]
impl Renderer for CardInfo {
    fn name(&self) -> &'static str {
        "cardinfo"
    }

    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput> {
        let view: CardInfoView = parse_payload(payload)?;
        let id = number(&view.id);
        let server = view.pjsk_type;
        let cards = view.card();
        let gfx = ctx.gfx()?;
        let static_dir = ctx.app.paths.static_dir.clone();
        let server_name = ctx.app.server_name(server).to_uppercase();
        let fetch_card = async |trained| {
            tokio::join!(
                cardlarge(ctx, id, trained, Some(&cards), server),
                cardthumnail(ctx, id, trained, Some(&cards), false, false, server)
            )
        };
        let trained_card = async {
            if view.trained() {
                Some(fetch_card(true).await)
            } else {
                None
            }
        };
        let (normal, trained, costumes, assets) = tokio::join!(
            fetch_card(false),
            trained_card,
            costumes(ctx, &view),
            section_assets(ctx, &view),
        );
        let card_images = [Some(normal), trained]
            .into_iter()
            .flatten()
            .map(|(large, thumb)| {
                let optional_missing = |err: DrawError| match err {
                    DrawError::LocalOnlyAssetNotFound(_) => Ok(None),
                    other => Err(other),
                };
                let large = large.map(Some).or_else(optional_missing)?;
                Ok::<_, DrawError>((large, thumb.or_else(optional_missing)?))
            })
            .collect::<Result<Vec<_>>>()?;
        render_jpeg(gfx, 85, false, move |gfx| {
            compose(
                gfx,
                &static_dir,
                &view,
                &server_name,
                card_images,
                costumes,
                assets,
            )
        })
        .await
    }
}

type CardImages = Vec<(Option<RgbaImage>, Option<RgbaImage>)>;

fn compose(
    gfx: &GfxContext,
    static_dir: &Path,
    view: &CardInfoView,
    server: &str,
    cards: CardImages,
    costumes: Vec<(&'static str, RgbaImage)>,
    assets: SectionAssets,
) -> RgbaImage {
    let mut large = Vec::new();
    let mut thumbs = Vec::new();
    for (art, thumbnail) in cards {
        large.push(art.map_or_else(
            || placeholder(gfx, "卡面", (COLUMN, 585)),
            |img| resize_lanczos(&img, COLUMN, 585),
        ));
        thumbs.push(thumbnail.map_or_else(
            || placeholder(gfx, "缩略图", (180, 180)),
            |thumb| {
                render_card_thumbnail_tile(
                    gfx,
                    static_dir,
                    &thumb,
                    CardTileOptions {
                        size: 180,
                        ..Default::default()
                    },
                )
            },
        ));
    }
    let mut left = vec![stack(&large, 30, Align::Start)];
    let mut right = vec![
        title(gfx, static_dir, view),
        detail(gfx, view, server, thumbs, costumes),
    ];
    if number(&view.gacha["id"]) != 0 {
        let label = if view.release_at == view.gacha["startAt"] {
            "当期卡池"
        } else {
            "初次可得卡池"
        };
        left.extend([
            heading(gfx, label),
            gacha_section(gfx, view, assets.gacha.as_ref()),
        ]);
    }
    if number(&view.event["id"]) != 0 {
        left.extend([
            heading(gfx, "活动"),
            event_section(gfx, static_dir, &view.event, assets.event.as_ref()),
        ]);
    }
    if number(&view.music["id"]) != 0 {
        let height = |images: &[RgbaImage]| images.iter().map(RgbaImage::height).sum::<u32>();
        let column = if height(&left) + GAP * (right.len() - 1) as u32
            > height(&right) + GAP * (left.len() - 1) as u32 + 80
        {
            &mut right
        } else {
            &mut left
        };
        column.extend([
            heading(gfx, "歌曲"),
            music_section(gfx, &view.music, assets.music),
        ]);
    }
    let left = stack(&left, GAP, Align::Start);
    let right = stack(&right, GAP, Align::Start);
    let (w, h) = (
        left.width() + right.width() + 180,
        left.height().max(right.height()) + 360,
    );
    let mut canvas = gfx
        .cache
        .open_pjsk_image(static_dir.join("pics/cardinfo.png"), Some((w, h)))
        .map_or_else(
            |_| RgbaImage::from_pixel(w, h, rgba([248, 246, 252], 255)),
            |img| Arc::unwrap_or_clone(img),
        );
    paste_rgba(&mut canvas, &left, 60, 180);
    paste_rgba(&mut canvas, &right, left.width() as i32 + 120, 180);
    if let Ok(badge) = gfx
        .cache
        .open_pjsk_image(static_dir.join("pics/cardinfo_badge.png"), None)
    {
        let badge = fit_width(&badge, right.width() / 2);
        paste_rgba(&mut canvas, &badge, 60, 120 - badge.height() as i32);
    }
    canvas
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_card_view_and_wraps_unicode() {
        let view: CardInfoView = parse_payload(json!({"id":"12","cardRarityType":"rarity_4","isLimited":true,"assets":{"card":"asset"},"event":null})).unwrap();
        assert!(view.trained() && view.limited());
        assert_eq!(view.card()[0]["id"], 12);
        assert_eq!(view.card()[0]["assetbundleName"], "asset");
        assert_eq!(
            wrap_lines("技能效果\n\nABC", 2, |s| s.chars().count() as f32),
            ["技能", "效果", "", "AB", "C"]
        );
    }

    #[tokio::test]
    async fn renders_detail_and_related_sections_as_jpeg() {
        let fixture = RenderFixture::new();
        fixture.png("static/chara/cardFrame_L_rarity_1.png", (80, 50));
        fixture.png("static/chara/cardFrame_rarity_1.png", (32, 32));
        fixture.png("static/chara/rarity_star_normal.png", (32, 32));
        fixture.png("static/chara/icon_attribute_cool.png", (32, 32));
        fixture.png(
            "ondemand/jp/startapp/character/member/test/card_normal.png",
            (80, 50),
        );
        fixture.png(
            "ondemand/jp/startapp/thumbnail/chara/test_normal.png",
            (32, 32),
        );
        fixture.png(
            "ondemand/jp/startapp/thumbnail/costume/test_body.png",
            (32, 32),
        );
        let ctx = fixture.context();
        let payload = json!({
            "id":1,"cardRarityType":"rarity_1","attr":"cool","prefix":"Card","charaName":"Player",
            "assets":{"card":"test","costume":{"body":["test_body"]}},
            "event":{"id":11,"name":"Event"},"music":{"id":12,"title":"Song"},"gacha":{"id":13,"name":"Gacha"}
        });
        let output = CardInfo.render(&ctx, payload).await.unwrap();
        let image =
            image::load_from_memory_with_format(&output.images[0], image::ImageFormat::Jpeg)
                .unwrap()
                .into_rgb8();
        assert!(image.width() >= 2100 && image.height() > 2000);
    }
}
