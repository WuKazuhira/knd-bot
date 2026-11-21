use std::{
    cmp::Reverse,
    collections::{BTreeMap, BTreeSet, HashMap},
    sync::Arc,
};

use crate::components::profile_header::{PjskHeaderData, draw_pjsk_profile_header};
use crate::core::{DrawError, RenderOutput, RenderRequestContext, Result};
use crate::gfx::{GfxContext, PJSK_WATERMARK_TEXT, TextAnchor::La, paste_rgba, resize_lanczos};
use chrono::Local;
use image::{Rgba, RgbaImage};
use serde_json::Value;

use crate::renderers::common::{
    BOLD, MEDIUM, SoftGlow, card_assets, character_portrait, decimal, fit_text, label,
    load_ordered, make_style_gradient_bg, rgba, rounded_panel_overlay, rows, string,
};

use super::{
    common::{ACCENT, Assets, MUTED, OK, Size, TEXT, WARN, panel, resized, truthy},
    compact_schema::{effective_drop_quantity, integer},
    data::{self, Master, SITES, SPECIAL, UNITS, id, rarity},
};

const WIDTH: i32 = 1100;
const PAD: i32 = 30;
const CARD: i32 = WIDTH - 2 * PAD;
const HEADER: i32 = 230;
const TIP: [u8; 3] = [0, 204, 187];
const UNIT_COLORS: [[u8; 3]; 5] = [
    [68, 85, 221],
    [136, 221, 68],
    [238, 17, 102],
    [255, 153, 0],
    [136, 68, 153],
];
type Picture = Arc<RgbaImage>;

#[derive(Clone)]
pub(super) struct View<'a> {
    ctx: &'a RenderRequestContext,
    gfx: &'a Arc<GfxContext>,
    assets: Assets,
    payload: &'a Value,
    region: i32,
}

impl<'a> View<'a> {
    pub(super) fn new(
        ctx: &'a RenderRequestContext,
        gfx: &'a Arc<GfxContext>,
        assets: Assets,
        payload: &'a Value,
        region: i32,
    ) -> Self {
        Self {
            ctx,
            gfx,
            assets,
            payload,
            region,
        }
    }

    pub(super) async fn encode(self, image: RgbaImage, quality: u8) -> Result<RenderOutput> {
        let meta = self.assets.metadata();
        let mut output =
            crate::renderers::common::render_jpeg(self.gfx.clone(), quality, true, move |_| image)
                .await?;
        if let Value::Object(meta) = meta {
            output.meta.extend(meta);
        }
        Ok(RenderOutput::with_meta(output.images, output.meta))
    }

