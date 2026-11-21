use std::{
    cmp::Reverse,
    collections::{HashMap, HashSet},
    path::{Path, PathBuf},
    sync::{Arc, Mutex, OnceLock},
    time::SystemTime,
};

use crate::components::{
    card::cardthumnail,
    event_data::{
        analysisunitid, get_ban_events_id_set, get_event_banner_chara_ids, normalize_master_list,
    },
    ui::{draw_round_panel, paste_round_image},
};
use crate::core::{
    AssetSourcePolicy, DrawError, RenderOutput, RenderRequestContext, Renderer, Result,
};
use crate::gfx::{
    GfxContext, PJSK_WATERMARK_TEXT, RenderContextGfxExt,
    TextAnchor::{La, Lm, Mm, Ra},
    gaussian_blur_rgba, paste_rgba, resize_lanczos,
};
use async_trait::async_trait;
use image::{RgbaImage, imageops};
use serde_json::Value;

use crate::renderers::common::{
    BOLD, MEDIUM, RODIN, SoftGlow, alpha_bounds, character_portrait, fit_text, label, load_ordered,
    make_style_gradient_bg, number, render_error, render_jpeg, rgba, rounded_panel_overlay, rows,
    shanghai_time, string,
};

const CHARA_NAMES: [&str; 26] = [
    "ick", "saki", "hnm", "shiho", "mnr", "hrk", "airi", "szk", "khn", "an", "akt", "toya", "tks",
    "emu", "nene", "rui", "knd", "mfy", "ena", "mzk", "miku", "rin", "len", "luka", "meiko",
    "kaito",
];
const INK: [u8; 3] = [44, 36, 58];
const MUTED: [u8; 3] = [118, 112, 132];
const PANEL: (u32, u32) = (835, 250);

pub(crate) struct EventInfo;
pub(crate) struct EventCatalog;

struct EventAssets<'a> {
    ctx: &'a RenderRequestContext,
    server: i32,
    bundle: &'a str,
}

impl EventAssets<'_> {
    async fn load(&self, candidates: &[(&str, &str)]) -> Result<Option<Arc<RgbaImage>>> {
        let mut missing = None;
        for (directory, filename) in candidates {
            let path = match *directory {
                "story" => format!("ondemand/event_story/{}/screen_image", self.bundle),
                directory => format!("ondemand/event/{}/{directory}", self.bundle),
            };
            match self
                .ctx
                .app
                .get_asset_cached(&path, filename, self.server, self.ctx.policy, true)
                .await
            {
                Ok(Some(image)) => return Ok(Some(image)),
                Ok(None) => {}
                Err(
                    error @ (DrawError::LocalOnlyAsset(_) | DrawError::LocalOnlyAssetNotFound(_)),
                ) => {
                    if missing.is_none() || matches!(error, DrawError::LocalOnlyAsset(_)) {
                        missing = Some(error);
                    }
                }
                Err(error) => return Err(error),
            }
        }
        if self.ctx.policy == AssetSourcePolicy::LocalOnly {
            return Err(missing
                .unwrap_or_else(|| DrawError::LocalOnlyAssetNotFound(self.bundle.to_owned())));
        }
        Ok(None)
    }
}

#[derive(Clone)]
struct BonusCharacter {
    id: Option<i64>,
    unit: String,
    icon: String,
}

fn analyze(
    ctx: &RenderRequestContext,
    server: i32,
    ids: impl IntoIterator<Item = i64>,
    units: &Value,
) -> Vec<BonusCharacter> {
    ids.into_iter()
        .filter_map(|id| analysisunitid(ctx, id, Some(units), server).ok().flatten())
        .map(|(id, unit, icon)| BonusCharacter {
            id: Some(id),
            unit,
            icon,
        })
        .collect()
}

fn merge_virtual_singers(characters: &mut Vec<BonusCharacter>) {
    if let Some(first) = characters.first()
        && characters
            .iter()
            .all(|character| character.unit == first.unit)
    {
        let unit = first.unit.clone();
        characters.retain(|character| character.id.is_some_and(|id| id <= 20));
        characters.push(BonusCharacter {
            id: None,
            unit,
            icon: "vs_90.png".into(),
        });
    }
}

async fn thumbnail(
    ctx: &RenderRequestContext,
    server: i32,
    id: i64,
    cards: &Value,
    trained: bool,
) -> Result<Option<RgbaImage>> {
    match cardthumnail(ctx, id, trained, Some(cards), false, false, server).await {
        Ok(None) if ctx.policy == AssetSourcePolicy::LocalOnly => Err(
            DrawError::LocalOnlyAssetNotFound(format!("活动卡牌缩略图未就绪: {id}")),
        ),
        Err(_) if trained && ctx.policy != AssetSourcePolicy::LocalOnly => Ok(None),
        result => result,
    }
}

fn static_icon(gfx: &GfxContext, root: &Path, name: &str, size: u32) -> Option<Arc<RgbaImage>> {
    gfx.cache
        .open_pjsk_image(root.join("chara").join(name), Some((size, size)))
        .ok()
}

