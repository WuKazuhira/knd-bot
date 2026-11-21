use std::{
    collections::{BTreeMap, BTreeSet, HashMap, HashSet},
    sync::Arc,
};

use crate::components::normalize_master_list;
use crate::core::{DrawError, Result, context::AppContext};
use chrono::{DateTime, Datelike, Duration, Local, TimeZone, Timelike};
use serde_json::Value;

use super::{
    common::truthy,
    compact_schema::{effective_drop_quantity, integer},
};
use crate::renderers::common::{number, rows, string};

pub(super) const SITES: [i64; 4] = [5, 7, 6, 8];
pub(super) const SPECIAL: [i64; 4] = [5, 12, 20, 24];
pub(super) const UNITS: [(&str, &str); 7] = [
    ("light_sound", "Leo/need"),
    ("idol", "MORE MORE JUMP!"),
    ("street", "Vivid BAD SQUAD"),
    ("theme_park", "Wonderlands×Showtime"),
    ("school_refusal", "25時、ナイトコードで。"),
    ("vocaloid", "VIRTUAL SINGER"),
    ("other", "其他"),
];

pub(super) struct Master<'a> {
    pub(super) app: &'a AppContext,
    pub(super) region: i32,
}

impl Master<'_> {
    pub(super) fn load(&self, table: &str) -> Result<Arc<Value>> {
        self.app
            .master_data
            .load_master_data(&format!("{table}.json"), self.region)
    }

    pub(super) fn list(&self, table: &str) -> Vec<Value> {
        self.load(table)
            .map(|data| normalize_master_list(&data).into_iter().cloned().collect())
            .unwrap_or_default()
    }

    pub(super) fn required(&self, table: &str) -> Result<Vec<Value>> {
        let alias = match table {
            "mysekaiBlueprintMaterialCosts" => Some("mysekaiBlueprintMaterialCost"),
            "mysekaiGateMaterialGroups" => Some("mysekaiGateLevels"),
            "mysekaiMusicRecords" => Some("musics"),
            _ => None,
        };
        self.load(table)
            .or_else(|err| alias.map_or(Err(err), |alias| self.load(alias)))
            .map(|data| normalize_master_list(&data).into_iter().cloned().collect())
            .map_err(|_| {
                DrawError::MasterDataNotFound(format!(
                    "缺少或无法读取 MasterData：{table}.json\n请先更新 pjsk masterdata。"
                ))
            })
    }

    pub(super) fn get(&self, table: &str, id: i64) -> Value {
        self.app
            .master_data
            .master_data_by_id(&format!("{table}.json"), self.region, "id")
            .ok()
            .and_then(|index| index.get(&id.to_string()).cloned())
            .unwrap_or_default()
    }

    pub(super) fn name(&self, table: &str, id: i64, default: &str) -> String {
        self.get(table, id)["name"]
            .as_str()
            .filter(|name| !name.is_empty())
            .map_or_else(|| format!("{default}({id})"), str::to_owned)
    }

    pub(super) fn site_name(&self, id: i64) -> String {
        match self.load("mysekaiSites") {
            Ok(_) => self.get("mysekaiSites", id)["name"]
                .as_str()
                .map_or_else(|| format!("区域{id}"), str::to_owned),
            Err(_) => match id {
                5 => "森林".into(),
                7 => "山丘".into(),
                6 => "海边".into(),
                8 => "遗迹".into(),
                _ => format!("区域{id}"),
            },
        }
    }

    pub(super) fn genre(&self, id: i64, main: bool) -> String {
        self.name(
            if main {
                "mysekaiFixtureMainGenres"
            } else {
                "mysekaiFixtureSubGenres"
            },
            id,
            "分类",
        )
    }
}

pub(super) fn id(value: &Value, key: &str) -> i64 {
    number(&value[key])
}

pub(super) fn maps(info: &Value) -> &[Value] {
    rows(&info["updatedResources"]["userMysekaiHarvestMaps"])
}

pub(super) fn drops(site: &Value, show_harvested: bool) -> impl Iterator<Item = &Value> {
    rows(&site["userMysekaiSiteHarvestResourceDrops"])
        .iter()
        .filter(move |drop| {
            show_harvested || drop["mysekaiSiteHarvestResourceDropStatus"] == "before_drop"
        })
}

pub(super) fn resource_key(drop: &Value) -> String {
    format!(
        "{}_{}",
        drop["resourceType"].as_str().unwrap_or("None"),
        drop["resourceId"]
            .as_str()
            .map(str::to_owned)
            .unwrap_or_else(|| {
                if drop["resourceId"].is_null() {
                    "None".into()
                } else {
                    drop["resourceId"].to_string()
                }
            })
    )
}