    fn master(&self) -> Master<'_> {
        Master {
            app: &self.ctx.app,
            region: self.region,
        }
    }

    fn flag(&self, key: &str) -> bool {
        truthy(&self.payload[key])
    }

    fn info(&self) -> &Value {
        &self.payload["mysekai_info"]
    }

    fn suite(&self) -> &Value {
        &self.payload["suite_data"]
    }

    fn canvas(&self, height: i32) -> RgbaImage {
        let h = height as f32;
        make_style_gradient_bg(
            (WIDTH as u32, height as u32),
            [255, 246, 250],
            [236, 244, 255],
            &[
                SoftGlow {
                    center: (138., h / 16.),
                    radii: (412., h * 0.19),
                    color: rgba([255, 190, 220], 70),
                },
                SoftGlow {
                    center: (935., h * 0.71),
                    radii: (385., h * 0.46),
                    color: rgba([170, 210, 255], 58),
                },
            ],
        )
    }

    fn card(&self, height: i32) -> RgbaImage {
        panel(self.gfx, CARD as u32, height as u32)
    }

    fn title_card(&self, height: i32, title: &str) -> RgbaImage {
        let mut card = self.card(height);
        self.title(&mut card, (18, 14), title);
        card
    }

    fn title(&self, image: &mut RgbaImage, xy: (i32, i32), text: &str) {
        self.text(image, xy, text, 22., TEXT, true);
    }

    fn text(
        &self,
        image: &mut RgbaImage,
        xy: (i32, i32),
        text: &str,
        size: f32,
        color: [u8; 3],
        bold: bool,
    ) {
        label(
            self.gfx,
            image,
            xy,
            text,
            (if bold { BOLD } else { MEDIUM }, size, color, La),
        );
    }

    fn fit(&self, text: &str, size: f32, width: f32) -> String {
        fit_text(&self.gfx.fonts, text, MEDIUM, size, width)
    }

    fn push(&self, canvas: &mut RgbaImage, card: &RgbaImage, y: &mut i32, gap: i32) {
        self.paste_card(canvas, card, PAD, *y);
        *y += card.height() as i32 + gap;
    }

    fn paste_card(&self, canvas: &mut RgbaImage, card: &RgbaImage, x: i32, y: i32) {
        let shadow = self
            .gfx
            .cache
            .get_soft_shadow(card.dimensions(), 14, 35, 4, [0; 3]);
        paste_rgba(canvas, &shadow, x, y + 2);
        paste_rgba(canvas, card, x, y);
    }

    fn watermark(&self, image: &mut RgbaImage) {
        self.text(
            image,
            (PAD, image.height() as i32 - 28),
            PJSK_WATERMARK_TEXT,
            13.,
            TIP,
            false,
        );
    }

    async fn header(
        &self,
        canvas: &mut RgbaImage,
        y: &mut i32,
        title: &str,
        subtitle: &str,
    ) -> Result<()> {
        let profile = &self.payload["profile"];
        let mut data = PjskHeaderData::from_payload(profile);
        if string(&profile["name"]).is_empty() {
            data.name = "MySekai User".into();
        }
        data.is_private = self.flag("is_private");
        data.user_decks = rows(&profile["userDecks"])
            .iter()
            .filter_map(integer)
            .collect();
        data.user_profile_honors = rows(&profile["userProfileHonors"]).to_vec();
        data.user_honor_missions = rows(&profile["userHonorMissions"]).to_vec();
        let cards = self
            .master()
            .load("cards")
            .ok()
            .map(|cards| card_assets(&cards));
        let badge = [("模式", subtitle)];
        draw_pjsk_profile_header(
            self.ctx,
            canvas,
            (PAD, *y, PAD + CARD, *y + HEADER),
            &data,
            title,
            self.region,
            cards.as_ref(),
            (!subtitle.is_empty()).then_some(badge.as_slice()),
            false,
            false,
        )
        .await?;
        *y += HEADER + 18;
        Ok(())
    }

    fn character(&self, cid: i64, size: Size, visit: bool) -> Picture {
        let paths = &self.ctx.app.paths;
        if visit {
            if let Some(image) = character_portrait(self.gfx, &paths.static_dir, cid) {
                return resized(image, Some(size));
            }
        }
        for rel in [
            format!("chara/chr_sd_{cid:02}_01/chr_sd_{cid:02}_01.png"),
            format!("chara/chr_sd_{cid}_01/chr_sd_{cid}_01.png"),
            format!("chara/chr_ts_{cid}.png"),
            format!("chara/chr_sd_{cid:02}_01.png"),
        ] {
            if let Ok(image) = self
                .gfx
                .cache
                .open_pjsk_image(paths.ondemand_dir.join(rel), None)
            {
                return resized(image, Some(size));
            }
        }
        self.assets.placeholder(size, &cid.to_string())
    }

    fn character_id(&self, unit: i64) -> i64 {
        integer(&self.master().get("gameCharacterUnits", unit)["gameCharacterId"]).unwrap_or(unit)
    }

    fn gate_icon(&self, gate: i64, size: Size) -> Picture {
        self.assets
            .picture(&format!("gate_icon/gate_{gate}.png"), Some(size))
            .unwrap_or_else(|| self.assets.placeholder(size, "?"))
    }

    async fn fixture_icon(&self, fixture: &Value, index: usize, size: Size) -> Picture {
        let asset = string(&fixture["assetbundleName"]);
        let fid = id(fixture, "id");
        if asset.is_empty() {
            let (path, reason) = if fixture.is_null() {
                ("fixture:unknown".into(), "master-missing")
            } else {
                (format!("fixture:{fid}"), "asset-name-missing")
            };
            self.assets.missing(self.region, &path, reason);
            return self.assets.placeholder(size, "?");
        }
        let color = 1 + index.min(rows(&fixture["mysekaiFixtureAnotherColors"]).len());
        let default = format!("mysekai/thumbnail/fixture/{asset}_{color}.png");
        let layout = string(&fixture["mysekaiSettableLayoutType"]);
        let surface = fixture["mysekaiFixtureType"] == "surface_appearance";
        let path = if surface && !layout.is_empty() {
            format!("mysekai/thumbnail/surface_appearance/{asset}/tex_{asset}_{layout}_{color}.png")
        } else {
            default.clone()
        };
        let file = path.rsplit('/').next().unwrap();
        for candidate in [
            file.into(),
            format!("harvest_fixture_icon/rarity_1/{file}"),
            format!("harvest_fixture_icon/rarity_2/{file}"),
            format!("{asset}_{fid}.png"),
        ] {
            if let Some(image) = self.assets.picture(&candidate, Some(size)) {
                return image;
            }
        }
        if let Some(image) = self
            .assets
            .load(&path, self.region, Some(size), false)
            .await
        {
            return image;
        }
        if surface && path != default {
            if let Some(image) = self
                .assets
                .load(&default, self.region, Some(size), false)
                .await
            {
                return image;
            }
        }
        self.assets.missing(self.region, &path, "missing");
        self.assets.placeholder(size, "?")
    }

    async fn resource_icon(&self, key: &str, size: Size) -> Picture {
        let master = self.master();
        if let Some((kind, rid)) = data::resource_parts(key) {
            match kind {
                "mysekai_material" | "mysekai_item" => {
                    let (table, prefix) = if kind == "mysekai_material" {
                        ("mysekaiMaterials", "material")
                    } else {
                        ("mysekaiItems", "item")
                    };
                    let row = master.get(table, rid);
                    let asset = string(&row["iconAssetbundleName"]);
                    if !asset.is_empty() {
                        let path = format!("mysekai/thumbnail/{prefix}/{asset}.png");
                        return self
                            .assets
                            .rip(&path, self.region, Some(size), true, "?")
                            .await;
                    }
                }
                "material" => {
                    return self
                        .assets
                        .rip(
                            &format!("thumbnail/material_rip/material{rid}.png"),
                            self.region,
                            Some(size),
                            true,
                            "?",
                        )
                        .await;
                }
                "mysekai_fixture" | "mysekai_blueprint" => {
                    let fixture = if kind == "mysekai_fixture" {
                        master.get("mysekaiFixtures", rid)
                    } else {
                        let bp = master.get("mysekaiBlueprints", rid);
                        if data::fixture_blueprint(&bp) {
                            master.get("mysekaiFixtures", id(&bp, "craftTargetId"))
                        } else {
                            Value::Null
                        }
                    };
                    let asset = string(&fixture["assetbundleName"]);
                    if !asset.is_empty() {
                        let path = format!("mysekai/thumbnail/fixture/{asset}_1.png");
                        if let Some(image) =
                            self.assets.load(&path, self.region, Some(size), true).await
                        {
                            return image;
                        }
                    }
                }
                "mysekai_music_record" => {
                    let record = master.get("mysekaiMusicRecords", rid);
                    let mid = if record.is_null() {
                        Some(rid)
                    } else {
                        integer(&record["externalId"])
                    };
                    let music = mid.map(|mid| master.get("musics", mid)).unwrap_or_default();
                    let asset = if music.is_null() {
                        mid.map_or_else(
                            || "jacket_s_None".into(),
                            |mid| format!("jacket_s_{mid:03}"),
                        )
                    } else {
                        music["assetbundleName"]
                            .as_str()
                            .unwrap_or("None")
                            .to_owned()
                    };
                    for path in [
                        format!("music/jacket/{asset}_rip/{asset}.png"),
                        format!("thumbnail/music_jacket/{asset}.png"),
                    ] {
                        if let Some(image) =
                            self.assets.load(&path, self.region, Some(size), true).await
                        {
                            return image;
                        }
                    }
                    self.assets
                        .missing(self.region, key, "remote-record-cover-missing");
                    return self.assets.placeholder(size, "?");
                }
                _ => {}
            }
        }
        self.assets
            .missing(self.region, key, "resource-icon-missing");
        self.assets.placeholder(size, "?")
    }

    async fn resources(&self, keys: Vec<String>, size: Size) -> Result<Vec<Picture>> {
        let ctx = self.ctx.clone();
        let gfx = self.gfx.clone();
        let region = self.region;
        let assets = self.assets.clone();
        load_ordered(
            keys.into_iter().map(move |key| {
                let (ctx, gfx, assets) = (ctx.clone(), gfx.clone(), assets.clone());
                async move {
                    View::new(&ctx, &gfx, assets, &Value::Null, region)
                        .resource_icon(&key, size)
                        .await
                }
            }),
            32,
        )
        .await
    }

    async fn fixtures(&self, fixtures: Vec<(Value, usize)>, size: Size) -> Result<Vec<Picture>> {
        let ctx = self.ctx.clone();
        let gfx = self.gfx.clone();
        let region = self.region;
        let assets = self.assets.clone();
        load_ordered(
            fixtures.into_iter().map(move |(fixture, color)| {
                let (ctx, gfx, assets) = (ctx.clone(), gfx.clone(), assets.clone());
                async move {
                    View::new(&ctx, &gfx, assets, &Value::Null, region)
                        .fixture_icon(&fixture, color, size)
                        .await
                }
            }),
            32,
        )
        .await
    }

    pub(super) async fn compose_summary(&self) -> Result<RgbaImage> {
        let master = self.master();
        let region = self.ctx.app.server_name(self.region);
        let now = Local::now();
        let (refresh, birthday) = data::last_refresh(region, now);
        let upload = data::timestamp(&self.info()["upload_time"]);
        let (current, forecasts) = data::weather(self.info(), region, now);
        let special = data::special_resources(self.info());
        let gates = data::gate_groups(&master, 0, true)?;
        let (visitors, reservation) = data::visitors(&master, self.info());
        let gate = &self.info()["userMysekaiGateCharacterVisit"]["userMysekaiGate"];
        let gate_id = id(gate, "mysekaiGateId");
        let level = id(gate, "mysekaiGateLevel");
        let msg = string(&self.payload["data_msg"]);
        let sy = if msg.is_empty() { 122 } else { 148 };
        let owned_record = data::has_owned_refresh_music_record(
            self.info(),
            refresh.timestamp() as f64,
            now.timestamp_millis() as f64 / 1000.,
        );
        let owned_y = sy + if special.is_empty() { 32 } else { 43 };
        let panel_height = if owned_record {
            220.max(owned_y + 38)
        } else {
            220
        };
        let mut canvas = self.canvas(361 + panel_height * 2);
        let mut y = PAD;
        self.header(&mut canvas, &mut y, "MySekai 摘要", &region.to_uppercase())
            .await?;
        let mut cards: Vec<_> = ["数据状态", "来访角色", "天气预报", "当前门升级材料"]
            .into_iter()
            .map(|title| {
                let mut card = panel(self.gfx, 511, panel_height as u32);
                self.title(&mut card, (18, 14), title);
                card
            })
            .collect();
        let upload_text = upload.and_then(data::local_time).map_or_else(
            || "未知".into(),
            |time| {
                let diff = (now - time).num_seconds();
                let relative = match diff {
                    ..60 => "刚刚".into(),
                    60..3600 => format!("{}分钟前", diff / 60),
                    3600..86400 => format!("{}小时前", diff / 3600),
                    _ => format!("{}天前", diff / 86400),
                };
                format!("{} ({relative})", time.format("%m-%d %H:%M:%S"))
            },
        );
        let reason = birthday.map_or_else(
            || "自然刷新".into(),
            |(cid, start)| {
                format!(
                    "{}生日{}",
                    master.name("gameCharacters", cid, "角色"),
                    if start { "开始" } else { "结束" }
                )
            },
        );
        self.text(
            &mut cards[0],
            (22, 56),
            &format!("MySekai：{upload_text}"),
            17.,
            MUTED,
            false,
        );
        self.text(
            &mut cards[0],
            (22, 88),
            &format!("上次刷新：{}（{reason}）", refresh.format("%m-%d %H:%M")),
            16.,
            ACCENT,
            false,
        );
        if !msg.is_empty() {
            self.text(
                &mut cards[0],
                (22, 122),
                &self.fit(msg, 15., 467.),
                15.,
                WARN,
                false,
            );
        }
        self.text(&mut cards[0], (22, sy), "特殊资源：", 16., TEXT, true);
        let mids: Vec<_> = SPECIAL
            .into_iter()
            .filter(|id| special.is_empty() || special.contains_key(id))
            .collect();
        let icons = self
            .resources(
                mids.iter()
                    .map(|mid| format!("mysekai_material_{mid}"))
                    .collect(),
                (28, 28),
            )
            .await?;
        if special.is_empty() {
            self.text(&mut cards[0], (110, sy), "本次未刷新", 14., MUTED, false);
        }
        for (i, (mid, image)) in mids.into_iter().zip(icons).enumerate() {
            let x = if special.is_empty() {
                198 + i as i32 * 34
            } else {
                110 + i as i32 * 92
            };
            paste_rgba(&mut cards[0], &image, x, sy - 4);
            if let Some((quantity, sites)) = special.get(&mid) {
                self.text(
                    &mut cards[0],
                    (x + 30, sy - 2),
                    &format!("x{quantity}"),
                    14.,
                    WARN,
                    true,
                );
                let mut names = Vec::new();
                for site in sites {
                    let name = master.site_name(*site);
                    if !names.contains(&name) {
                        names.push(name);
                    }
                }
                self.text(
                    &mut cards[0],
                    (x, sy + 24),
                    &self.fit(&names.join("、"), 11., 68.),
                    11.,
                    MUTED,
                    false,
                );
            }
        }
        if owned_record {
            self.text(
                &mut cards[0],
                (22, owned_y),
                "本次刷新的唱片已获取",
                17.,
                [210, 48, 60],
                true,
            );
        }
        if gate_id != 0 {
            paste_rgba(&mut cards[1], &self.gate_icon(gate_id, (54, 54)), 22, 72);
            self.text(
                &mut cards[1],
                (29, 126),
                &format!("Lv.{level}"),
                16.,
                UNIT_COLORS
                    .get(gate_id.saturating_sub(1) as usize)
                    .copied()
                    .unwrap_or(ACCENT),
                true,
            );
        }
        if visitors.is_empty() {
            self.text(
                &mut cards[1],
                (105, 88),
                "暂无来访角色数据",
                17.,
                MUTED,
                false,
            );
        }
        for (i, (group, unit)) in visitors.into_iter().take(5).enumerate() {
            let x = 105 + i as i32 * 74;
            let cid = self.character_id(unit);
            paste_rgba(&mut cards[1], &self.character(cid, (60, 60), true), x, 62);
            if Some(group) == reservation {
                if let Some(icon) = self.assets.picture("invitationcard.png", Some((22, 22))) {
                    paste_rgba(&mut cards[1], &icon, x + 2, 110);
                }
            }
            if let Some(cid) = integer(&master.get("gameCharacterUnits", unit)["gameCharacterId"])
                .filter(|&id| id != 0)
            {
                let icon = self
                    .assets
                    .rip(
                        &format!("mysekai/item_preview/material/item_memoria_{cid}.png"),
                        self.region,
                        Some((26, 26)),
                        false,
                        "?",
                    )
                    .await;
                paste_rgba(&mut cards[1], &icon, x + 42, 106);
            }
        }
        if forecasts.is_empty() {
            self.text(&mut cards[2], (22, 88), "无天气数据", 17., MUTED, false);
        }
        for (i, (phenomenon, time)) in forecasts.into_iter().take(5).enumerate() {
            let x = 22 + i as i32 * 88;
            let current = Some(phenomenon) == current;
            let icon = master.get("mysekaiPhenomenas", phenomenon);
            let asset = string(&icon["iconAssetbundleName"]);
            let icon = if asset.is_empty() {
                self.assets.placeholder((44, 44), "☁")
            } else {
                self.assets
                    .rip(
                        &format!("mysekai/thumbnail/phenomena/{asset}.png"),
                        self.region,
                        Some((44, 44)),
                        false,
                        "?",
                    )
                    .await
            };
            rounded_panel_overlay(
                self.gfx,
                &mut cards[2],
                (x, 58, x + 78, 136),
                12,
                rgba(
                    if current {
                        [235, 255, 255]
                    } else {
                        [246, 246, 252]
                    },
                    240,
                ),
                Some(rgba([216, 234, 240], 255)),
            );
            self.text(
                &mut cards[2],
                (x + 20, 64),
                &time.format("%H:%M").to_string(),
                13.,
                if current { TEXT } else { MUTED },
                true,
            );
            paste_rgba(&mut cards[2], &icon, x + 17, 86);
        }
        if gate_id == 0 || level == 0 {
            self.text(
                &mut cards[3],
                (22, 96),
                "没有当前门或 Suite 材料数据",
                17.,
                MUTED,
                false,
            );
        } else {
            let owned = data::quantities(
                self.suite(),
                "userMysekaiMaterials",
                "mysekaiMaterialId",
                "quantity",
            );
            paste_rgba(&mut cards[3], &self.gate_icon(gate_id, (42, 42)), 22, 52);
            self.text(
                &mut cards[3],
                (72, 62),
                &format!("当前 Lv.{level}"),
                18.,
                ACCENT,
                true,
            );
            for index in 0..2 {
                let target = level.saturating_add(1 + i64::from(index));
                let yy = 100 + index * 55;
                self.text(
                    &mut cards[3],
                    (24, yy + 14),
                    &format!("Lv.{target}"),
                    17.,
                    TEXT,
                    true,
                );
                let costs = gates
                    .get(&gate_id)
                    .and_then(|levels| levels.get(&target))
                    .map(Vec::as_slice)
                    .unwrap_or_default();
                if costs.is_empty() {
                    self.text(
                        &mut cards[3],
                        (92, yy + 14),
                        "没有材料表或已接近满级",
                        15.,
                        MUTED,
                        false,
                    );
                }
                let icons = self
                    .resources(costs.iter().take(5).map(material_key).collect(), (30, 30))
                    .await?;
                for (i, (cost, icon)) in costs.iter().zip(icons).enumerate() {
                    let x = 92 + i as i32 * 82;
                    let need = id(cost, "quantity");
                    let own = owned
                        .get(&id(cost, "mysekaiMaterialId"))
                        .copied()
                        .unwrap_or_default();
                    paste_rgba(&mut cards[3], &icon, x, yy + 5);
                    self.text(
                        &mut cards[3],
                        (x + 34, yy + 10),
                        &format!("{own}/{need}"),
                        13.,
                        if own >= need { OK } else { WARN },
                        true,
                    );
                }
            }
        }
        for (i, card) in cards.into_iter().enumerate() {
            self.paste_card(
                &mut canvas,
                &card,
                PAD + (i % 2) as i32 * 529,
                y + (i / 2) as i32 * (panel_height + 18),
            );
        }
        Ok(canvas)
    }

    pub(super) async fn compose_res_list(&self) -> Result<RgbaImage> {
        let mut result = data::summarize_resources(self.info(), self.flag("show_harvested"));
        let sections: Vec<_> = SITES
            .into_iter()
            .filter_map(|site| {
                let mut items: Vec<_> = result.remove(&site)?.into_iter().collect();
                if items.is_empty() {
                    return None;
                }
                items.sort_by(|(key, qty), (other, other_qty)| {
                    (Reverse(rarity(key)), Reverse(*qty), key).cmp(&(
                        Reverse(rarity(other)),
                        Reverse(*other_qty),
                        other,
                    ))
                });
                let height = (26 + items.len().div_ceil(5) as i32 * 62).max(126);
                Some((site, items, height))
            })
            .collect();
        let msg = string(&self.payload["data_msg"]);
        let mut canvas = self.canvas(
            298 + sections
                .iter()
                .map(|(_, _, height)| height + 16)
                .sum::<i32>()
                + if msg.is_empty() { 0 } else { 70 },
        );
        let mut y = PAD;
        self.header(&mut canvas, &mut y, "MySekai 资源", "数量视图")
            .await?;
        for (site, items, height) in sections {
            let mut card = self.card(height);
            let thumbnail = self
                .assets
                .rip(
                    &format!("mysekai/site/sitemap/texture/img_harvest_site_{site}.png"),
                    0,
                    Some((190, 92)),
                    false,
                    &site.to_string(),
                )
                .await;
            paste_rgba(&mut card, &thumbnail, 18, 18);
            self.text(
                &mut card,
                (26, 86),
                &self.fit(&self.master().site_name(site), 16., 170.),
                16.,
                [255; 3],
                true,
            );
            let icons = self
                .resources(items.iter().map(|(key, _)| key.clone()).collect(), (42, 42))
                .await?;
            for (i, ((key, qty), icon)) in items.into_iter().zip(icons).enumerate() {
                let (x, yy) = grid(i, 5, (230, 16), (160, 62));
                let (fill, color) = match rarity(&key) {
                    2 => ([250, 235, 255], WARN),
                    1 => ([235, 252, 250], [80, 70, 190]),
                    _ => ([235, 248, 250], TEXT),
                };
                rounded_panel_overlay(
                    self.gfx,
                    &mut card,
                    (x, yy + 2, x + 46, yy + 48),
                    6,
                    rgba(fill, 210),
                    None,
                );
                paste_rgba(&mut card, &icon, x + 2, yy + 4);
                self.text(
                    &mut card,
                    (x + 56, yy + 7),
                    &qty.to_string(),
                    32.,
                    color,
                    true,
                );
            }
            self.push(&mut canvas, &card, &mut y, 16);
        }
        if !msg.is_empty() {
            let mut card = self.card(54);
            self.text(
                &mut card,
                (18, 16),
                &self.fit(msg, 16., 1004.),
                16.,
                WARN,
                false,
            );
            self.push(&mut canvas, &card, &mut y, 16);
        }
        Ok(canvas)
    }

    pub(super) async fn compose_fixture_list(&self) -> Result<RgbaImage> {
        let craftable = self.flag("only_craftable");
        let groups = data::fixture_collection(&self.master(), self.info(), craftable)?;
        let has_profile = self.flag("profile");
        let mut sections = Vec::new();
        let (mut total, mut obtained) = (0, 0);
        for (main, subgroups) in groups {
            let height = 50
                + subgroups
                    .values()
                    .map(|v| (v.len().div_ceil(18) as i32 + 1) * 46)
                    .sum::<i32>();
            for fixture in subgroups
                .values()
                .flatten()
                .filter(|f| f.birthday.is_none())
            {
                total += 1;
                obtained += usize::from(fixture.owned);
            }
            sections.push((main, subgroups, height));
        }
        let fixture_tasks = sections
            .iter()
            .flat_map(|(_, subgroups, _)| subgroups.values())
            .flatten()
            .map(|fixture| (fixture.row.clone(), 0))
            .collect();
        let mut icons = self.fixtures(fixture_tasks, (34, 34)).await?.into_iter();
        let start = if has_profile { 278 } else { 80 };
        let height = start
            + 55
            + sections
                .iter()
                .map(|(_, _, height)| height + 16)
                .sum::<i32>();
        let mut canvas = self.canvas(height);
        let mut y = PAD;
        if has_profile {
            let subtitle = if truthy(self.info()) && total != 0 {
                progress(obtained, total)
            } else {
                "收集视图".into()
            };
            self.header(
                &mut canvas,
                &mut y,
                if craftable {
                    "MySekai 蓝图"
                } else {
                    "MySekai 家具"
                },
                &subtitle,
            )
            .await?;
        } else {
            self.text(&mut canvas, (PAD, y), "MySekai 家具列表", 30., ACCENT, true);
            y += 50;
        }
        for (main, subgroups, height) in sections {
            let mut card = self.title_card(height, &self.master().genre(main, true));
            let mut yy = 50;
            let show_sub = subgroups.len() > 1;
            for (sub, fixtures) in subgroups {
                if show_sub && sub != -1 {
                    self.text(
                        &mut card,
                        (22, yy),
                        &self.master().genre(sub, false),
                        15.,
                        MUTED,
                        false,
                    );
                    yy += 24;
                }
                for (i, (fixture, icon)) in fixtures.iter().zip(&mut icons).enumerate() {
                    let (x, fy) = grid(i, 18, (22, yy), (56, 46));
                    paste_rgba(&mut card, &icon, x, fy);
                    if !fixture.owned {
                        shade(&mut card, (x, fy), (34, 34), 95);
                    }
                    self.text(
                        &mut card,
                        (x, fy + 34),
                        &fixture.row["id"].to_string(),
                        9.,
                        MUTED,
                        false,
                    );
                    if let Some(cid) = fixture.birthday {
                        paste_rgba(&mut card, &self.character(cid, (14, 14), false), x + 20, fy);
                    }
                }
                yy += fixtures.len().div_ceil(18) as i32 * 46;
            }
            self.push(&mut canvas, &card, &mut y, 16);
        }
        self.watermark(&mut canvas);
        Ok(canvas)
    }

    pub(super) async fn compose_fixture_detail(&self) -> Result<RgbaImage> {
        let master = self.master();
        let mut fixtures = Vec::new();
        for value in rows(&self.payload["fids"]) {
            let fid = integer(value)
                .ok_or_else(|| DrawError::InvalidPayload("fids must contain integers".into()))?;
            let fixture = master.get("mysekaiFixtures", fid);
            if !fixture.is_null() {
                fixtures.push(fixture);
            }
        }
        if fixtures.is_empty() {
            let mut canvas = self.canvas(253);
            paste_rgba(
                &mut canvas,
                &self.assets.placeholder((700, 160), "无家具"),
                PAD,
                PAD,
            );
            self.watermark(&mut canvas);
            return Ok(canvas);
        }
        let blueprints = master.required("mysekaiBlueprints")?;
        let mut canvas = self.canvas(75 + fixtures.len() as i32 * 338);
        let region = self.ctx.app.server_name(self.region).to_uppercase();
        let mut y = PAD;
        for fixture in fixtures {
            let fid = id(&fixture, "id");
            let mut card = self.title_card(
                320,
                &format!("【{region}-{fid}】{}", string(&fixture["name"])),
            );
            let colors: Vec<_> = std::iter::once(&fixture["colorCode"])
                .chain(
                    rows(&fixture["mysekaiFixtureAnotherColors"])
                        .iter()
                        .map(|row| &row["colorCode"]),
                )
                .take(5)
                .collect();
            let icons = self
                .fixtures(
                    (0..colors.len()).map(|i| (fixture.clone(), i)).collect(),
                    (96, 96),
                )
                .await?;
            for (i, (color, icon)) in colors.into_iter().zip(icons).enumerate() {
                let x = 22 + i as i32 * 112;
                paste_rgba(&mut card, &icon, x, 68);
                if let Some(rgb) = parse_color(string(color)) {
                    rounded_panel_overlay(
                        self.gfx,
                        &mut card,
                        (x, 172, x + 96, 188),
                        4,
                        rgba(rgb, 255),
                        Some(rgba([160; 3], 255)),
                    );
                }
            }
            let size = &fixture["gridSize"];
            let dimension =
                |field| integer(&size[field]).map_or_else(|| "?".into(), |n| n.to_string());
            let yes = |condition| if condition { "是" } else { "否" };
            for (index, line) in [
                format!("类型：{}", master.genre(data::main_genre(&fixture), true)),
                format!(
                    "大小：{}×{}×{}",
                    dimension("width"),
                    dimension("depth"),
                    dimension("height")
                ),
                format!(
                    "首次/重复放置消耗：{} / {}",
                    id(&fixture, "firstPutCost"),
                    id(&fixture, "secondPutCost")
                ),
                format!(
                    "可制作：{}    可回收：{}",
                    yes(truthy(&fixture["isAssembled"])),
                    yes(truthy(&fixture["isDisassembled"]))
                ),
                format!(
                    "玩家交互：{}    角色交互：{}",
                    yes(fixture["mysekaiFixturePlayerActionType"]
                        .as_str()
                        .unwrap_or("no_action")
                        != "no_action"),
                    yes(truthy(&fixture["isGameCharacterAction"]))
                ),
            ]
            .into_iter()
            .enumerate()
            {
                self.text(
                    &mut card,
                    (600, 70 + 30 * index as i32),
                    &line,
                    17.,
                    MUTED,
                    false,
                );
            }
            if let Some(bp) = blueprints
                .iter()
                .find(|bp| data::fixture_blueprint(bp) && id(bp, "craftTargetId") == fid)
            {
                let costs: Vec<_> = master
                    .required("mysekaiBlueprintMaterialCosts")?
                    .into_iter()
                    .filter(|cost| cost["mysekaiBlueprintId"] == bp["id"])
                    .take(14)
                    .collect();
                let icons = self
                    .resources(costs.iter().map(material_key).collect(), (36, 36))
                    .await?;
                self.text(&mut card, (18, 215), "制作材料", 18., TEXT, true);
                for (i, (cost, icon)) in costs.iter().zip(icons).enumerate() {
                    let x = 115 + 60 * i as i32;
                    paste_rgba(&mut card, &icon, x, 208);
                    self.text(
                        &mut card,
                        (x, 244),
                        &format!("x{}", id(cost, "quantity")),
                        12.,
                        TEXT,
                        false,
                    );
                }
            }
            self.push(&mut canvas, &card, &mut y, 18);
        }
        self.watermark(&mut canvas);
        Ok(canvas)
    }

    pub(super) async fn compose_gate(&self) -> Result<RgbaImage> {
        let groups = data::gate_groups(&self.master(), id(self.payload, "gate_id"), false)?;
        let owned = data::quantities(
            self.suite(),
            "userMysekaiMaterials",
            "mysekaiMaterialId",
            "quantity",
        );
        let current = data::quantities(
            self.suite(),
            "userMysekaiGates",
            "mysekaiGateId",
            "mysekaiGateLevel",
        );
        let has_suite = truthy(self.suite());
        let has_profile = self.flag("profile");
        let sections: Vec<_> = groups
            .into_iter()
            .map(|(gate, levels)| {
                let cur = current.get(&gate).copied().unwrap_or_default();
                let levels: Vec<_> = levels
                    .into_iter()
                    .filter(|(level, _)| !has_suite || *level > cur)
                    .take(if has_suite { 8 } else { 12 })
                    .collect();
                (gate, cur, levels)
            })
            .collect();
        let mut canvas = self.canvas(
            80 + if has_profile { 248 } else { 0 }
                + sections
                    .iter()
                    .map(|(_, _, levels)| 88 + levels.len().max(1) as i32 * 78)
                    .sum::<i32>(),
        );
        let mut y = PAD;
        if has_profile {
            self.header(&mut canvas, &mut y, "MySekai 门升级", "材料视图")
                .await?;
        }
        for (gate, cur, levels) in sections {
            let mut card = self.card(70 + levels.len().max(1) as i32 * 78);
            paste_rgba(&mut card, &self.gate_icon(gate, (48, 48)), 18, 14);
            let title = if has_suite {
                format!("Gate {gate}  当前 Lv.{cur}")
            } else {
                format!("Gate {gate}")
            };
            self.text(&mut card, (78, 22), &title, 24., ACCENT, true);
            for (i, (level, costs)) in levels.into_iter().enumerate() {
                let yy = 70 + i as i32 * 78;
                let own = |row| {
                    owned
                        .get(&id(row, "mysekaiMaterialId"))
                        .copied()
                        .unwrap_or_default()
                };
                let can_afford = |row| own(row) >= id(row, "quantity");
                let color = if !has_suite || costs.is_empty() {
                    TEXT
                } else if costs.iter().all(can_afford) {
                    OK
                } else {
                    WARN
                };
                self.text(
                    &mut card,
                    (22, yy + 20),
                    &format!("Lv.{level}"),
                    22.,
                    color,
                    true,
                );
                if costs.is_empty() {
                    self.text(
                        &mut card,
                        (105, yy + 20),
                        "缺少升级材料表，仅显示等级",
                        16.,
                        MUTED,
                        false,
                    );
                }
                let icons = self
                    .resources(costs.iter().map(material_key).collect(), (46, 46))
                    .await?;
                for (i, (cost, icon)) in costs.iter().zip(icons).enumerate() {
                    let x = 105 + 155 * i as i32;
                    let qty = id(cost, "quantity");
                    let text = if has_suite {
                        format!("{}/{qty}", own(cost))
                    } else {
                        format!("x{qty}")
                    };
                    paste_rgba(&mut card, &icon, x, yy + 4);
                    self.text(
                        &mut card,
                        (x + 52, yy + 14),
                        &text,
                        16.,
                        if !has_suite {
                            TEXT
                        } else if can_afford(cost) {
                            OK
                        } else {
                            WARN
                        },
                        true,
                    );
                }
            }
            self.push(&mut canvas, &card, &mut y, 18);
        }
        self.watermark(&mut canvas);
        Ok(canvas)
    }

    pub(super) async fn compose_musicrecord(&self) -> Result<RgbaImage> {
        let groups = data::music_records(&self.master())?;
        let owned = data::records_owned(self.info());
        let show_id = self.flag("show_id");
        let cell_height = if show_id { 92 } else { 76 };
        let count =
            |items: &[(i64, i64)]| items.iter().filter(|(rid, _)| owned.contains(rid)).count();
        let total = groups.iter().map(Vec::len).sum();
        let got = groups.iter().map(|items| count(items)).sum();
        let mut canvas = self.canvas(
            328 + groups
                .iter()
                .filter(|v| !v.is_empty())
                .map(|v| 68 + v.len().div_ceil(10) as i32 * cell_height)
                .sum::<i32>(),
        );
        let mut y = PAD;
        self.header(&mut canvas, &mut y, "MySekai 唱片", &progress(got, total))
            .await?;
        let badge = self.assets.picture("music_record.png", Some((22, 22)));
        for ((_, title), items) in UNITS
            .into_iter()
            .zip(groups)
            .filter(|(_, items)| !items.is_empty())
        {
            let mut card = self.title_card(
                50 + items.len().div_ceil(10) as i32 * cell_height,
                &format!("{title}  {}/{}", count(&items), items.len()),
            );
            let icons = self
                .resources(
                    items
                        .iter()
                        .map(|(rid, _)| format!("mysekai_music_record_{rid}"))
                        .collect(),
                    (58, 58),
                )
                .await?;
            for (i, ((rid, mid), icon)) in items.into_iter().zip(icons).enumerate() {
                let (x, yy) = grid(i, 10, (22, 50), (100, cell_height));
                paste_rgba(&mut card, &icon, x, yy);
                if !owned.contains(&rid) {
                    shade(&mut card, (x, yy), (58, 58), 145);
                } else if let Some(badge) = &badge {
                    paste_rgba(&mut card, badge, x + 36, yy + 36);
                }
                if show_id {
                    self.text(&mut card, (x, yy + 60), &mid.to_string(), 10., MUTED, false);
                }
            }
            self.push(&mut canvas, &card, &mut y, 18);
        }
        self.watermark(&mut canvas);
        Ok(canvas)
    }

    pub(super) async fn compose_material(&self) -> Result<RgbaImage> {
        let all = self.flag("show_all");
        let items = data::materials(&self.master(), self.suite(), all);
        let height = items.len().max(1).div_ceil(4) as i32 * 92 + 70;
        let mut canvas = self.canvas(346 + height);
        let mut y = PAD;
        self.header(
            &mut canvas,
            &mut y,
            "MySekai 材料",
            if all { "全部记录" } else { "持有材料" },
        )
        .await?;
        let mut card = self.title_card(
            height,
            if all {
                "材料列表（全部记录）"
            } else {
                "材料列表"
            },
        );
        let icons = self
            .resources(
                items
                    .iter()
                    .map(|item| format!("mysekai_material_{}", item.id))
                    .collect(),
                (54, 54),
            )
            .await?;
        if items.is_empty() {
            self.text(&mut card, (24, 70), "没有可展示的材料记录", 20., WARN, true);
        }
        for (i, (item, icon)) in items.into_iter().zip(icons).enumerate() {
            let (x, yy) = grid(i, 4, (20, 60), (255, 92));
            paste_rgba(&mut card, &icon, x, yy + 8);
            self.text(
                &mut card,
                (x + 64, yy + 10),
                &self.fit(&item.name, 15., 160.),
                15.,
                MUTED,
                false,
            );
            self.title(&mut card, (x + 64, yy + 36), &format!("x{}", item.quantity));
        }
        self.push(&mut canvas, &card, &mut y, 18);
        self.watermark(&mut canvas);
        Ok(canvas)
    }

    pub(super) async fn compose_talk_list(&self) -> Result<RgbaImage> {
        let master = self.master();
        let talk = match data::talk_collection(
            &master,
            self.suite(),
            id(self.payload, "cuid"),
            self.flag("show_all_talks"),
        ) {
            Ok(talk) => talk,
            Err(error @ DrawError::MasterDataNotFound(_)) => {
                let table = [
                    "mysekaiCharacterTalkConditions",
                    "mysekaiCharacterTalkConditionGroups",
                    "mysekaiCharacterTalks",
                ]
                .into_iter()
                .find(|table| error.to_string().contains(&format!("{table}.json")));
                let Some(table) = table else {
                    return Err(error);
                };
                let mut canvas = self.canvas(360);
                self.text(
                    &mut canvas,
                    (PAD, PAD),
                    "MySekai 对话进度",
                    28.,
                    ACCENT,
                    true,
                );
                self.text(
                    &mut canvas,
                    (PAD, PAD + 60),
                    &format!("缺少 {table} 数据，请等待远程同步后重试"),
                    20.,
                    WARN,
                    false,
                );
                return Ok(canvas);
            }
            Err(error) => return Err(error),
        };
        let owned = data::obtained_fixtures(&master, self.info());
        let total = talk
            .single
            .values()
            .chain(talk.multi.values())
            .map(|row| row.total)
            .sum::<usize>();
        let read = talk
            .single
            .values()
            .chain(talk.multi.values())
            .map(|row| row.read)
            .sum();
        let mut single = BTreeMap::<_, Vec<_>>::new();
        for (fids, count) in talk
            .single
            .iter()
            .filter(|(fids, count)| !fids.is_empty() && count.total != count.read)
        {
            let genre = data::main_genre(&master.get("mysekaiFixtures", fids[0]));
            single.entry(genre).or_default().push((fids, count));
        }
        let multi: Vec<_> = talk
            .multi
            .iter()
            .filter(|(_, count)| count.total != count.read)
            .collect();
        let has_profile = self.flag("profile");
        let height = 204
            + if has_profile { 248 } else { 0 }
            + if single.is_empty() {
                72
            } else {
                single
                    .values()
                    .map(|v| 66 + v.len().div_ceil(12) as i32 * 110)
                    .sum()
            }
            + if multi.is_empty() {
                72
            } else {
                multi.len() as i32 * 92
            };
        let mut canvas = self.canvas(height);
        let mut y = PAD;
        if has_profile {
            let subtitle = if total == 0 {
                "全部已读".into()
            } else {
                progress(read, total)
            };
            self.header(&mut canvas, &mut y, "对话进度", &subtitle)
                .await?;
        }
        self.push(
            &mut canvas,
            &self.title_card(60, "单人对话家具（未读）"),
            &mut y,
            12,
        );
        if single.is_empty() {
            self.read_card(&mut canvas, &mut y);
        }
        for (main, mut items) in single {
            items.sort_by_key(|(fids, _)| (Reverse(fids.len()), fids[0]));
            let mut card = self.title_card(
                50 + items.len().div_ceil(12) as i32 * 110,
                &master.genre(main, true),
            );
            for (i, (fids, count)) in items.into_iter().enumerate() {
                let (x, yy) = grid(i, 12, (22, 50), (88, 110));
                self.talk_fixtures(&mut card, (x, yy), &fids[..fids.len().min(3)], &owned)
                    .await?;
                let unread = count.total - count.read;
                if unread > 1 {
                    self.text(
                        &mut card,
                        (x + 50, yy - 4),
                        &format!("x{unread}"),
                        14.,
                        WARN,
                        true,
                    );
                }
                self.text(
                    &mut card,
                    (x, yy + 50),
                    &fids
                        .iter()
                        .take(2)
                        .map(i64::to_string)
                        .collect::<Vec<_>>()
                        .join(" "),
                    11.,
                    MUTED,
                    false,
                );
            }
            self.push(&mut canvas, &card, &mut y, 16);
        }
        self.push(
            &mut canvas,
            &self.title_card(60, "多人对话家具（未读）"),
            &mut y,
            12,
        );
        if multi.is_empty() {
            self.read_card(&mut canvas, &mut y);
        }
        for (fids, count) in multi {
            let mut card = self.card(80);
            let fids = &fids[..fids.len().min(5)];
            self.talk_fixtures(&mut card, (18, 16), fids, &owned)
                .await?;
            self.text(
                &mut card,
                (36 + 22 * fids.len() as i32, 26),
                &format!("未读 x{}", count.total - count.read),
                20.,
                WARN,
                true,
            );
            let mut x = 360;
            for units in &count.unread_units {
                for unit in units {
                    let image = self.character(self.character_id(*unit), (40, 40), false);
                    paste_rgba(&mut card, &image, x, 18);
                    x += 44;
                }
                x += 12;
            }
            self.push(&mut canvas, &card, &mut y, 12);
        }
        self.watermark(&mut canvas);
        Ok(canvas)
    }

    async fn talk_fixtures(
        &self,
        card: &mut RgbaImage,
        (x, y): (i32, i32),
        ids: &[i64],
        owned: &std::collections::HashSet<i64>,
    ) -> Result<()> {
        let master = self.master();
        let icons = self
            .fixtures(
                ids.iter()
                    .map(|id| (master.get("mysekaiFixtures", *id), 0))
                    .collect(),
                (44, 44),
            )
            .await?;
        for (i, (fid, icon)) in ids.iter().zip(icons).enumerate() {
            let x = x + i as i32 * 22;
            paste_rgba(card, &icon, x, y);
            if !owned.contains(fid) {
                shade(card, (x, y), (44, 44), 100);
            }
        }
        Ok(())
    }

    fn read_card(&self, canvas: &mut RgbaImage, y: &mut i32) {
        let mut card = self.card(60);
        self.text(&mut card, (18, 18), "全部已读", 22., OK, true);
        self.push(canvas, &card, y, 12);
    }

    pub(super) async fn compose_map(&self) -> Result<RgbaImage> {
        let maps = data::maps(self.info());
        let ctx = self.ctx.clone();
        let gfx = self.gfx.clone();
        let assets = self.assets.clone();
        let region = self.region;
        let show_harvested = self.flag("show_harvested");
        let owned = Arc::new(data::records_owned(self.info()));
        let jobs = SITES.into_iter().filter_map(|sid| {
            let site = maps
                .iter()
                .find(|row| id(row, "mysekaiSiteId") == sid)?
                .clone();
            let (ctx, gfx, assets, owned) =
                (ctx.clone(), gfx.clone(), assets.clone(), owned.clone());
            Some(async move {
                View::new(&ctx, &gfx, assets, &Value::Null, region)
                    .site_map(sid, &site, show_harvested, &owned)
                    .await
            })
        });
        let mut images = load_ordered(jobs, 4)
            .await?
            .into_iter()
            .collect::<Result<Vec<_>>>()?;
        if images.is_empty() {
            images.push((*self.assets.placeholder((480, 320), "无地图")).clone());
        }
        let mut widths = [0; 2];
        let mut heights = [0; 2];
        for (index, image) in images.iter().enumerate() {
            widths[index % 2] = widths[index % 2].max(image.width());
            heights[index / 2] = heights[index / 2].max(image.height());
        }
        let mut canvas = RgbaImage::from_pixel(
            32 + widths.iter().sum::<u32>() + 16 * u32::from(images.len() > 1),
            32 + heights.iter().sum::<u32>() + 16 * u32::from(images.len() > 2),
            rgba([18, 30, 110], 255),
        );
        for (i, image) in images.into_iter().enumerate() {
            paste_rgba(
                &mut canvas,
                &image,
                if i % 2 == 0 {
                    16
                } else {
                    32 + widths[0] as i32
                },
                if i < 2 { 16 } else { 32 + heights[0] as i32 },
            );
        }
        Ok(canvas)
    }

    async fn site_map(
        &self,
        sid: i64,
        site: &Value,
        show: bool,
        owned: &std::collections::HashSet<i64>,
    ) -> Result<RgbaImage> {
        let (file, grid, ox, oz, swap) = match sid {
            5 => ("grassland", 33.333, 0., -40., true),
            7 => ("flowergarden", 24.806, -62.015, 20.672, true),
            6 => ("beach", 20.513, 0., 80., false),
            _ => ("memorialplace", 21.333, 0., -106.667, false),
        };
        let scale = 0.4;
        let raw = if let Some(image) = self.assets.picture(&format!("site/{file}.png"), None) {
            image
        } else {
            let path = format!("mysekai/site/sitemap/texture_rip/img_harvest_site_{sid}.png");
            self.assets.rip(&path, 0, None, false, "?").await
        };
        let (w, h) = (
            (raw.width() as f64 * scale).max(1.) as u32,
            (raw.height() as f64 * scale).max(1.) as u32,
        );
        let mut image = resize_lanczos(&raw, w, h);
        let pos = |x: f64, z: f64| {
            let (x, z, dx) = if swap { (z, x, -1.) } else { (x, z, 1.) };
            let px = (x * grid * scale * dx + f64::from(w) / 2. + ox * scale)
                .clamp(0., f64::from(w - 1));
            let py =
                (-z * grid * scale + f64::from(h) / 2. + oz * scale).clamp(0., f64::from(h - 1));
            (px as i32, py as i32)
        };
        let project = |row: &Value| pos(decimal(&row["positionX"]), decimal(&row["positionZ"]));
        let mut buckets: BTreeMap<_, BTreeMap<_, i64>> = BTreeMap::new();
        let mut keys = BTreeSet::new();
        for drop in data::drops(site, show) {
            let key = data::resource_key(drop);
            keys.insert(key.clone());
            let quantity = buckets
                .entry(project(drop))
                .or_default()
                .entry(key)
                .or_default();
            *quantity = quantity.saturating_add(effective_drop_quantity(drop));
        }
        let icons = self
            .resources(keys.iter().cloned().collect(), (64, 64))
            .await?;
        let icons: HashMap<_, _> = keys.into_iter().zip(icons).collect();
        let mut points: Vec<_> = rows(&site["userMysekaiSiteHarvestFixtures"])
            .iter()
            .filter(|point| show || point["userMysekaiSiteHarvestFixtureStatus"] == "spawned")
            .map(|point| (project(point), id(point, "mysekaiSiteHarvestFixtureId")))
            .collect();
        points.sort_by_key(|&((x, z), _)| (z, x));
        let unique_fids: Vec<i64> = {
            let mut seen = BTreeSet::new();
            points
                .iter()
                .filter_map(|&(_, fid)| seen.insert(fid).then_some(fid))
                .collect()
        };
        let point_icons: HashMap<i64, Option<(RgbaImage, i32, i32)>> = {
            let master = self.master();
            let rows: Vec<_> = unique_fids
                .iter()
                .map(|&fid| (fid, master.get("mysekaiSiteHarvestFixtures", fid)))
                .collect();
            let loaded = load_ordered(
                rows.into_iter().map(|(fid, row)| {
                    let (ctx, gfx, assets, region) = (
                        self.ctx.clone(),
                        self.gfx.clone(),
                        self.assets.clone(),
                        self.region,
                    );
                    async move {
                        let view = View::new(&ctx, &gfx, assets, &Value::Null, region);
                        let birthday = row["mysekaiSiteHarvestFixtureType"] == "birthday_plant";
                        let size = (if birthday { 50. } else { 160. } * scale) as i32;
                        let picture = view.harvest_icon(fid, &row).await.map(|icon| {
                            let ratio =
                                f64::from(size) / f64::from(icon.width().max(icon.height()));
                            let image = resize_lanczos(
                                &icon,
                                (f64::from(icon.width()) * ratio).max(8.) as u32,
                                (f64::from(icon.height()) * ratio).max(8.) as u32,
                            );
                            (
                                image,
                                if birthday {
                                    -(size as f64 * 0.35) as i32
                                } else {
                                    -size / 2
                                },
                                if birthday {
                                    -size / 2
                                } else {
                                    -(size as f64 * 0.8) as i32
                                },
                            )
                        });
                        (fid, picture)
                    }
                }),
                16,
            )
            .await?;
            loaded.into_iter().collect()
        };
        for ((x, z), fid) in points {
            if let Some((icon, dx, dz)) = point_icons.get(&fid).and_then(|opt| opt.as_ref()) {
                paste_rgba(&mut image, icon, x + dx, z + dz);
            }
        }
        let mark = self
            .assets
            .picture("mark.png", Some((8, 8)))
            .unwrap_or_else(|| self.assets.placeholder((8, 8), "?"));
        let (x, z) = pos(0., 0.);
        paste_rgba(&mut image, &mark, x - 4, z - 4);
        let mut calls = Vec::new();
        for ((x, z), bucket) in buckets {
            let cotton = bucket.contains_key("mysekai_material_21")
                || bucket.contains_key("mysekai_material_22");
            let materials = bucket
                .keys()
                .any(|key| key.starts_with("mysekai_material_"));
            let birthday =
                |key: &str| matches!(data::resource_parts(key), Some(("material", 174..=199)));
            let sapling = bucket.iter().any(|(key, qty)| birthday(key) && *qty > 16);
            let mut items: Vec<_> = bucket
                .into_iter()
                .filter_map(|(key, quantity)| {
                    if (matches!(key.as_str(), "mysekai_material_1" | "mysekai_material_6")
                        && quantity == 6)
                        || (!sapling && birthday(&key))
                    {
                        return None;
                    }
                    let small = if sapling {
                        !birthday(&key)
                    } else {
                        materials && !key.starts_with("mysekai_material_")
                            || cotton
                                && !matches!(
                                    key.as_str(),
                                    "mysekai_material_21" | "mysekai_material_22"
                                )
                    };
                    Some((key, quantity, small))
                })
                .collect();
            items.sort_by_key(|(key, qty, _)| {
                (Reverse(*qty), data::resource_parts(key).map(|(_, id)| id))
            });
            let large = items.iter().filter(|(_, _, small)| !small).count() as f64;
            let (mut small_index, mut large_index) = (0., 0.);
            for (key, quantity, small) in items {
                let rank = rarity(&key);
                let record = data::resource_parts(&key)
                    .filter(|(kind, _)| *kind == "mysekai_music_record")
                    .map(|(_, id)| id);
                let base = 35. * scale;
                let large_size = if record.is_some() || key == "mysekai_material_24" {
                    base * 1.5
                } else {
                    base
                };
                let small_size = f64::from(((17. * scale) as i32).max(6));
                let z_offset = -160. * scale * 0.2;
                let (px, py, size) = if small {
                    let point = (
                        f64::from(x) + 0.5 * base * large - 0.6 * small_size,
                        f64::from(z) - 0.45 * base + small_size * small_index + z_offset,
                        small_size as u32,
                    );
                    small_index += 1.;
                    point
                } else {
                    let point = (
                        f64::from(x) - 0.5 * large_size * large + large_size * large_index,
                        f64::from(z) - 0.5 * large_size + z_offset,
                        large_size.max(8.) as u32,
                    );
                    large_index += 1.;
                    point
                };
                let mut py = py as i32;
                if py <= 0 {
                    py += (0.5 * large_size) as i32;
                }
                let order = z * 100
                    + x
                    + if small {
                        1_000_000
                    } else if rank == 2 {
                        100_000
                    } else {
                        0
                    };
                let light = if rank == 2 && !key.starts_with("material") {
                    (45. * scale * if small { 1. } else { 1.4 }) as u32
                } else {
                    0
                };
                calls.push(MapIcon {
                    key,
                    quantity,
                    small,
                    size,
                    x: px as i32,
                    y: py,
                    order,
                    rank,
                    light,
                    badge: record.is_some_and(|id| owned.contains(&id)),
                });
            }
        }
        calls.sort_by_key(|call| call.order);
        let light = self.assets.picture("light.png", None);
        if let Some(light) = light {
            for call in calls.iter().filter(|call| call.light > 0) {
                let glow = resized(light.clone(), Some((call.light, call.light)));
                let offset = call.size as i32 / 2 - call.light as i32 / 2;
                paste_rgba(&mut image, &glow, call.x + offset, call.y + offset);
            }
        }
        for call in &calls {
            let icon = resized(icons[&call.key].clone(), Some((call.size, call.size)));
            paste_rgba(&mut image, &icon, call.x, call.y);
            if call.rank == 2 || call.small {
                let size = call.size as i32;
                let outline = if call.rank == 2 {
                    rgba([255, 50, 50], 200)
                } else {
                    rgba([50, 50, 255], 130)
                };
                rounded_panel_overlay(
                    self.gfx,
                    &mut image,
                    (call.x, call.y, call.x + size, call.y + size),
                    4,
                    Rgba([0; 4]),
                    Some(outline),
                );
            }
        }
        if let Some(badge) = self.assets.picture("music_record.png", None) {
            for call in calls.iter().filter(|call| call.badge) {
                let size = ((call.size as f64 * 0.6) as u32).max(10);
                let icon = resized(badge.clone(), Some((size, size)));
                let offset = call.size as i32 - (size as f64 * 0.7) as i32;
                paste_rgba(&mut image, &icon, call.x + offset, call.y + offset);
            }
        }
        for call in calls.into_iter().filter(|call| !call.small) {
            let (size, color) = match call.quantity {
                2 => (11, [200, 20, 0]),
                3.. => (11, [200, 20, 200]),
                _ => (10, [50; 3]),
            };
            let (dx, dy) = if call.quantity >= 10 {
                (1, call.size as i32 - size - 3)
            } else {
                (-1, -1)
            };
            self.text(
                &mut image,
                (call.x + dx, call.y + dy),
                &call.quantity.to_string(),
                size as f32,
                color,
                true,
            );
        }
        Ok(image)
    }

    async fn harvest_icon(&self, fid: i64, fixture: &Value) -> Option<Picture> {
        let asset = string(&fixture["assetbundleName"]);
        let rarity = string(&fixture["mysekaiSiteHarvestFixtureRarityType"]);
        if !asset.is_empty() && !rarity.is_empty() {
            let rel = format!("harvest_fixture_icon/{rarity}/{asset}.png");
            if let Some(icon) = self.assets.picture(&rel, None) {
                return Some(icon);
            }
            let path = format!("mysekai/{rel}");
            if let Some(icon) = self.assets.load(&path, self.region, None, false).await {
                return Some(icon);
            }
            self.assets.missing(self.region, &path, "missing");
        }
        if fixture["mysekaiSiteHarvestFixtureType"] == "birthday_plant" {
            return None;
        }
        let file = match fid {
            1001 => "oak",
            1002 => "pine",
            1003 => "palm",
            1004 => "luxury",
            2001 => "stone",
            2002 => "copper",
            2003 => "glass",
            2004 => "iron",
            2005 => "crystal",
            2006 => "diamond",
            3001 => "toolbox",
            6001 => "barrel",
            5001..=5004 | 5101..=5104 => "junk",
            _ => return None,
        };
        self.assets.picture(&format!("{file}.png"), None)
    }
}