#[async_trait]
impl Renderer for EventInfo {
    fn name(&self) -> &'static str {
        "event_info"
    }

    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput> {
        let event = &payload["event"];
        let server = number(&payload["pjsk_type"]) as i32;
        let assets = EventAssets {
            ctx,
            server,
            bundle: string(&event["assetbundleName"]),
        };
        let background = assets
            .load(&[("story", "story_bg.png"), ("screen", "bg.png")])
            .await?;
        let character = assets
            .load(&[("screen", "character.png"), ("story", "story_title.png")])
            .await?;
        let logo = assets.load(&[("logo", "logo.png")]).await?;
        let store = &ctx.app.master_data;
        let cards = store.load_master_data_async("cards.json", server).await?;
        let units = store
            .load_master_data_async("gameCharacterUnits.json", server)
            .await?;
        let id = number(&event["id"]);
        let attr = string(&event["bonuseattr"]).to_owned();
        let end = number(&event["aggregateAtorin"]);
        let mut new_cards = Vec::new();
        for card in rows(&event["cards"]) {
            if let Some(image) = thumbnail(ctx, server, number(card), &cards, true).await? {
                new_cards.push(image);
            }
        }

        let mut seen = HashSet::new();
        let mut bonus_ids: Vec<_> = store
            .load_master_data_async("eventDeckBonuses.json", server)
            .await
            .ok()
            .iter()
            .flat_map(|data| normalize_master_list(data))
            .filter(|bonus| number(&bonus["eventId"]) == id && number(&bonus["bonusRate"]) == 50)
            .map(|bonus| number(&bonus["gameCharacterUnitId"]))
            .filter(|&id| id > 0 && seen.insert(id))
            .collect();
        if bonus_ids.is_empty() {
            bonus_ids.extend(
                rows(&event["bonusechara"])
                    .iter()
                    .map(number)
                    .filter(|id| seen.insert(*id)),
            );
        }
        let mut characters = analyze(ctx, server, bonus_ids, &units);
        if id >= 37
            && characters
                .iter()
                .any(|character| character.id.is_some_and(|id| id > 20))
        {
            merge_virtual_singers(&mut characters);
        }
        let all_cards = normalize_master_list(&cards);
        let mut bonus_rows = Vec::new();
        for character in characters {
            let mut matching: Vec<_> = all_cards
                .iter()
                .copied()
                .filter(|card| {
                    let id = number(&card["characterId"]);
                    let unit = string(&card["supportUnit"]);
                    character
                        .id
                        .map_or(id > 20 && unit == character.unit, |target| target == id)
                        && (unit == character.unit || unit == "none")
                        && card["attr"] == attr
                        && number(&card["releaseAt"]) < end
                })
                .collect();
            matching.sort_by_key(|card| {
                Reverse((
                    rarity_weight(string(&card["cardRarityType"])),
                    number(&card["releaseAt"]),
                    number(&card["id"]),
                ))
            });
            let card_ids: Vec<i64> = matching
                .into_iter()
                .map(|card| number(&card["id"]))
                .collect();
            let images = load_ordered(
                card_ids.into_iter().map(|cid| {
                    let ctx = ctx.clone();
                    let cards = cards.clone();
                    async move { thumbnail(&ctx, server, cid, &cards, true).await }
                }),
                16,
            )
            .await?
            .into_iter()
            .collect::<Result<Vec<_>>>()?
            .into_iter()
            .flatten()
            .collect::<Vec<_>>();
            if !images.is_empty() {
                bonus_rows.push((character, images));
            }
        }
        if ctx.policy == AssetSourcePolicy::LocalOnly {
            assets
                .load(&[
                    ("story", "banner_event_story.png"),
                    ("screen", "banner.png"),
                ])
                .await?;
        }
        let root = ctx.app.paths.static_dir.clone();
        render_jpeg(ctx.gfx()?, 75, false, move |gfx| {
            let mut canvas = background.map_or_else(
                || RgbaImage::from_pixel(1980, 1210, rgba([200; 3], 255)),
                |image| {
                    let small = resize_lanczos(&image, 990, 605);
                    let mut blurred = gaussian_blur_rgba(&small, 4.0);
                    for px in blurred.as_mut().chunks_exact_mut(4) {
                        px[0] = (f32::from(px[0]) * 0.92) as u8;
                        px[1] = (f32::from(px[1]) * 0.92) as u8;
                        px[2] = (f32::from(px[2]) * 0.92) as u8;
                    }
                    resize_lanczos(&blurred, 1980, 1210)
                },
            );
            if let Some(character) = character {
                let (x, y, w, h) = alpha_bounds(&character, 0);
                let crop = imageops::crop_imm(character.as_ref(), x, y, w, h).to_image();
                paste_rgba(&mut canvas, &crop, 0, (1210 - h as i32).div_euclid(2));
            }
            if let Some(logo) = logo {
                paste_rgba(&mut canvas, &logo, 60, 990 - logo.height() as i32);
            }
            for (y, title, field) in [
                (1010, "开始时间", "startAt"),
                (1090, "结束时间", "aggregateAt"),
            ] {
                rounded_panel_overlay(
                    gfx,
                    &mut canvas,
                    (60, y, 650, y + 58),
                    29,
                    rgba([59, 67, 97], 225),
                    None,
                );
                label(
                    gfx,
                    &mut canvas,
                    (125, y + 29),
                    title,
                    (BOLD, 30.0, [255; 3], Lm),
                );
                label(
                    gfx,
                    &mut canvas,
                    (290, y + 29),
                    string(&payload["event"][field]),
                    (BOLD, 34.0, [255; 3], Lm),
                );
            }
            label(
                gfx,
                &mut canvas,
                (18, 12),
                &format!("Event ID: {id}"),
                (BOLD, 24.0, [20, 20, 35], La),
            );
            section(gfx, &mut canvas, 54, 300, "活动新卡");
            let (mut x, mut y) = (745, 122);
            for image in new_cards {
                paste_rgba(&mut canvas, &resize_lanczos(&image, 136, 136), x, y);
                x += 154;
                if x + 136 > 1900 {
                    x = 735;
                    y += 154;
                }
            }
            y += 172;
            section(gfx, &mut canvas, y, 350, "同属性加成卡片");
            y += 72;
            for (character, images) in bonus_rows {
                for (name, size, x, offset) in [
                    (character.icon, 50, 710, 17),
                    (format!("icon_attribute_{attr}.png"), 64, 770, 10),
                ] {
                    if let Some(icon) = static_icon(gfx, &root, &name, size) {
                        paste_rgba(&mut canvas, &icon, x, y + offset);
                    }
                }
                for (index, image) in images.iter().enumerate() {
                    paste_rgba(
                        &mut canvas,
                        &resize_lanczos(image, 84, 84),
                        845 + (index % 11) as i32 * 94,
                        y + (index / 11) as i32 * 92,
                    );
                }
                y += images.len().div_ceil(11) as i32 * 92 + 12;
            }
            canvas
        })
        .await
    }
}

