use std::sync::LazyLock;

use crate::core::{DrawError, Result};
use serde_json::{Map, Value};

static SCHEMA: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("mysekai_harvest_maps.avsc"))
        .expect("embedded harvest schema")
});

pub(super) fn integer(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(value) => Some(i64::from(*value)),
        Value::String(value) => value.trim().parse().ok(),
        _ => value.as_i64().or_else(|| Some(value.as_f64()? as i64)),
    }
}

pub(super) fn effective_drop_quantity(drop: &Value) -> i64 {
    integer(&drop["quantity"]).unwrap_or(1).max(0)
}

pub(super) fn restore_compact_mysekai_data(mut payload: Value) -> Result<Value> {
    if let Some(payload) = payload.as_object_mut() {
        for field in crate::renderers::common::rows(&SCHEMA["fields"]) {
            if let Some(value) = payload.get_mut(field["name"].as_str().unwrap()) {
                restore(value, &field["type"])?;
            }
        }
    }
    Ok(payload)
}

fn restore(value: &mut Value, spec: &Value) -> Result<()> {
    if let Some(branches) = spec.as_array() {
        if !value.is_null() {
            if let Some(branch) = branches.iter().find(|branch| *branch != "null") {
                restore(value, branch)?;
            }
        }
    } else if spec.is_object() {
        match spec["type"].as_str() {
            Some("record") => restore_record(value, spec)?,
            Some("array") if !value.is_null() => {
                let items = value.as_array_mut().ok_or_else(|| {
                    DrawError::InvalidPayload("AVSC array field must be a list".into())
                })?;
                for item in items {
                    restore(item, &spec["items"])?;
                }
            }
            None => restore(value, &spec["type"])?,
            _ => {}
        }
    }
    Ok(())
}

fn restore_record(value: &mut Value, spec: &Value) -> Result<()> {
    let fields = crate::renderers::common::rows(&spec["fields"]);
    let name = spec["name"].as_str().unwrap_or("record");
    if let Some(items) = value.as_array_mut() {
        let width = fields
            .iter()
            .filter_map(|f| f["msgpack_key"].as_u64())
            .max()
            .map_or(0, |key| key as usize + 1);
        if width != items.len() {
            return Err(DrawError::InvalidPayload(format!(
                "{name} compact width mismatch: schema expects {width}, received {}",
                items.len()
            )));
        }
        let mut record = Map::new();
        for field in fields {
            if let Some(index) = field["msgpack_key"].as_u64() {
                record.insert(
                    field["name"].as_str().unwrap().into(),
                    items[index as usize].take(),
                );
            }
        }
        *value = Value::Object(record);
    } else if let Some(record) = value.as_object_mut() {
        for field in fields {
            let name = field["name"].as_str().unwrap();
            let key = &field["msgpack_key"];
            let indexed = key
                .as_i64()
                .and_then(|index| record.get(&index.to_string()))
                .or_else(|| key.as_str().and_then(|key| record.get(key)));
            if let Some(item) = indexed.cloned() {
                record.insert(name.into(), item);
            }
        }
    } else {
        return Err(DrawError::InvalidPayload(format!(
            "{name} must be an object or compact array"
        )));
    }
    let record = value.as_object_mut().unwrap();
    for field in fields {
        let name = field["name"].as_str().unwrap();
        if !record.contains_key(name) {
            if let Some(default) = field.get("default") {
                record.insert(name.into(), default.clone());
            }
        }
        if let Some(item) = record.get_mut(name) {
            restore(item, &field["type"])?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn restores_maps_and_talks_without_reinterpreting_timing() {
        let restored = restore_compact_mysekai_data(json!({
            "extra": {"keep": 7},
            "updatedResources": {
                "userMysekaiHarvestMaps": [[5, [[1001, 2, -3, 90, "spawned", null]], [
                    ["mysekai_material", 12, 2, -3, 31, 1, "before_drop", 1, null],
                    {"0": "material", "1": 17, "resourceId": 3, "quantity": "9", "extra": true}
                ]]],
                "userMysekaiCharacterTalks": [[2, true]],
                "userMysekaiMusicRecords": [[123]]
            },
            "userMysekaiCharacterTalks": [{"0": 9, "1": false, "extra": "a"}]
        }))
        .unwrap();
        assert_eq!(restored["extra"], json!({"keep": 7}));
        let updated = &restored["updatedResources"];
        assert_eq!(updated["userMysekaiMusicRecords"], json!([[123]]));
        assert_eq!(
            updated["userMysekaiCharacterTalks"][0],
            json!({"mysekaiCharacterTalkId": 2, "isRead": true})
        );
        let site = &updated["userMysekaiHarvestMaps"][0];
        assert_eq!(site["mysekaiSiteId"], 5);
        let fixture = &site["userMysekaiSiteHarvestFixtures"][0];
        assert_eq!(fixture["positionZ"], -3);
        assert_eq!(fixture["userMysekaiSiteHarvestFixtureStatus"], "spawned");
        assert!(fixture["mysekaiSiteHarvestSpawnLimitedRelationGroupId"].is_null());
        let drops = &site["userMysekaiSiteHarvestResourceDrops"];
        assert_eq!(drops[0]["compactField4"], 31);
        assert_eq!(drops[0]["quantity"], 1);
        assert_eq!(effective_drop_quantity(&drops[1]), 9);
        assert_eq!(drops[1]["extra"], true);
        assert_eq!(drops[1]["resourceId"], 17);
        assert_eq!(restored["userMysekaiCharacterTalks"][0]["isRead"], false);
        assert_eq!(
            restore_compact_mysekai_data(restored.clone()).unwrap(),
            restored
        );
    }

    #[test]
    fn validates_record_width_and_array_types() {
        for (input, message) in [
            (
                json!({"updatedResources": {"userMysekaiHarvestMaps": [[5, []]]}}),
                "expects 3, received 2",
            ),
            (
                json!({"updatedResources": {"userMysekaiHarvestMaps": {}}}),
                "AVSC array field must be a list",
            ),
            (
                json!({"userMysekaiCharacterTalks": [null]}),
                "must be an object or compact array",
            ),
        ] {
            assert!(
                restore_compact_mysekai_data(input)
                    .unwrap_err()
                    .to_string()
                    .contains(message)
            );
        }
        for input in [
            json!(null),
            json!([]),
            json!({"userMysekaiCharacterTalks": null}),
        ] {
            assert_eq!(restore_compact_mysekai_data(input.clone()).unwrap(), input);
        }
    }

    #[test]
    fn normalizes_display_quantity() {
        for (quantity, expected) in [
            (json!(null), 1),
            (json!("invalid"), 1),
            (json!(false), 0),
            (json!(true), 1),
            (json!(" -4 "), 0),
            (json!(2.9), 2),
            (json!([]), 1),
        ] {
            assert_eq!(
                effective_drop_quantity(&json!({"quantity": quantity, "compactField4": 31})),
                expected
            );
        }
        assert_eq!(effective_drop_quantity(&json!([31])), 1);
    }
}