pub(super) fn resource_parts(key: &str) -> Option<(&str, i64)> {
    let (kind, id) = key.rsplit_once('_')?;
    Some((kind, id.parse().ok()?))
}

pub(super) fn rarity(key: &str) -> u8 {
    match resource_parts(key) {
        Some(("mysekai_material", 32..=34 | 61 | 64..=66 | 93..=94))
        | Some(("mysekai_music_record", 0..=9999)) => 1,
        Some(("mysekai_material", 5 | 12 | 20 | 24 | 67..=92))
        | Some(("mysekai_fixture", 121))
        | Some(("material", 17 | 170 | 173..=203)) => 2,
        _ => 0,
    }
}

pub(super) fn summarize_resources(
    info: &Value,
    show_harvested: bool,
) -> BTreeMap<i64, BTreeMap<String, i64>> {
    let mut result: BTreeMap<_, BTreeMap<_, i64>> = SITES
        .into_iter()
        .map(|sid| (sid, BTreeMap::new()))
        .collect();
    for site in maps(info) {
        let totals = result.entry(id(site, "mysekaiSiteId")).or_default();
        for drop in drops(site, show_harvested) {
            let quantity = totals.entry(resource_key(drop)).or_default();
            *quantity = quantity.saturating_add(effective_drop_quantity(drop));
        }
    }
    result
}

pub(super) fn special_resources(info: &Value) -> BTreeMap<i64, (i64, Vec<i64>)> {
    let mut result: BTreeMap<i64, (i64, Vec<i64>)> = BTreeMap::new();
    for site in maps(info) {
        for drop in drops(site, false) {
            let material = id(drop, "resourceId");
            if drop["resourceType"] == "mysekai_material" && SPECIAL.contains(&material) {
                let (quantity, sites) = result.entry(material).or_default();
                *quantity = quantity.saturating_add(effective_drop_quantity(drop));
                if let Some(site) = site["mysekaiSiteId"].as_i64() {
                    if !sites.contains(&site) {
                        sites.push(site);
                    }
                }
            }
        }
    }
    result
}

pub(super) fn timestamp(value: &Value) -> Option<f64> {
    let time = value
        .as_f64()
        .or_else(|| value.as_str()?.trim().parse().ok())?;
    time.is_finite().then_some(if time > 10_000_000_000. {
        time / 1000.
    } else {
        time
    })
}

pub(super) fn local_time(time: f64) -> Option<DateTime<Local>> {
    Local.timestamp_opt(time as i64, 0).single()
}

fn hours(region: &str) -> (u32, u32) {
    if region == "cn" { (5, 17) } else { (4, 16) }
}

fn at_hour(time: DateTime<Local>, hour: u32) -> DateTime<Local> {
    time.date_naive()
        .and_hms_opt(hour, 0, 0)
        .and_then(|time| Local.from_local_datetime(&time).earliest())
        .unwrap_or(time)
}

pub(super) fn weather(
    info: &Value,
    region: &str,
    now: DateTime<Local>,
) -> (Option<i64>, Vec<(i64, DateTime<Local>)>) {
    let ids: Vec<_> = rows(&info["mysekaiPhenomenaSchedules"])
        .iter()
        .filter_map(|row| integer(&row["mysekaiPhenomenaId"]))
        .collect();
    let upload = timestamp(&info["upload_time"])
        .and_then(local_time)
        .unwrap_or(now);
    let (first, second) = hours(region);
    let overnight = upload.hour() < first;
    let index = usize::from(overnight || upload.hour() >= second).min(ids.len().saturating_sub(1));
    let current = ids.get(index).copied();
    let start = at_hour(upload, first) - Duration::days(i64::from(overnight));
    (
        current,
        ids.into_iter()
            .enumerate()
            .map(|(i, id)| (id, start + Duration::hours(12 * i as i64)))
            .collect(),
    )
}

const BIRTHDAYS: [(u32, u32); 26] = [
    (8, 11),
    (5, 9),
    (10, 27),
    (1, 8),
    (4, 14),
    (10, 5),
    (3, 19),
    (12, 6),
    (3, 2),
    (7, 26),
    (11, 12),
    (5, 25),
    (5, 17),
    (9, 9),
    (7, 20),
    (6, 24),
    (2, 10),
    (1, 27),
    (4, 30),
    (8, 27),
    (8, 31),
    (12, 27),
    (12, 27),
    (1, 30),
    (11, 5),
    (2, 17),
];