fn rarity_weight(rarity: &str) -> i32 {
    match rarity {
        "rarity_4" => 5,
        "rarity_birthday" => 4,
        "rarity_3" => 3,
        "rarity_2" => 2,
        "rarity_1" => 1,
        _ => 0,
    }
}

fn section(gfx: &GfxContext, canvas: &mut RgbaImage, y: i32, width: i32, title: &str) {
    rounded_panel_overlay(
        gfx,
        canvas,
        (730, y, 730 + width, y + 48),
        24,
        rgba([59, 67, 97], 245),
        None,
    );
    label(
        gfx,
        canvas,
        (730 + width / 2, y + 24),
        title,
        (BOLD, 30.0, [255; 3], Mm),
    );
}

struct Translation {
    modified: SystemTime,
    size: u64,
    data: Arc<serde_yaml::Value>,
}

fn load_translate(path: PathBuf) -> Result<Arc<serde_yaml::Value>> {
    static CACHE: OnceLock<Mutex<HashMap<PathBuf, Translation>>> = OnceLock::new();
    let mut cache = CACHE
        .get_or_init(Default::default)
        .lock()
        .map_err(render_error)?;
    let metadata = match std::fs::metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            cache.remove(&path);
            return Ok(Arc::default());
        }
        Err(error) => return Err(error.into()),
    };
    let (modified, size) = (metadata.modified()?, metadata.len());
    if let Some(entry) = cache.get(&path)
        && entry.modified == modified
        && entry.size == size
    {
        return Ok(entry.data.clone());
    }
    let data = Arc::new(serde_yaml::from_slice(&std::fs::read(&path)?).map_err(render_error)?);
    cache.insert(
        path,
        Translation {
            modified,
            size,
            data: Arc::clone(&data),
        },
    );
    Ok(data)
}

fn grouped(rows: &Value) -> HashMap<i64, Vec<&Value>> {
    let mut groups = HashMap::<_, Vec<_>>::new();
    for row in normalize_master_list(rows) {
        groups.entry(number(&row["eventId"])).or_default().push(row);
    }
    groups
}

fn box_labels(
    ctx: &RenderRequestContext,
    server: i32,
    events: &Value,
    cards: &Value,
    links: &Value,
) -> Result<HashMap<i64, String>> {
    let banned = get_ban_events_id_set(ctx, server)?;
    let banners = get_event_banner_chara_ids(ctx, server, Some(cards), Some(links), None)?;
    let mut events = normalize_master_list(events);
    events.sort_by_key(|event| number(&event["startAt"]));
    let mut counts = HashMap::<i64, usize>::new();
    let mut labels = HashMap::new();
    for event in events {
        let id = number(&event["id"]);
        if banned.contains(&id)
            && let Some(Some(character)) = banners.get(&id)
        {
            let count = counts.entry(*character).or_default();
            *count += 1;
            let name = CHARA_NAMES
                .get(character.saturating_sub(1) as usize)
                .map_or_else(|| character.to_string(), |name| (*name).to_owned());
            labels.insert(id, format!("{name}{count}箱"));
        }
    }
    Ok(labels)
}

