use std::{
    fmt, fs,
    io::ErrorKind,
    num::NonZeroUsize,
    path::{Path, PathBuf},
    sync::Arc,
    time::SystemTime,
};

use lru::LruCache;
use parking_lot::Mutex;
use serde::de::{self, MapAccess, Visitor};
use serde_json::{Value, value::RawValue};

use crate::core::{
    DrawError, MasterIndex, Result, config::DrawConfig, paths::DrawPaths, resolve_server_name,
};

const WRAPPER_KEYS: [&str; 10] = [
    "data",
    "items",
    "list",
    "entries",
    "rankings",
    "masterData",
    "cards",
    "musics",
    "events",
    "skills",
];
const UNCACHED_MASTER_FILES: [&str; 1] = ["costume3ds.json"];
const MASTER_CACHE_BYTES: u64 = 64 << 20;

pub fn normalize_master_data(data: Value) -> Value {
    let Value::Object(mut map) = data else {
        return data;
    };
    for key in WRAPPER_KEYS {
        if map.get(key).is_some_and(is_container) {
            return match map.remove(key).unwrap() {
                Value::Object(inner) => normalize_object(inner),
                other => other,
            };
        }
    }
    normalize_object(map)
}

fn is_container(value: &Value) -> bool {
    value.is_array() || value.is_object()
}

fn normalize_object(entries: impl IntoIterator<Item = (String, Value)>) -> Value {
    let mut all_digits = true;
    let mut values = Vec::new();
    for (key, value) in entries {
        all_digits &= !key.is_empty() && key.bytes().all(|byte| byte.is_ascii_digit());
        values.push(value);
    }
    if !all_digits {
        let count = values.len();
        values.retain(is_container);
        if count > 1 && values.len() == 1 {
            return values.pop().unwrap();
        }
    }
    Value::Array(values)
}

// serde_json::Value 会按 key 排序，这里保留 JSON 原始顺序来取首尾 ID
struct OrderedObject<'a>(Vec<(String, &'a RawValue)>);

impl<'de> serde::Deserialize<'de> for OrderedObject<'de> {
    fn deserialize<D: de::Deserializer<'de>>(
        deserializer: D,
    ) -> std::result::Result<Self, D::Error> {
        struct ObjectVisitor;
        impl<'de> Visitor<'de> for ObjectVisitor {
            type Value = OrderedObject<'de>;

            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str("a JSON object")
            }

            fn visit_map<M: MapAccess<'de>>(
                self,
                mut map: M,
            ) -> std::result::Result<Self::Value, M::Error> {
                let mut entries = Vec::new();
                let mut positions = std::collections::HashMap::new();
                while let Some((key, value)) = map.next_entry::<String, &'de RawValue>()? {
                    let index = *positions.entry(key.clone()).or_insert(entries.len());
                    if index == entries.len() {
                        entries.push((key, value));
                    } else {
                        entries[index].1 = value;
                    }
                }
                Ok(OrderedObject(entries))
            }
        }
        deserializer.deserialize_map(ObjectVisitor)
    }
}