pub(super) fn last_refresh(
    region: &str,
    now: DateTime<Local>,
) -> (DateTime<Local>, Option<(i64, bool)>) {
    let (first, second) = hours(region);
    let hour = now.hour();
    let natural = if hour < first {
        at_hour(now - Duration::days(1), second)
    } else {
        at_hour(now, if hour < second { first } else { second })
    };
    if region == "jp" {
        let yesterday = now - Duration::days(1);
        for (i, (month, day)) in BIRTHDAYS.into_iter().enumerate() {
            let Some(mut end) = Local
                .with_ymd_and_hms(yesterday.year(), month, day, 0, 0, 0)
                .earliest()
            else {
                continue;
            };
            if end < yesterday {
                let Some(next) = end.with_year(end.year() + 1) else {
                    continue;
                };
                end = next;
            }
            for (time, starts) in [(end - Duration::days(3), true), (end, false)] {
                if natural < time && time <= now {
                    return (time, Some((i as i64 + 1, starts)));
                }
            }
        }
    }
    (natural, None)
}

pub(super) fn records_owned(info: &Value) -> HashSet<i64> {
    rows(&info["updatedResources"]["userMysekaiMusicRecords"])
        .iter()
        .filter_map(|record| positive_id(&record["mysekaiMusicRecordId"]))
        .collect()
}

fn positive_id(value: &Value) -> Option<i64> {
    let id = value.as_i64().or_else(|| {
        let s = value.as_str()?.trim();
        s.bytes()
            .all(|b| b.is_ascii_digit())
            .then(|| s.parse().ok())
            .flatten()
    })?;
    (id > 0).then_some(id)
}

pub(super) fn has_owned_refresh_music_record(info: &Value, refresh: f64, now: f64) -> bool {
    if !timestamp(&info["upload_time"]).is_some_and(|time| (refresh..=now).contains(&time)) {
        return false;
    }
    let owned = records_owned(info);
    maps(info)
        .iter()
        .flat_map(|site| drops(site, false))
        .any(|drop| {
            drop["resourceType"] == "mysekai_music_record"
                && (drop["quantity"].is_null() || positive_id(&drop["quantity"]).is_some())
                && positive_id(&drop["resourceId"]).is_some_and(|id| owned.contains(&id))
        })
}

pub(super) fn unit_group(master: &Master, group: i64) -> Vec<i64> {
    let row = master.get("mysekaiGameCharacterUnitGroups", group);
    (1..10)
        .filter_map(|n| integer(&row[format!("gameCharacterUnitId{n}")]))
        .collect()
}

pub(super) fn visitors(master: &Master, info: &Value) -> (Vec<(i64, i64)>, Option<i64>) {
    let visit = &info["userMysekaiGateCharacterVisit"];
    let mut seen = HashSet::new();
    let mut visitors = Vec::new();
    let mut reservation = None;
    for item in rows(&visit["userMysekaiGateCharacters"]) {
        let group = id(item, "mysekaiGameCharacterUnitGroupId");
        if group != 0 && seen.insert(group) {
            if let [unit] = unit_group(master, group).as_slice() {
                visitors.push((group, *unit));
                if truthy(&item["isReservation"]) {
                    reservation = Some(group);
                }
            }
        }
    }
    (visitors, reservation)
}

pub(super) fn fixture_blueprint(bp: &Value) -> bool {
    bp["mysekaiCraftType"] == "mysekai_fixture"
}

pub(super) fn obtained_fixtures(master: &Master, info: &Value) -> HashSet<i64> {
    rows(&info["updatedResources"]["userMysekaiBlueprints"])
        .iter()
        .filter_map(|item| {
            let bp = master.get("mysekaiBlueprints", id(item, "mysekaiBlueprintId"));
            fixture_blueprint(&bp).then(|| id(&bp, "craftTargetId"))
        })
        .collect()
}

pub(super) fn main_genre(fixture: &Value) -> i64 {
    integer(&fixture["mysekaiFixtureMainGenreId"]).unwrap_or(-1)
}

pub(super) struct Fixture {
    pub(super) row: Value,
    pub(super) owned: bool,
    pub(super) birthday: Option<i64>,
}

pub(super) type FixtureGroups = BTreeMap<i64, BTreeMap<i64, Vec<Fixture>>>;