struct CatalogEntry {
    event: Value,
    cards: Vec<i64>,
    characters: Vec<BonusCharacter>,
    attr: String,
    scope: String,
    teams: Vec<Value>,
}

fn catalog_entry(
    event: &Value,
    filter: &Value,
    cards: &[&Value],
    bonuses: &[&Value],
    mut characters: Vec<BonusCharacter>,
    cards_ids: Vec<i64>,
    box_label: Option<&String>,
    teams: &[&Value],
) -> Option<CatalogEntry> {
    let event_type = string(&event["eventType"]);
    if filter["event_type"]
        .as_str()
        .is_some_and(|kind| kind != event_type)
    {
        return None;
    }
    let world_bloom = event_type == "world_bloom";
    let attr = bonuses
        .iter()
        .map(|bonus| string(&bonus["cardAttr"]))
        .find(|attr| !attr.is_empty())
        .or_else(|| {
            cards
                .iter()
                .map(|card| string(&card["attr"]))
                .find(|attr| !attr.is_empty())
        })?;
    if let Some(requested) = filter["event_attr"].as_str()
        && requested != attr
        && !(world_bloom && cards.iter().any(|card| card["attr"] == requested))
    {
        return None;
    }
    let requested = rows(&filter["event_charas_id"]);
    let matches = |candidate: &Value| {
        let (id, unit) = match rows(candidate) {
            [id, unit] => (number(id), unit.as_str()),
            _ => (number(candidate), None),
        };
        let matches = |chara, group| chara == id && unit.is_none_or(|unit| unit == group);
        cards
            .iter()
            .any(|card| matches(number(&card["characterId"]), string(&card["supportUnit"])))
            || characters
                .iter()
                .any(|character| character.id.is_some_and(|id| matches(id, &character.unit)))
    };
    if !requested.is_empty() {
        let found = if filter["isContainAllCharasId"].as_bool().unwrap_or(true) {
            requested.iter().all(matches)
        } else {
            requested.iter().any(matches)
        };
        if !found {
            return None;
        }
    }
    if number(&event["id"]) >= 37 {
        merge_virtual_singers(&mut characters);
    }
    let units: HashSet<_> = characters
        .iter()
        .map(|character| character.unit.as_str())
        .collect();
    let is_team = units.len() == 1;
    if filter["isTeamEvent"]
        .as_bool()
        .is_some_and(|team| team != is_team)
    {
        return None;
    }
    let requested: HashSet<_> = rows(&filter["event_units_name"])
        .iter()
        .filter_map(Value::as_str)
        .collect();
    if !requested.is_empty() {
        let found = if filter["isEqualAllUnits"].as_bool().unwrap_or(true) {
            requested == units
        } else {
            units.len() > 1 && requested.is_subset(&units)
        };
        if !found {
            return None;
        }
    }
    let scope = if world_bloom {
        "WL"
    } else if is_team {
        box_label.map_or("箱活", String::as_str)
    } else {
        "混活"
    };
    Some(CatalogEntry {
        event: event.clone(),
        cards: cards_ids,
        characters,
        attr: attr.to_owned(),
        scope: scope.into(),
        teams: teams.iter().take(2).map(|team| (*team).clone()).collect(),
    })
}

