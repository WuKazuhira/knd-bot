mod common;
mod compact_schema;
mod data;
mod draw;

pub use self::common::MySekaiPaths;

use crate::core::{DrawError, RenderOutput, RenderRequestContext, Renderer, Result};
use crate::gfx::RenderContextGfxExt;
use async_trait::async_trait;
use serde_json::Value;

use self::{
    common::Assets,
    compact_schema::{integer, restore_compact_mysekai_data},
    draw::View,
};

#[derive(Clone, Copy)]
pub(super) enum MySekai {
    Summary,
    ResList,
    Map,
    FixtureList,
    FixtureDetail,
    Gate,
    MusicRecord,
    Material,
    TalkList,
}

impl MySekai {
    pub(super) const ALL: [Self; 9] = [
        Self::Summary,
        Self::ResList,
        Self::Map,
        Self::FixtureList,
        Self::FixtureDetail,
        Self::Gate,
        Self::MusicRecord,
        Self::Material,
        Self::TalkList,
    ];
}

#[async_trait]
impl Renderer for MySekai {
    fn name(&self) -> &'static str {
        match self {
            Self::Summary => "mysekai_summary",
            Self::ResList => "mysekai_res_list",
            Self::Map => "mysekai_map",
            Self::FixtureList => "mysekai_fixture_list",
            Self::FixtureDetail => "mysekai_fixture_detail",
            Self::Gate => "mysekai_gate",
            Self::MusicRecord => "mysekai_musicrecord",
            Self::Material => "mysekai_material",
            Self::TalkList => "mysekai_talk_list",
        }
    }

    async fn render(&self, ctx: &RenderRequestContext, mut payload: Value) -> Result<RenderOutput> {
        let gfx = ctx.gfx()?;
        let assets = Assets::new(ctx, gfx.clone(), &payload);
        let int = |key: &str, default| {
            payload.get(key).map_or(Ok(default), |value| {
                integer(value)
                    .ok_or_else(|| DrawError::InvalidPayload(format!("{key} must be an integer")))
            })
        };
        let region = i32::try_from(int("pjsk_type", 0)?)
            .map_err(|_| DrawError::InvalidPayload("pjsk_type out of range".into()))?;
        let quality = int("quality", 90)?.clamp(1, 100) as u8;
        if matches!(
            self,
            Self::Summary | Self::ResList | Self::Map | Self::TalkList
        ) {
            payload["mysekai_info"] = restore_compact_mysekai_data(payload["mysekai_info"].take())?;
        }
        if matches!(self, Self::TalkList) {
            payload["suite_data"] = restore_compact_mysekai_data(payload["suite_data"].take())?;
        }
        let view = View::new(ctx, &gfx, assets, &payload, region);
        let image = match self {
            Self::Summary => view.compose_summary().await?,
            Self::ResList => view.compose_res_list().await?,
            Self::Map => view.compose_map().await?,
            Self::FixtureList => view.compose_fixture_list().await?,
            Self::FixtureDetail => view.compose_fixture_detail().await?,
            Self::Gate => view.compose_gate().await?,
            Self::MusicRecord => view.compose_musicrecord().await?,
            Self::Material => view.compose_material().await?,
            Self::TalkList => view.compose_talk_list().await?,
        };
        view.encode(image, quality).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::registry::TaskRegistry;
    use crate::renderers::common::RenderFixture;
    use serde_json::json;

    fn masters(fixture: &RenderFixture) {
        for (table, entries) in [
            (
                "mysekaiFixtures",
                json!([
                    {"id": 4, "name": "Bench", "assetbundleName": "bench", "mysekaiFixtureMainGenreId": 1, "colorCode": "f39b77", "mysekaiFixtureAnotherColors": [{"colorCode": "abcdff"}]},
                    {"id": 6, "name": "Lamp", "assetbundleName": "lamp", "mysekaiFixtureMainGenreId": 1, "mysekaiFixtureSubGenreId": 1},
                    {"id": 9, "name": "Gate", "mysekaiFixtureType": "gate"}
                ]),
            ),
            (
                "mysekaiBlueprints",
                json!([{"id": 14, "mysekaiCraftType": "mysekai_fixture", "craftTargetId": 4}, {"id": 16,"mysekaiCraftType": "mysekai_fixture","craftTargetId": 6}]),
            ),
            (
                "mysekaiBlueprintMaterialCost",
                json!([{"mysekaiBlueprintId": 14, "mysekaiMaterialId": 5, "quantity": 3}]),
            ),
            (
                "mysekaiFixtureMainGenres",
                json!([{"id": 1,"name": "Furniture"}]),
            ),
            ("mysekaiFixtureSubGenres", json!([])),
            ("gameCharacters", json!([{"id": 1, "givenName": "Ichika"}])),
            (
                "gameCharacterUnits",
                json!([{"id": 11, "gameCharacterId": 1}]),
            ),
            (
                "mysekaiGateMaterialGroups",
                json!([{"groupId": 1002,"mysekaiMaterialId": 5,"quantity": 3}]),
            ),
            (
                "mysekaiMaterials",
                json!([{"id": 5, "name": "Wood", "iconAssetbundleName": "wood"}, {"id": 12,"name": "Gem"}]),
            ),
            (
                "mysekaiPhenomenas",
                json!([{"id": 1,"iconAssetbundleName": "sun"}]),
            ),
            (
                "mysekaiMusicRecords",
                json!([{"id": 8,"externalId": 7,"mysekaiMusicTrackType": "music"}, {"id": 9, "mysekaiMusicTrackType": "other"}]),
            ),
            (
                "musics",
                json!([{"id": 7,"assetbundleName": "jacket_test"}]),
            ),
            (
                "musicTags",
                json!([{"musicId": 7,"musicTag": "all"}, {"musicId": 7,"musicTag": "idol"}]),
            ),
            (
                "mysekaiCharacterTalkConditions",
                json!([
                    {"id": 1,"mysekaiCharacterTalkConditionType": "mysekai_fixture_id","mysekaiCharacterTalkConditionTypeValue": 4},
                    {"id": 2,"mysekaiCharacterTalkConditionType": "mysekai_fixture_id","mysekaiCharacterTalkConditionTypeValue": 6}
                ]),
            ),
            (
                "mysekaiCharacterTalkConditionGroups",
                json!([{"id": 3,"mysekaiCharacterTalkConditionId": 1}, {"id": 3,"mysekaiCharacterTalkConditionId": 2}]),
            ),
            (
                "mysekaiCharacterTalks",
                json!([
                    {"id": 1,"mysekaiCharacterTalkConditionGroupId": 3,"mysekaiGameCharacterUnitGroupId": 4,"characterArchiveMysekaiCharacterTalkGroupId": 5},
                    {"id": 2,"mysekaiCharacterTalkConditionGroupId": 3,"mysekaiGameCharacterUnitGroupId": 6,"characterArchiveMysekaiCharacterTalkGroupId": 7}
                ]),
            ),
            (
                "mysekaiGameCharacterUnitGroups",
                json!([{"id": 4,"gameCharacterUnitId1": 11}, {"id": 6,"gameCharacterUnitId1": 11,"gameCharacterUnitId2": 12}]),
            ),
        ] {
            fixture.write(&format!("ondemand/jp/{table}.json"), entries.to_string());
        }
        for (path, size) in [
            ("site/grassland.png", (250, 150)),
            ("oak.png", (28, 36)),
            ("mark.png", (10, 10)),
            ("thumbnail/fixture/bench_1.png", (40, 50)),
            ("bench_2.png", (40, 50)),
            ("lamp_1.png", (40, 50)),
            ("thumbnail/phenomena/sun.png", (50, 50)),
            ("item_preview/material/item_memoria_1.png", (50, 50)),
            ("site/sitemap/texture/img_harvest_site_5.png", (190, 92)),
            ("music_record.png", (50, 50)),
            ("gate_icon/gate_1.png", (50, 50)),
        ] {
            fixture.png(&format!("static/pics/mysekai/{path}"), size);
        }
    }

    fn payload() -> Value {
        json!({
            "profile": {"userid": 123, "name": "Sekai User", "rank": 42},
            "is_private": true, "quality": 72, "fast_render": true, "asset_wait_budget": 0,
            "mysekai_info": {
                "mysekaiPhenomenaSchedules": [{"mysekaiPhenomenaId": 1}],
                "userMysekaiGateCharacterVisit": {
                    "userMysekaiGate": {"mysekaiGateId": 1, "mysekaiGateLevel": 1},
                    "userMysekaiGateCharacters": [{"mysekaiGameCharacterUnitGroupId": 4, "isReservation": true}]
                },
                "updatedResources": {
                    "userMysekaiHarvestMaps": [[5, [[1001, 2, -3, 90, "spawned", null]], [
                        ["mysekai_material", 5, 0, 0, 31, 1, "before_drop", 1, null],
                        ["mysekai_material", 12, 0, 0, 31, 2, "dropped", 1, null]
                    ]]],
                    "userMysekaiBlueprints": [{"mysekaiBlueprintId": 14}],
                    "userMysekaiMusicRecords": [{"mysekaiMusicRecordId": 8}]
                }
            },
            "suite_data": {
                "userMysekaiMaterials": [{"mysekaiMaterialId": 5, "quantity": 7}, {"mysekaiMaterialId": 12, "quantity": 0}],
                "userMysekaiGates": [{"mysekaiGateId": 1, "mysekaiGateLevel": 1}],
                "userMysekaiCharacterTalks": [[1, true]]
            },
            "fids": ["4"], "only_craftable": true, "gate_id": 1, "show_id": true, "cuid": 11
        })
    }

    #[tokio::test]
    async fn registered_renderers_emit_jpeg_and_asset_metadata_offline() {
        let fixture = RenderFixture::new();
        masters(&fixture);
        let ctx = fixture.context();
        let mut registry = TaskRegistry::new();
        crate::renderers::register_all(&mut registry).unwrap();
        assert_eq!(
            registry
                .names()
                .iter()
                .filter(|name| name.starts_with("mysekai_"))
                .count(),
            9
        );
        let payload = payload();
        let material_path = "mysekai/thumbnail/material/wood.png";
        for (name, missing, dimensions) in [
            (
                "mysekai_summary",
                Some((material_path, "budget-exhausted")),
                (1100, 801),
            ),
            (
                "mysekai_res_list",
                Some((material_path, "budget-exhausted")),
                (1100, 440),
            ),
            (
                "mysekai_map",
                Some((material_path, "budget-exhausted")),
                (640, 228),
            ),
            ("mysekai_fixture_list", None, (1100, 583)),
            (
                "mysekai_fixture_detail",
                Some((material_path, "budget-exhausted")),
                (1100, 413),
            ),
            (
                "mysekai_gate",
                Some((material_path, "budget-exhausted")),
                (1100, 494),
            ),
            (
                "mysekai_musicrecord",
                Some(("mysekai_music_record_8", "remote-record-cover-missing")),
                (1100, 488),
            ),
            (
                "mysekai_material",
                Some((material_path, "budget-exhausted")),
                (1100, 508),
            ),
            ("mysekai_talk_list", None, (1100, 616)),
        ] {
            let output = registry
                .dispatch(ctx.app.clone(), name, payload.clone())
                .await
                .unwrap_or_else(|err| panic!("{name}: {err}"));
            assert_eq!(output.images.len(), 1, "{name}");
            assert_eq!(
                image::guess_format(&output.images[0]).unwrap(),
                image::ImageFormat::Jpeg,
                "{name}"
            );
            let decoded = image::load_from_memory(&output.images[0]).unwrap();
            assert_eq!((decoded.width(), decoded.height()), dimensions, "{name}");
            assert_eq!(output.meta["assets_complete"], missing.is_none(), "{name}");
            let missing = missing.map_or_else(
                || json!([]),
                |(path, reason)| {
                    json!([
                        {"key": format!("0:{path}"), "region": "jp", "path": path, "reason": reason}
                    ])
                },
            );
            assert_eq!(output.meta["missing_assets"], missing, "{name}");
        }
    }

    #[test]
    fn separates_owned_records_harvest_view_and_archive_progress() {
        let fixture = RenderFixture::new();
        masters(&fixture);
        let ctx = fixture.context();
        let master = data::Master {
            app: &ctx.app,
            region: 0,
        };
        let payload = payload();
        let mut info = restore_compact_mysekai_data(payload["mysekai_info"].clone()).unwrap();
        let totals = data::summarize_resources(&info, false);
        assert_eq!(totals[&5]["mysekai_material_5"], 1);
        assert!(!totals[&5].contains_key("mysekai_material_12"));
        assert_eq!(
            data::summarize_resources(&info, true)[&5]["mysekai_material_12"],
            1
        );
        info["upload_time"] = json!(1_760_000_000_000_i64);
        info["updatedResources"]["userMysekaiHarvestMaps"][0]["userMysekaiSiteHarvestResourceDrops"]
            [0]["resourceType"] = json!("mysekai_music_record");
        info["updatedResources"]["userMysekaiHarvestMaps"][0]["userMysekaiSiteHarvestResourceDrops"]
            [0]["resourceId"] = json!(8);
        assert!(data::has_owned_refresh_music_record(
            &info,
            1_759_999_999.,
            1_760_000_001.
        ));
        assert!(!data::has_owned_refresh_music_record(
            &info,
            1_760_000_001.,
            1_760_000_002.
        ));
        let suite = restore_compact_mysekai_data(payload["suite_data"].clone()).unwrap();
        let talk = data::talk_collection(&master, &suite, 11, false).unwrap();
        assert_eq!(talk.single[&vec![4, 6]].read, 1);
        assert_eq!(talk.multi[&vec![4, 6]].total, 1);
        assert!(talk.multi[&vec![4, 6]].unread_units.contains(&vec![11, 12]));
        let all = data::talk_collection(&master, &suite, 11, true).unwrap();
        assert_eq!(all.single[&vec![4, 6]].read, 0);
        assert_eq!(data::music_records(&master).unwrap()[1], [(8, 7)]);
        assert_eq!(data::materials(&master, &suite, false).len(), 1);
        assert_eq!(data::materials(&master, &suite, true).len(), 2);
    }
}