pub(super) fn fixture_collection(
    master: &Master,
    info: &Value,
    craftable_only: bool,
) -> Result<FixtureGroups> {
    let fixtures = master.required("mysekaiFixtures")?;
    let owned = obtained_fixtures(master, info);
    let craftable: HashSet<_> = if craftable_only {
        master
            .required("mysekaiBlueprints")?
            .iter()
            .filter(|bp| fixture_blueprint(bp))
            .map(|bp| id(bp, "craftTargetId"))
            .collect()
    } else {
        HashSet::new()
    };
    let characters = master.required("gameCharacters")?;
    let mut groups = FixtureGroups::new();
    for row in fixtures {
        let id = id(&row, "id");
        if id == 0
            || row["mysekaiFixtureType"] == "gate"
            || craftable_only && !craftable.contains(&id)
        {
            continue;
        }
        let birthday = characters
            .iter()
            .find(|c| string(&row["name"]).ends_with(&format!("（{}）", string(&c["givenName"]))))
            .and_then(|c| integer(&c["id"]));
        let main = main_genre(&row);
        let sub = match (main, id) {
            (4 | 5 | 7..=13, _) => -1,
            (_, 4) => 14,
            _ => integer(&row["mysekaiFixtureSubGenreId"]).unwrap_or(-1),
        };
        groups
            .entry(main)
            .or_default()
            .entry(sub)
            .or_default()
            .push(Fixture {
                row,
                birthday,
                owned: !truthy(info) || owned.contains(&id),
            });
    }
    Ok(groups)
}

pub(super) fn quantities(suite: &Value, table: &str, key: &str, field: &str) -> HashMap<i64, i64> {
    rows(&suite[table])
        .iter()
        .map(|row| (id(row, key), id(row, field)))
        .collect()
}

pub(super) type GateGroups = BTreeMap<i64, BTreeMap<i64, Vec<Value>>>;

pub(super) fn gate_groups(master: &Master, filter: i64, summary: bool) -> Result<GateGroups> {
    let entries = master.required("mysekaiGateMaterialGroups")?;
    let alternate = !summary
        && entries
            .first()
            .is_some_and(|row| row.get("mysekaiGateMaterialGroupId").is_some());
    let mut groups = GateGroups::new();
    for row in entries.into_iter().filter(Value::is_object) {
        let (gate, level) = if alternate {
            (id(&row, "mysekaiGateId"), id(&row, "level"))
        } else {
            if summary && row["groupId"].is_null() {
                continue;
            }
            let group = id(&row, "groupId");
            (group.div_euclid(1000), group.rem_euclid(1000))
        };
        if filter == 0 || gate == filter {
            let costs = groups.entry(gate).or_default().entry(level).or_default();
            if !alternate {
                costs.push(row);
            }
        }
    }
    Ok(groups)
}

pub(super) fn music_records(master: &Master) -> Result<[Vec<(i64, i64)>; 7]> {
    let entries = master.required("mysekaiMusicRecords")?;
    let typed = entries
        .first()
        .is_some_and(|row| row.get("mysekaiMusicTrackType").is_some());
    let tags = master.list("musicTags");
    let vocals = master.list("musicVocals");
    let mut units = HashMap::new();
    for (list, field) in [(&tags, "musicTag"), (&vocals, "unit")] {
        for row in list {
            let unit = string(&row[field]);
            if !unit.is_empty() && !(field == "musicTag" && unit == "all") {
                units.entry(id(row, "musicId")).or_insert(unit);
            }
        }
    }
    let mut groups = std::array::from_fn(|_| Vec::new());
    for row in entries
        .iter()
        .filter(|row| row.is_object() && (!typed || row["mysekaiMusicTrackType"] == "music"))
    {
        let rid = id(row, "id");
        let mid = if typed && truthy(&row["externalId"]) {
            id(row, "externalId")
        } else {
            rid
        };
        let unit = units
            .get(&mid)
            .and_then(|unit| UNITS.iter().position(|(key, _)| key == unit))
            .unwrap_or(6);
        groups[unit].push((rid, mid));
    }
    Ok(groups)
}

pub(super) struct Material {
    pub(super) id: i64,
    pub(super) quantity: i64,
    pub(super) name: String,
}