#[async_trait]
impl Renderer for EventCatalog {
    fn name(&self) -> &'static str {
        "event_catalog"
    }

    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput> {
        let server = number(&payload["pjsk_type"]) as i32;
        let store = &ctx.app.master_data;
        let local_events = store.load_master_data_async("events.json", server).await;
        let events = if payload["events"].is_null() {
            local_events.as_ref().map_err(render_error)?.as_ref()
        } else {
            &payload["events"]
        };
        let links = store
            .load_master_data_async("eventCards.json", server)
            .await?;
        let bonuses = store
            .load_master_data_async("eventDeckBonuses.json", server)
            .await?;
        let units = store
            .load_master_data_async("gameCharacterUnits.json", server)
            .await?;
        let cards = store.load_master_data_async("cards.json", server).await?;
        let labels = local_events
            .as_ref()
            .ok()
            .and_then(|events| box_labels(ctx, server, events, &cards, &links).ok())
            .unwrap_or_default();
        let server_name = ctx.app.server_name(server);
        let (teams, translation) = if payload["event_type"] == "marathon" {
            (Arc::default(), Arc::default())
        } else {
            (
                store
                    .load_master_data_async("cheerfulCarnivalTeams.json", server)
                    .await?,
                load_translate(
                    ctx.app
                        .paths
                        .ondemand_dir
                        .join(server_name)
                        .join("translate.yaml"),
                )?,
            )
        };
        let teams = grouped(&teams);
        let links = grouped(&links);
        let bonuses = grouped(&bonuses);
        let mut events = normalize_master_list(events);
        events.sort_by_key(|event| Reverse(number(&event["startAt"])));

        let indexed_cards = store
            .master_data_first_by_id_async("cards.json", server, "id")
            .await?;
        let mut card_events = HashMap::<_, HashSet<_>>::new();
        for (&event, links) in &links {
            for link in links {
                card_events
                    .entry(number(&link["cardId"]))
                    .or_default()
                    .insert(event);
            }
        }
        let mut event_cards = HashMap::<_, Vec<_>>::new();
        for card in normalize_master_list(&cards) {
            if let Some(events) = card_events.get(&number(&card["id"])) {
                for &event in events {
                    event_cards.entry(event).or_default().push(card);
                }
            }
        }
        let limit = payload["display_limit"].as_i64();
        let mut entries = Vec::new();
        for event in events {
            let id = number(&event["id"]);
            let bonuses = bonuses.get(&id).map(Vec::as_slice).unwrap_or_default();
            let characters = analyze(
                ctx,
                server,
                bonuses
                    .iter()
                    .filter(|bonus| {
                        event["eventType"] == "world_bloom" || number(&bonus["bonusRate"]) == 50
                    })
                    .map(|bonus| number(&bonus["gameCharacterUnitId"]))
                    .filter(|&id| id > 0),
                &units,
            );
            let cards_ids = links
                .get(&id)
                .into_iter()
                .flatten()
                .map(|link| number(&link["cardId"]))
                .collect();
            if let Some(entry) = catalog_entry(
                event,
                &payload,
                event_cards.get(&id).map(Vec::as_slice).unwrap_or_default(),
                bonuses,
                characters,
                cards_ids,
                labels.get(&id),
                teams.get(&id).map(Vec::as_slice).unwrap_or_default(),
            ) {
                entries.push(entry);
                if limit.is_some_and(|limit| limit > 0 && entries.len() as i64 >= limit) {
                    break;
                }
            }
        }
        if let Some(limit) = limit.filter(|&limit| limit <= 0) {
            entries.truncate(if limit == 0 {
                0
            } else {
                entries.len().saturating_sub(limit.unsigned_abs() as usize)
            });
        }
        if entries.is_empty() {
            return Ok(RenderOutput::single(Vec::new()));
        }

        let panels = load_ordered(
            entries.into_iter().map(|entry| {
                let ctx = ctx.clone();
                let indexed_cards = indexed_cards.clone();
                let translation = translation.clone();
                async move { load_panel(&ctx, server, entry, &indexed_cards, &translation).await }
            }),
            4,
        )
        .await?
        .into_iter()
        .collect::<Result<Vec<_>>>()?;
        let server_name = server_name.to_uppercase();
        let root = ctx.app.paths.static_dir.clone();
        render_jpeg(ctx.gfx()?, 70, false, move |gfx| {
            draw_catalog(gfx, &root, &server_name, limit.is_some(), panels)
        })
        .await
    }
}

struct CatalogPanel {
    entry: CatalogEntry,
    banner: Option<Arc<RgbaImage>>,
    teams: Vec<(String, Option<Arc<RgbaImage>>)>,
    cards: Vec<(i64, Option<RgbaImage>)>,
}

async fn load_panel(
    ctx: &RenderRequestContext,
    server: i32,
    entry: CatalogEntry,
    cards: &crate::core::MasterIndex,
    translation: &serde_yaml::Value,
) -> Result<CatalogPanel> {
    let assets = EventAssets {
        ctx,
        server,
        bundle: string(&entry.event["assetbundleName"]),
    };
    let banner = assets.load(&[("story", "banner_event_story.png")]).await?;
    let mut teams = Vec::new();
    if entry.event["eventType"] == "cheerful_carnival" {
        for team in &entry.teams {
            let filename = format!("{}.png", string(&team["assetbundleName"]));
            let image = assets.load(&[("team_image", &filename)]).await?;
            let id = number(&team["id"]);
            let names = &translation["cheerful_carnival_teams"];
            let name = names
                .get(serde_yaml::Value::from(id))
                .or_else(|| names.get(id.to_string().as_str()))
                .and_then(serde_yaml::Value::as_str)
                .filter(|name| !name.is_empty())
                .or_else(|| team["teamName"].as_str().filter(|name| !name.is_empty()))
                .map_or_else(|| id.to_string(), str::to_owned);
            teams.push((name, image));
        }
    }
    let mut thumbnails = Vec::new();
    for &id in entry.cards.iter().take(5) {
        let card = cards
            .get(&id.to_string())
            .map(|card| Value::Array(vec![card.clone()]));
        let image = match card {
            Some(card) => thumbnail(ctx, server, id, &card, false).await?,
            None => None,
        };
        thumbnails.push((id, image));
    }
    Ok(CatalogPanel {
        entry,
        banner,
        teams,
        cards: thumbnails,
    })
}

