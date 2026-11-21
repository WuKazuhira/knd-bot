use std::collections::{HashMap, HashSet};

use crate::core::{RenderRequestContext, Result};
use serde_json::Value;

use crate::components::{integer, string, truthy};

pub fn normalize_master_list(data: &Value) -> Vec<&Value> {
    match data {
        Value::Object(map) => map.values().collect(),
        Value::Array(rows) => rows.iter().collect(),
        _ => Vec::new(),
    }
}

pub fn get_event_card_ids(
    ctx: &RenderRequestContext,
    event_id: i64,
    pjsk_type: i32,
) -> Result<HashSet<i64>> {
    let rows = ctx
        .app
        .master_data
        .load_master_data("eventCards.json", pjsk_type)?;
    Ok(related_ids(&rows, event_id, "cardId")
        .map(|(_, id)| id)
        .collect())
}

pub fn get_event_music_ids(
    ctx: &RenderRequestContext,
    event_id: i64,
    pjsk_type: i32,
) -> Result<Vec<i64>> {
    let rows = ctx
        .app
        .master_data
        .load_master_data("eventMusics.json", pjsk_type)?;
    let mut matched: Vec<_> = related_ids(&rows, event_id, "musicId").collect();
    matched.sort_by(|(a, _), (b, _)| {
        a["seq"]
            .as_f64()
            .unwrap_or_default()
            .total_cmp(&b["seq"].as_f64().unwrap_or_default())
    });
    Ok(matched.into_iter().map(|(_, id)| id).collect())
}

fn related_ids<'a>(
    rows: &'a Value,
    event_id: i64,
    field: &'static str,
) -> impl Iterator<Item = (&'a Value, i64)> {
    normalize_master_list(rows)
        .into_iter()
        .filter_map(move |row| {
            (row["eventId"].as_i64() == Some(event_id) && truthy(&row[field]))
                .then(|| integer(&row[field]).map(|id| (row, id)))
                .flatten()
        })
}

pub fn get_ban_events_id_set(ctx: &RenderRequestContext, pjsk_type: i32) -> Result<HashSet<i64>> {
    let events = ctx
        .app
        .master_data
        .load_master_data("events.json", pjsk_type)?;
    let musics = ctx
        .app
        .master_data
        .load_master_data("eventMusics.json", pjsk_type)?;
    Ok(ban_event_ids(&events, &musics))
}

fn ban_event_ids(events: &Value, musics: &Value) -> HashSet<i64> {
    let with_music: HashSet<_> = normalize_master_list(musics)
        .into_iter()
        .filter(|row| truthy(&row["eventId"]))
        .filter_map(|row| integer(&row["eventId"]))
        .collect();
    normalize_master_list(events)
        .into_iter()
        .filter(|event| {
            matches!(
                string(&event["eventType"]),
                "marathon" | "cheerful_carnival"
            )
        })
        .filter_map(|event| event["id"].as_i64())
        .filter(|id| with_music.contains(id))
        .chain([74])
        .collect()
}

pub fn get_event_banner_chara_ids(
    ctx: &RenderRequestContext,
    pjsk_type: i32,
    cards: Option<&Value>,
    event_cards: Option<&Value>,
    card_supplies: Option<&Value>,
) -> Result<HashMap<i64, Option<i64>>> {
    let store = &ctx.app.master_data;
    let loaded_cards;
    let loaded_links;
    let loaded_supplies;
    let cards = match cards {
        Some(cards) => cards,
        None => {
            loaded_cards = store.load_master_data("cards.json", pjsk_type)?;
            &loaded_cards
        }
    };
    let links = match event_cards {
        Some(links) => links,
        None => {
            loaded_links = store.load_master_data("eventCards.json", pjsk_type)?;
            &loaded_links
        }
    };
    let supplies = match card_supplies {
        Some(supplies) => supplies,
        None => {
            loaded_supplies = store.load_master_data("cardSupplies.json", pjsk_type)?;
            &loaded_supplies
        }
    };
    Ok(banner_characters(cards, links, supplies))
}