pub(super) fn materials(master: &Master, suite: &Value, show_all: bool) -> Vec<Material> {
    let mut items: Vec<_> = rows(&suite["userMysekaiMaterials"])
        .iter()
        .filter_map(|row| {
            let quantity = id(row, "quantity");
            if quantity == 0 && !show_all {
                return None;
            }
            let id = id(row, "mysekaiMaterialId");
            let data = master.get("mysekaiMaterials", id);
            let memory = (35..=60)
                .contains(&id)
                .then(|| {
                    let (_, name) = string(&data["description"]).split_once('和')?;
                    let end = ["的回忆碎片", "的回憶碎片"]
                        .iter()
                        .filter_map(|suffix| name.find(suffix))
                        .min()?;
                    (!name[..end].contains('\n')).then(|| format!("{}的记忆", &name[..end]))
                })
                .flatten();
            let name = memory
                .or_else(|| data["name"].as_str().map(str::to_owned))
                .unwrap_or_else(|| format!("材料{id}"));
            Some((
                integer(&data["seq"]).unwrap_or(id),
                Material { id, quantity, name },
            ))
        })
        .collect();
    items.sort_by_key(|(seq, material)| (*seq, material.id));
    items.into_iter().map(|(_, material)| material).collect()
}

#[derive(Default)]
pub(super) struct TalkCount {
    pub(super) total: usize,
    pub(super) read: usize,
    pub(super) unread_units: BTreeSet<Vec<i64>>,
}

#[derive(Default)]
pub(super) struct Talks {
    pub(super) single: BTreeMap<Vec<i64>, TalkCount>,
    pub(super) multi: BTreeMap<Vec<i64>, TalkCount>,
}

pub(super) fn talk_collection(
    master: &Master,
    suite: &Value,
    cuid: i64,
    show_all: bool,
) -> Result<Talks> {
    let fixtures: HashSet<_> = master
        .required("mysekaiFixtures")?
        .iter()
        .filter(|row| row.is_object() && row["mysekaiFixtureType"] != "gate")
        .map(|row| id(row, "id"))
        .collect();
    let mut conditions: HashMap<i64, Vec<i64>> = HashMap::new();
    for row in master.required("mysekaiCharacterTalkConditions")? {
        let fid = id(&row, "mysekaiCharacterTalkConditionTypeValue");
        if row["mysekaiCharacterTalkConditionType"] == "mysekai_fixture_id"
            && fixtures.contains(&fid)
        {
            conditions.entry(id(&row, "id")).or_default().push(fid);
        }
    }
    let mut groups: HashMap<i64, BTreeSet<i64>> = HashMap::new();
    for row in master.required("mysekaiCharacterTalkConditionGroups")? {
        if let Some(fixtures) = conditions.get(&id(&row, "mysekaiCharacterTalkConditionId")) {
            groups.entry(id(&row, "id")).or_default().extend(fixtures);
        }
    }
    let mut archive = master.list("characterArchiveMysekaiCharacterTalkGroups");
    if archive.is_empty() {
        archive = master.list("mysekaiCharacterArchiveTalkGroups");
    }
    let hidden: HashSet<_> = archive
        .iter()
        .filter(|row| row.get("archiveDisplayType").is_some_and(|v| v != "normal"))
        .map(|row| id(row, "id"))
        .collect();
    let mut read = HashMap::new();
    if !show_all {
        for row in rows(&suite["userMysekaiCharacterTalks"]) {
            read.entry(id(row, "mysekaiCharacterTalkId"))
                .or_insert_with(|| truthy(&row["isRead"]));
        }
    }
    let mut archives: BTreeMap<Option<i64>, (BTreeSet<i64>, bool, Vec<i64>)> = BTreeMap::new();
    let mut unit_cache = HashMap::new();
    for row in master.required("mysekaiCharacterTalks")? {
        let Some(fixtures) = groups.get(&id(&row, "mysekaiCharacterTalkConditionGroupId")) else {
            continue;
        };
        let archive = integer(&row["characterArchiveMysekaiCharacterTalkGroupId"]);
        if archive.is_some_and(|id| hidden.contains(&id)) {
            continue;
        }
        let group = id(&row, "mysekaiGameCharacterUnitGroupId");
        let units = unit_cache
            .entry(group)
            .or_insert_with(|| unit_group(master, group));
        if !units.contains(&cuid) {
            continue;
        }
        let entry = archives
            .entry(archive)
            .or_insert_with(|| (BTreeSet::new(), false, units.clone()));
        entry.0.extend(fixtures);
        entry.1 |= read.get(&id(&row, "id")).copied().unwrap_or(false);
    }
    let mut result = Talks::default();
    for (_, (fixtures, read, units)) in archives {
        let target = if units.len() == 1 {
            &mut result.single
        } else {
            &mut result.multi
        };
        let entry = target.entry(fixtures.into_iter().collect()).or_default();
        entry.total += 1;
        entry.read += usize::from(read);
        if !read {
            entry.unread_units.insert(units);
        }
    }
    Ok(result)
}