fn draw_catalog(
    gfx: &GfxContext,
    root: &Path,
    server: &str,
    limited: bool,
    panels: Vec<CatalogPanel>,
) -> RgbaImage {
    let columns = panels.len().div_ceil(10) as i32;
    let rows = panels.len().min(10) as i32;
    let w = 68 + columns * 835 + (columns - 1) * 24;
    let h = 132 + rows * 250 + (rows - 1) * 18 + 82;
    let mut canvas = make_style_gradient_bg(
        (w as u32, h as u32),
        [255, 246, 250],
        [236, 244, 255],
        &[
            SoftGlow {
                center: (w as f32 * 0.15, h as f32 * 0.04),
                radii: (w as f32 * 0.35, h as f32 * 0.21),
                color: rgba([255, 190, 220], 72),
            },
            SoftGlow {
                center: (w as f32 * 0.88, h as f32 * 0.75),
                radii: (w as f32 * 0.38, h as f32 * 0.42),
                color: rgba([170, 210, 255], 64),
            },
        ],
    );
    let outline = Some(rgba([255; 3], 245));
    draw_round_panel(
        &gfx.cache,
        &mut canvas,
        (34, 24, w - 34, 114),
        30,
        rgba([255; 3], 218),
        outline,
        true,
    );
    label(
        gfx,
        &mut canvas,
        (62, 54),
        "EVENT LIST",
        (RODIN, 24.0, MUTED, Lm),
    );
    let scope = if limited {
        "近 50 个活动"
    } else {
        "全部活动"
    };
    label(
        gfx,
        &mut canvas,
        (62, 90),
        &format!("{server} 活动列表 · {scope}"),
        (BOLD, 32.0, INK, Lm),
    );
    rounded_panel_overlay(
        gfx,
        &mut canvas,
        (w - 182, 54, w - 62, 92),
        19,
        rgba([88, 92, 118], 220),
        None,
    );
    label(
        gfx,
        &mut canvas,
        (w - 122, 73),
        server,
        (RODIN, 18.0, [255; 3], Mm),
    );

    let mut panel = RgbaImage::new(PANEL.0, PANEL.1);
    draw_round_panel(
        &gfx.cache,
        &mut panel,
        (0, 0, 827, 242),
        24,
        rgba([255; 3], 232),
        outline,
        true,
    );
    for (index, assets) in panels.into_iter().enumerate() {
        let mut image = panel.clone();
        draw_entry(gfx, &mut image, root, assets);
        paste_rgba(
            &mut canvas,
            &image,
            34 + (index / 10) as i32 * 859,
            132 + (index % 10) as i32 * 268,
        );
    }
    let footer = h - 72;
    draw_round_panel(
        &gfx.cache,
        &mut canvas,
        (34, footer, w - 34, h - 24),
        22,
        rgba([255; 3], 205),
        outline,
        true,
    );
    label(
        gfx,
        &mut canvas,
        (58, footer + 26),
        "查活动 + [活动ID] 查询活动详情   /   查卡 + [卡面ID] 查询卡面详情",
        (MEDIUM, 18.0, [0, 204, 187], Lm),
    );
    label(
        gfx,
        &mut canvas,
        (w - 58, h - 32),
        PJSK_WATERMARK_TEXT,
        (MEDIUM, 16.0, MUTED, Ra),
    );
    canvas
}