fn banner_characters(cards: &Value, links: &Value, supplies: &Value) -> HashMap<i64, Option<i64>> {
    let index = |rows| {
        normalize_master_list(rows)
            .into_iter()
            .filter(|row| truthy(&row["id"]))
            .filter_map(|row| integer(&row["id"]).map(|id| (id, row)))
            .collect::<HashMap<_, _>>()
    };
    let cards = index(cards);
    let supplies = index(supplies);
    let mut banners = HashMap::<i64, (i64, Option<i64>)>::new();
    for link in normalize_master_list(links) {
        let (Some(event_id), Some(card_id)) = (link["eventId"].as_i64(), integer(&link["cardId"]))
        else {
            continue;
        };
        if !truthy(&link["cardId"]) {
            continue;
        }
        let Some(card) = cards.get(&card_id) else {
            continue;
        };
        let supply = card["cardSupplyId"]
            .as_i64()
            .and_then(|id| supplies.get(&id));
        if supply.is_some_and(|row| string(&row["cardSupplyType"]).contains("festival_limited")) {
            continue;
        }
        let candidate = (card_id, card["characterId"].as_i64());
        let banner = banners.entry(event_id).or_insert(candidate);
        if card_id < banner.0 {
            *banner = candidate;
        }
    }
    banners
        .into_iter()
        .map(|(event, (_, character))| (event, character))
        .collect()
}

pub fn get_event_banner_chara_id(
    ctx: &RenderRequestContext,
    event_id: i64,
    pjsk_type: i32,
) -> Result<Option<i64>> {
    Ok(
        get_event_banner_chara_ids(ctx, pjsk_type, None, None, None)?
            .get(&event_id)
            .copied()
            .flatten(),
    )
}

pub fn analysisunitid(
    ctx: &RenderRequestContext,
    unit_id: i64,
    game_character_units: Option<&Value>,
    pjsk_type: i32,
) -> Result<Option<(i64, String, String)>> {
    let loaded;
    let units = match game_character_units {
        Some(units) => units,
        None => {
            loaded = ctx
                .app
                .master_data
                .load_master_data("gameCharacterUnits.json", pjsk_type)?;
            &loaded
        }
    };
    Ok(analyze_unit(units, unit_id))
}

fn analyze_unit(units: &Value, id: i64) -> Option<(i64, String, String)> {
    let row = units
        .as_array()?
        .iter()
        .find(|row| row["id"].as_i64() == Some(id))?;
    let unit = string(&row["unit"]);
    let (character, unit, name) = if id <= 20 {
        (id, unit, format!("chr_ts_90_{id}.png"))
    } else {
        match row["gameCharacterId"].as_i64()? {
            21 if id == 21 => (21, "piapro", "chr_ts_90_21.png".to_owned()),
            21 => (21, unit, format!("chr_ts_90_21_{}.png", id - 25)),
            character => (character, unit, format!("chr_ts_90_{character}_2.png")),
        }
    };
    Some((character, unit.to_owned(), name))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn events_keep_sdl3_and_choose_lowest_non_festival_card() {
        assert_eq!(
            ban_event_ids(
                &json!([{"id":1,"eventType":"marathon"},{"id":2,"eventType":"world_bloom"},{"id":3,"eventType":"marathon"}]),
                &json!({"a":{"eventId":"1"},"b":{"eventId":2}}),
            ),
            HashSet::from([1, 74])
        );
        let cards = json!([{"id":1,"characterId":8,"cardSupplyId":3},{"id":4,"characterId":9},{"id":2,"characterId":10}]);
        let links =
            json!([{"eventId":7,"cardId":4},{"eventId":7,"cardId":1},{"eventId":7,"cardId":"2"}]);
        let supplies = json!([{"id":3,"cardSupplyType":"bloom_festival_limited"}]);
        assert_eq!(
            banner_characters(&cards, &links, &supplies),
            HashMap::from([(7, Some(10))])
        );
    }

    #[test]
    fn character_units_use_special_miku_variants() {
        let rows = json!([{"id":21,"gameCharacterId":21,"unit":"none"},{"id":27,"gameCharacterId":21,"unit":"street"}]);
        assert_eq!(analyze_unit(&rows, 21).unwrap().1, "piapro");
        assert_eq!(analyze_unit(&rows, 27).unwrap().2, "chr_ts_90_21_2.png");
        assert!(analyze_unit(&rows, 999).is_none());
    }
}