struct MapIcon {
    key: String,
    quantity: i64,
    small: bool,
    size: u32,
    x: i32,
    y: i32,
    order: i32,
    rank: u8,
    light: u32,
    badge: bool,
}

fn grid(index: usize, columns: usize, (x, y): (i32, i32), (dx, dy): (i32, i32)) -> (i32, i32) {
    (
        x + (index % columns) as i32 * dx,
        y + (index / columns) as i32 * dy,
    )
}

fn shade(image: &mut RgbaImage, (x, y): (i32, i32), (w, h): Size, alpha: u8) {
    paste_rgba(
        image,
        &RgbaImage::from_pixel(w, h, Rgba([0, 0, 0, alpha])),
        x,
        y,
    );
}

fn progress(got: usize, total: usize) -> String {
    let fraction = if total == 0 {
        0.
    } else {
        100. * got as f64 / total as f64
    };
    format!("{got}/{total} ({fraction:.1}%)")
}

fn material_key(row: &Value) -> String {
    format!("mysekai_material_{}", id(row, "mysekaiMaterialId"))
}

fn parse_color(color: &str) -> Option<[u8; 3]> {
    let hex = color.trim_matches('#').as_bytes();
    let byte = |i| u8::from_str_radix(std::str::from_utf8(hex.get(i..i + 2)?).ok()?, 16).ok();
    Some([byte(0)?, byte(2)?, byte(4)?])
}