fn draw_entry(gfx: &GfxContext, panel: &mut RgbaImage, root: &Path, assets: CatalogPanel) {
    let CatalogPanel {
        entry,
        banner,
        teams,
        cards,
    } = assets;
    let event = &entry.event;
    let banner = banner.map_or_else(
        || RgbaImage::from_pixel(286, 112, rgba([232, 232, 238], 255)),
        |image| {
            image::DynamicImage::ImageRgba8((*image).clone())
                .resize_to_fill(286, 112, imageops::FilterType::Lanczos3)
                .into_rgba8()
        },
    );
    paste_round_image(&gfx.cache, panel, &banner, (22, 52), (286, 112), 18);
    rounded_panel_overlay(
        gfx,
        panel,
        (22, 18, 106, 42),
        12,
        rgba([88, 92, 118], 230),
        None,
    );
    label(
        gfx,
        panel,
        (64, 30),
        &format!("#{}", number(&event["id"])),
        (RODIN, 16.0, [255; 3], Mm),
    );
    let fallback = format!("Event {}", number(&event["id"]));
    let title = ["name", "eventName", "assetbundleName"]
        .into_iter()
        .map(|key| string(&event[key]))
        .find(|text| !text.is_empty())
        .unwrap_or(&fallback);
    label(
        gfx,
        panel,
        (118, 30),
        &fit_text(&gfx.fonts, title, MEDIUM, 20.0, 360.0),
        (MEDIUM, 20.0, INK, Lm),
    );
    let kind = match string(&event["eventType"]) {
        "marathon" => "马拉松",
        "cheerful_carnival" => "欢乐嘉年华",
        "world_bloom" => "World Link",
        _ => "活动",
    };
    rounded_panel_overlay(
        gfx,
        panel,
        (326, 54, 446, 82),
        14,
        rgba([244, 238, 255], 255),
        Some(rgba([224, 214, 246], 255)),
    );
    label(gfx, panel, (386, 68), kind, (MEDIUM, 15.0, INK, Mm));
    let label_w = (gfx.fonts.measure_text(MEDIUM, 15.0, &entry.scope).0 as i32 + 28).max(70);
    let (fill, outline) = if entry.scope == "混活" {
        ([238, 248, 255], [215, 232, 248])
    } else {
        ([255, 246, 251], [245, 218, 232])
    };
    rounded_panel_overlay(
        gfx,
        panel,
        (458, 54, 458 + label_w, 82),
        14,
        rgba(fill, 255),
        Some(rgba(outline, 255)),
    );
    label(
        gfx,
        panel,
        (458 + label_w / 2, 68),
        &entry.scope,
        (MEDIUM, 15.0, INK, Mm),
    );

    for (y, field, title, offset) in [
        (104, "startAt", "开始", 0),
        (130, "aggregateAt", "结束", 1000),
    ] {
        let time = shanghai_time(number(&event[field]).saturating_add(offset));
        label(
            gfx,
            panel,
            (326, y),
            &format!(
                "{title} {}",
                time.split_whitespace().next().unwrap_or_default()
            ),
            (MEDIUM, 15.0, MUTED, La),
        );
    }
    let attr_x = 472 + label_w;
    if let Some(icon) = static_icon(gfx, root, &format!("icon_attribute_{}.png", entry.attr), 28) {
        paste_rgba(panel, &icon, attr_x, 54);
    }
    let mut x = attr_x + 38;
    let max_icons = ((803 - x) / 34).clamp(0, 7) as usize;
    for character in entry.characters.iter().take(max_icons) {
        let color = match character.unit.as_str() {
            "light_sound" => [68, 85, 221],
            "idol" => [136, 221, 68],
            "street" => [238, 17, 102],
            "theme_park" => [255, 153, 0],
            "school_refusal" => [136, 68, 153],
            _ => [110, 110, 120],
        };
        rounded_panel_overlay(
            gfx,
            panel,
            (x - 2, 52, x + 29, 83),
            16,
            rgba(color, 255),
            None,
        );
        let id = character.id.unwrap_or(21);
        let variant = if id == 21 && character.unit != "piapro" && !character.unit.is_empty() {
            static_icon(
                gfx,
                root,
                &format!("chara_icon/miku_{}.png", character.unit),
                28,
            )
        } else {
            None
        };
        if let Some(icon) = variant.or_else(|| character_portrait(gfx, root, id)) {
            paste_round_image(&gfx.cache, panel, &icon, (x, 54), (28, 28), 14);
        }
        x += 34;
    }
    let mut team_x = 326;
    for (index, (name, image)) in teams.into_iter().enumerate() {
        if let Some(image) = image {
            paste_round_image(&gfx.cache, panel, &image, (team_x, 152), (46, 46), 12);
            label(
                gfx,
                panel,
                (team_x + 23, 204),
                &fit_text(&gfx.fonts, &name, MEDIUM, 10.0, 66.0),
                (MEDIUM, 10.0, MUTED, Mm),
            );
            if index == 0 {
                label(gfx, panel, (team_x + 64, 175), "VS", (RODIN, 16.0, INK, Mm));
                team_x += 82;
            }
        }
    }
    for (index, (id, image)) in cards.into_iter().enumerate() {
        let x = 22 + index as i32 * 62;
        if let Some(image) = image {
            paste_round_image(&gfx.cache, panel, &image, (x, 174), (54, 54), 12);
        } else {
            rounded_panel_overlay(
                gfx,
                panel,
                (x, 174, x + 54, 228),
                12,
                rgba([232, 232, 238], 255),
                None,
            );
        }
        label(
            gfx,
            panel,
            (x + 27, 238),
            &id.to_string(),
            (MEDIUM, 10.0, MUTED, Mm),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::renderers::common::RenderFixture;
    use image::GenericImageView;
    use serde_json::json;
    use tokio::{
        io::{AsyncReadExt, AsyncWriteExt},
        net::TcpListener,
    };

    #[tokio::test]
    async fn event_info_renders_offline() {
        let fixture = RenderFixture::new();
        fixture.write("ondemand/jp/cards.json", "[]");
        fixture.write("ondemand/jp/gameCharacterUnits.json", "[]");
        let output = EventInfo.render(&fixture.context(), json!({
            "event":{"id":12, "assetbundleName":"example", "startAt":"2025/01/01", "aggregateAt":"2025/01/10"}
        })).await.unwrap();
        assert_eq!(dimensions(&output), (1980, 1210));
    }

    #[tokio::test]
    async fn event_info_propagates_local_only_asset_errors() {
        let fixture = RenderFixture::new();
        fixture.write(
            "ondemand/jp/cards.json",
            r#"[{"id":42,"assetbundleName":"card","cardRarityType":"rarity_3"}]"#,
        );
        fixture.write("ondemand/jp/gameCharacterUnits.json", "[]");
        fixture.png("static/chara/cardFrame_rarity_3.png", (16, 16));
        let mut image = std::io::Cursor::new(Vec::new());
        RgbaImage::from_pixel(16, 16, rgba([128, 64, 192], 255))
            .write_to(&mut image, image::ImageFormat::Png)
            .unwrap();
        let image = image.into_inner();
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base_url = format!("http://{}", listener.local_addr().unwrap());
        fixture.write("config/pjsk/servers.yaml", format!(
            "jp:\n  rip:\n    sources:\n      - name: local-updater\n        base_url: {base_url}\n"
        ));
        let task = tokio::spawn(async move {
            loop {
                let (mut socket, _) = listener.accept().await.unwrap();
                let mut request = Vec::new();
                while !request.ends_with(b"\r\n\r\n") {
                    request.push(socket.read_u8().await.unwrap());
                }
                let request = String::from_utf8(request).unwrap();
                let missing = [
                    "missing_bg",
                    "thumbnail",
                    "banner_event_story.png",
                    "/banner.png",
                ]
                .iter()
                .any(|part| request.lines().next().unwrap().contains(part));
                let (status, body) = if missing {
                    ("404 Not Found", &[][..])
                } else {
                    ("200 OK", image.as_slice())
                };
                socket
                    .write_all(
                        format!(
                            "HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                            body.len()
                        )
                        .as_bytes(),
                    )
                    .await
                    .unwrap();
                socket.write_all(body).await.unwrap();
            }
        });
        let mut ctx = fixture.context();
        ctx.policy = AssetSourcePolicy::LocalOnly;
        for (bundle, cards, expected) in [
            ("missing_bg", json!([]), "bg.png"),
            ("ready", json!([42]), "card_after_training.png"),
            ("ready", json!([]), "banner_event_story.png"),
        ] {
            let error = EventInfo
                .render(
                    &ctx,
                    json!({"event":{"id":12,"assetbundleName":bundle, "cards":cards}}),
                )
                .await
                .err()
                .unwrap();
            assert!(
                matches!(error, DrawError::LocalOnlyAsset(ref detail) | DrawError::LocalOnlyAssetNotFound(ref detail) if detail.contains(expected)),
                "{error}"
            );
        }
        task.abort();
    }

    #[tokio::test]
    async fn event_catalog_filters_and_renders_offline() {
        let fixture = RenderFixture::new();
        fixture.write(
            "ondemand/jp/events.json",
            r#"[{"id":12,"name":"Example","eventType":"cheerful_carnival","assetbundleName":"example"}]"#,
        );
        fixture.write("ondemand/jp/cards.json", "[]");
        fixture.write("ondemand/jp/eventCards.json", "[]");
        fixture.write(
            "ondemand/jp/gameCharacterUnits.json",
            r#"[{"id":1,"unit":"light_sound"}]"#,
        );
        fixture.write(
            "ondemand/jp/eventDeckBonuses.json",
            r#"[{"eventId":12,"cardAttr":"cool","bonusRate":50,"gameCharacterUnitId":1}]"#,
        );
        fixture.write(
            "ondemand/jp/cheerfulCarnivalTeams.json",
            r#"[{"id":12,"eventId":12,"assetbundleName":"team_a","teamName":"Team A"}]"#,
        );
        fixture.write(
            "ondemand/jp/translate.yaml",
            "cheerful_carnival_teams: {12: Translated}",
        );
        fixture.png(
            "ondemand/jp/ondemand/event/example/team_image/team_a.png",
            (60, 60),
        );
        fixture.png(
            "ondemand/jp/ondemand/event_story/example/screen_image/banner_event_story.png",
            (320, 140),
        );
        fixture.png("static/chara/chara_icon/ick.png", (28, 28));
        fixture.png("static/chara/icon_attribute_cool.png", (30, 30));
        let ctx = fixture.context();
        let mut payload = json!({"event_type":"cheerful_carnival", "event_attr":"cool", "event_charas_id":[[1,"light_sound"]]});
        let output = EventCatalog.render(&ctx, payload.clone()).await.unwrap();
        assert_eq!(dimensions(&output), (903, 464));
        payload["event_attr"] = json!("pure");
        let output = EventCatalog.render(&ctx, payload).await.unwrap();
        assert!(output.images[0].is_empty());
    }

    #[test]
    fn translation_cache_updates_on_size_change() {
        let fixture = RenderFixture::new();
        let path = fixture.write("translate.yaml", "cheerful_carnival_teams: {12: A}\n");
        let first = load_translate(path.clone()).unwrap();
        assert!(Arc::ptr_eq(&first, &load_translate(path.clone()).unwrap()));
        fixture.write("translate.yaml", "cheerful_carnival_teams: {12: Beta}\n");
        let updated = load_translate(path).unwrap();
        assert_eq!(
            updated["cheerful_carnival_teams"]
                .get(serde_yaml::Value::from(12))
                .unwrap()
                .as_str(),
            Some("Beta")
        );
    }

    fn dimensions(output: &RenderOutput) -> (u32, u32) {
        assert_eq!(output.images.len(), 1);
        assert!(output.images[0].starts_with(b"\xff\xd8\xff"));
        image::load_from_memory(&output.images[0])
            .unwrap()
            .dimensions()
    }
}
