use std::collections::{HashMap, HashSet};

use crate::components::card::{CardGridData, build_attr_grouped_image, build_unit_grouped_image};
use crate::core::{RenderOutput, RenderRequestContext, Renderer, Result};
use crate::gfx::RenderContextGfxExt;
use async_trait::async_trait;
use serde::Deserialize;
use serde_json::Value;

use crate::renderers::common::{deserialize_ids, parse_payload, render_error, render_jpeg};

#[derive(Clone, Copy)]
pub(crate) enum FindCard {
    Unit,
    Attribute,
}

#[derive(Default, Deserialize)]
#[serde(default)]
struct Payload {
    pjsk_type: i32,
    #[serde(deserialize_with = "deserialize_ids")]
    card_ids: Vec<i64>,
    #[serde(deserialize_with = "deserialize_ids")]
    ordered_chars: Vec<i64>,
    unit_internal: Option<String>,
    #[serde(deserialize_with = "deserialize_ids")]
    limited_card_ids: Vec<i64>,
    #[serde(deserialize_with = "deserialize_ids")]
    fes_card_ids: Vec<i64>,
}

fn badge_fields_present(payload: &Value) -> bool {
    payload.get("limited_card_ids").is_some() || payload.get("fes_card_ids").is_some()
}

#[async_trait]
impl Renderer for FindCard {
    fn name(&self) -> &'static str {
        match self {
            Self::Unit => "findcard",
            Self::Attribute => "findcard_by_attr",
        }
    }

    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput> {
        let badges_supplied = badge_fields_present(&payload);
        let payload: Payload = parse_payload(payload)?;
        let layout = *self;
        let gfx = ctx.gfx()?;
        let ctx = ctx.clone();
        let runtime = tokio::runtime::Handle::current();
        // The grid futures borrow card rows across a buffered stream. Keep
        // them on one thread and leave Tokio's blocking pool free for asset I/O.
        static GRID_SLOTS: tokio::sync::Semaphore = tokio::sync::Semaphore::const_new(8);
        let slot = GRID_SLOTS.acquire().await.map_err(render_error)?;
        let (send, receive) = tokio::sync::oneshot::channel();
        std::thread::Builder::new()
            .name("pjsk-findcard".into())
            .spawn(move || {
                let _slot = slot;
                let result = runtime.block_on(layout.draw_grid(&ctx, payload, badges_supplied));
                let _ = send.send(result);
            })?;
        let image = receive.await.map_err(render_error)??;
        render_jpeg(gfx, 85, false, |_| image).await
    }
}

impl FindCard {
    async fn draw_grid(
        self,
        ctx: &RenderRequestContext,
        payload: Payload,
        badges_supplied: bool,
    ) -> Result<image::RgbaImage> {
        let store = &ctx.app.master_data;
        let pjsk_type = payload.pjsk_type;
        let cards = store
            .load_master_data_async("cards.json", pjsk_type)
            .await?;
        let skills = store
            .load_master_data_async("skills.json", pjsk_type)
            .await?;
        let characters = store
            .load_master_data_async("gameCharacters.json", pjsk_type)
            .await?;
        let badge_tables = if badges_supplied {
            None
        } else {
            Some([
                store
                    .load_master_data_async("cardCostume3ds.json", pjsk_type)
                    .await?,
                store
                    .load_master_data_async("costume3ds.json", pjsk_type)
                    .await?,
                store
                    .load_master_data_async("cardSupplies.json", pjsk_type)
                    .await?,
            ])
        };

        let card_by_id = by_id(&cards);
        let skill_by_id = by_id(&skills);
        let target_cards: Vec<_> = payload
            .card_ids
            .iter()
            .filter_map(|id| card_by_id.get(id).map(|&card| card.clone()))
            .collect();
        let limited: HashSet<_> = payload.limited_card_ids.into_iter().collect();
        let fes: HashSet<_> = payload.fes_card_ids.into_iter().collect();
        let data = CardGridData {
            all_cards: Some(&cards),
            card_costumes: badge_tables.as_ref().map(|tables| tables[0].as_ref()),
            costumes: badge_tables.as_ref().map(|tables| tables[1].as_ref()),
            card_supplies: badge_tables.as_ref().map(|tables| tables[2].as_ref()),
            skills: Some(&skills),
            game_characters: Some(&characters),
            limited_card_ids: badges_supplied.then_some(&limited),
            fes_card_ids: badges_supplied.then_some(&fes),
            card_by_id: Some(&card_by_id),
            skill_by_id: Some(&skill_by_id),
            pjsk_type,
        };
        match self {
            Self::Unit => {
                build_unit_grouped_image(
                    ctx,
                    &target_cards,
                    payload
                        .unit_internal
                        .as_deref()
                        .filter(|unit| !unit.is_empty())
                        .unwrap_or("all"),
                    &payload.ordered_chars,
                    data,
                )
                .await
            }
            Self::Attribute => build_attr_grouped_image(ctx, &target_cards, data).await,
        }
    }
}

fn by_id(items: &Value) -> HashMap<i64, &Value> {
    items
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|item| Some((item["id"].as_i64()?, item)))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn supplied_badges_include_empty_and_null_fields() {
        assert!(!badge_fields_present(&json!({})));
        for payload in [
            json!({"limited_card_ids": []}),
            json!({"fes_card_ids": null}),
        ] {
            assert!(badge_fields_present(&payload));
            let parsed: Payload = parse_payload(payload).unwrap();
            assert!(parsed.limited_card_ids.is_empty());
            assert!(parsed.fes_card_ids.is_empty());
        }
    }
}