fn parse_master_data(bytes: &[u8]) -> serde_json::Result<Value> {
    let raw: &RawValue = serde_json::from_slice(bytes)?;
    let raw_kind = |value: &RawValue| {
        value
            .get()
            .trim_start()
            .as_bytes()
            .first()
            .copied()
            .unwrap_or(0)
    };
    if raw_kind(raw) != b'{' {
        return serde_json::from_str(raw.get());
    }
    let OrderedObject(mut entries) = serde_json::from_str(raw.get())?;
    for key in WRAPPER_KEYS {
        if let Some((_, value)) = entries
            .iter()
            .find(|(name, value)| name == key && matches!(raw_kind(value), b'[' | b'{'))
        {
            if raw_kind(value) == b'[' {
                return serde_json::from_str(value.get());
            }
            entries = serde_json::from_str::<OrderedObject<'_>>(value.get())?.0;
            break;
        }
    }
    let decoded = entries
        .into_iter()
        .map(|(key, value)| Ok((key, serde_json::from_str(value.get())?)))
        .collect::<serde_json::Result<Vec<_>>>()?;
    Ok(normalize_object(decoded))
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
enum DuplicatePolicy {
    First,
    Last,
}

pub fn index_by_id(items: &Value, key: &str) -> MasterIndex {
    build_index(items, key, DuplicatePolicy::Last)
}

pub fn index_first_by_id(items: &Value, key: &str) -> MasterIndex {
    build_index(items, key, DuplicatePolicy::First)
}

fn build_index(items: &Value, key: &str, policy: DuplicatePolicy) -> MasterIndex {
    let mut index = MasterIndex::new();
    for item in items.as_array().into_iter().flatten() {
        let Some(id) = item.get(key) else { continue };
        let id = match id {
            Value::String(id) => id.clone(),
            Value::Object(_) | Value::Array(_) => continue,
            id => id.to_string(),
        };
        match policy {
            DuplicatePolicy::First => {
                index.entry(id).or_insert_with(|| item.clone());
            }
            DuplicatePolicy::Last => {
                index.insert(id, item.clone());
            }
        }
    }
    index
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
struct FileVersion {
    modified: SystemTime,
    size: u64,
}

#[derive(Debug, Eq, Hash, PartialEq)]
struct IndexKey {
    path: PathBuf,
    version: FileVersion,
    field: String,
    policy: DuplicatePolicy,
}

struct Caches {
    files: LruCache<PathBuf, (FileVersion, Arc<Value>)>,
    bytes: u64,
    indexes: LruCache<IndexKey, Arc<MasterIndex>>,
}

pub struct MasterDataStore {
    ondemand_dir: PathBuf,
    server_map: std::collections::BTreeMap<i32, String>,
    caches: Mutex<Caches>,
}

impl MasterDataStore {
    pub fn new(paths: &DrawPaths, config: &DrawConfig) -> Self {
        Self {
            ondemand_dir: paths.ondemand_dir.clone(),
            server_map: config.server_map.clone(),
            caches: Mutex::new(Caches {
                files: LruCache::new(NonZeroUsize::new(24).unwrap()),
                bytes: 0,
                indexes: LruCache::new(NonZeroUsize::new(64).unwrap()),
            }),
        }
    }

    pub fn load_master_data(&self, filename: &str, pjsk_type: i32) -> Result<Arc<Value>> {
        let (path, version) = self.locate(filename, pjsk_type)?;
        let cacheable = !UNCACHED_MASTER_FILES.contains(&filename);
        if cacheable {
            let mut caches = self.caches.lock();
            if let Some(cached) = caches.get_file(&path, version) {
                return Ok(cached);
            }
        }
        // 锁外读盘解析，不卡并发请求
        let bytes = fs::read(&path).map_err(|error| file_error(&path, error))?;
        let data = Arc::new(parse_master_data(&bytes)?);
        if cacheable && bytes.len() as u64 == version.size {
            self.caches
                .lock()
                .put_file(path, version, Arc::clone(&data));
        }
        Ok(data)
    }

    pub fn master_data_by_id(
        &self,
        filename: &str,
        pjsk_type: i32,
        key: &str,
    ) -> Result<Arc<MasterIndex>> {
        self.cached_index(filename, pjsk_type, key, DuplicatePolicy::Last)
    }

    pub fn master_data_first_by_id(
        &self,
        filename: &str,
        pjsk_type: i32,
        key: &str,
    ) -> Result<Arc<MasterIndex>> {
        self.cached_index(filename, pjsk_type, key, DuplicatePolicy::First)
    }

    pub async fn load_master_data_async(
        self: &Arc<Self>,
        filename: &str,
        pjsk_type: i32,
    ) -> Result<Arc<Value>> {
        let store = Arc::clone(self);
        let filename = filename.to_owned();
        tokio::task::spawn_blocking(move || store.load_master_data(&filename, pjsk_type))
            .await
            .map_err(|error| DrawError::Render(error.to_string()))?
    }

    pub async fn master_data_by_id_async(
        self: &Arc<Self>,
        filename: &str,
        pjsk_type: i32,
        key: &str,
    ) -> Result<Arc<MasterIndex>> {
        let store = Arc::clone(self);
        let (filename, key) = (filename.to_owned(), key.to_owned());
        tokio::task::spawn_blocking(move || store.master_data_by_id(&filename, pjsk_type, &key))
            .await
            .map_err(|error| DrawError::Render(error.to_string()))?
    }

    pub async fn master_data_first_by_id_async(
        self: &Arc<Self>,
        filename: &str,
        pjsk_type: i32,
        key: &str,
    ) -> Result<Arc<MasterIndex>> {
        let store = Arc::clone(self);
        let (filename, key) = (filename.to_owned(), key.to_owned());
        tokio::task::spawn_blocking(move || {
            store.master_data_first_by_id(&filename, pjsk_type, &key)
        })
        .await
        .map_err(|error| DrawError::Render(error.to_string()))?
    }

    pub fn clear_cache(&self) {
        let mut caches = self.caches.lock();
        caches.files.clear();
        caches.indexes.clear();
        caches.bytes = 0;
    }

    fn locate(&self, filename: &str, pjsk_type: i32) -> Result<(PathBuf, FileVersion)> {
        let path = self
            .ondemand_dir
            .join(resolve_server_name(&self.server_map, pjsk_type))
            .join(filename);
        let metadata = fs::metadata(&path).map_err(|error| file_error(&path, error))?;
        let version = FileVersion {
            modified: metadata.modified()?,
            size: metadata.len(),
        };
        Ok((path, version))
    }

    fn cached_index(
        &self,
        filename: &str,
        pjsk_type: i32,
        field: &str,
        policy: DuplicatePolicy,
    ) -> Result<Arc<MasterIndex>> {
        let (path, version) = self.locate(filename, pjsk_type)?;
        let key = IndexKey {
            path,
            version,
            field: field.to_owned(),
            policy,
        };
        if let Some(index) = self.caches.lock().indexes.get(&key) {
            return Ok(Arc::clone(index));
        }
        let items = self.load_master_data(filename, pjsk_type)?;
        let index = Arc::new(build_index(&items, field, policy));
        if items.is_array() {
            self.caches.lock().indexes.put(key, Arc::clone(&index));
        }
        Ok(index)
    }
}

impl Caches {
    fn get_file(&mut self, path: &Path, version: FileVersion) -> Option<Arc<Value>> {
        self.files
            .get(path)
            .filter(|(old, _)| *old == version)
            .map(|(_, data)| Arc::clone(data))
    }

    fn put_file(&mut self, path: PathBuf, version: FileVersion, data: Arc<Value>) {
        if let Some((old, _)) = self.files.pop(&path) {
            self.bytes = self.bytes.saturating_sub(old.size);
        }
        if let Some((_, (evicted, _))) = self.files.push(path, (version, data)) {
            self.bytes = self.bytes.saturating_sub(evicted.size);
        }
        self.bytes = self.bytes.saturating_add(version.size);
        while self.bytes > MASTER_CACHE_BYTES {
            let Some((_, (evicted, _))) = self.files.pop_lru() else {
                self.bytes = 0;
                break;
            };
            self.bytes = self.bytes.saturating_sub(evicted.size);
        }
    }
}

fn file_error(path: &Path, error: std::io::Error) -> DrawError {
    if error.kind() == ErrorKind::NotFound {
        DrawError::MasterDataNotFound(path.display().to_string())
    } else {
        error.into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn normalizes_wrappers_and_metadata() {
        assert_eq!(
            normalize_master_data(json!({"items": [1], "data": {"meta": 0, "rows": [2]}})),
            json!([2])
        );
        assert_eq!(
            normalize_master_data(json!({"foo": {"x": 1}, "bar": 0})),
            json!({"x": 1})
        );
        assert_eq!(normalize_master_data(json!({"rows": [1]})), json!([[1]]));
        assert_eq!(
            parse_master_data(br#"{"data":{"20":{"id":2},"10":{"id":1}}}"#).unwrap(),
            json!([{"id":2},{"id":1}])
        );
    }

    #[test]
    fn indexes_first_and_last_by_requested_field() {
        let items = json!([{"k": 1, "v": "first"}, {}, null, {"k": 1, "v": "last"}]);
        assert_eq!(index_first_by_id(&items, "k")["1"]["v"], "first");
        assert_eq!(index_by_id(&items, "k")["1"]["v"], "last");
        assert!(index_by_id(&items, "id").is_empty());
    }
}
