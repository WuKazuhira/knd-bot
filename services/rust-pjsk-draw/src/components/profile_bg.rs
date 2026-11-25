use std::{
    collections::HashMap,
    fs::{self, File},
    io::BufReader,
    path::PathBuf,
    sync::{LazyLock, Mutex},
    time::SystemTime,
};

use crate::core::paths::DrawPaths;
use serde_json::{Map, Value};

struct CachedSettings {
    version: (SystemTime, u64),
    data: Map<String, Value>,
}

static SETTINGS: LazyLock<Mutex<HashMap<PathBuf, CachedSettings>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

pub fn get_user_bg_settings(paths: &DrawPaths, userid: &str, server: &str) -> Value {
    let path = paths.static_dir.join("profile_bg/settings.json");
    let key = format!("{server}:{userid}");
    let empty = || Value::Object(Map::new());
    let mut cache = SETTINGS.lock().unwrap_or_else(|error| error.into_inner());
    let Ok(metadata) = fs::metadata(&path) else {
        cache.remove(&path);
        return empty();
    };
    let Ok(modified) = metadata.modified() else {
        return empty();
    };
    let version = (modified, metadata.len());
    if let Some(entry) = cache.get(&path).filter(|entry| entry.version == version) {
        return entry.data.get(&key).cloned().unwrap_or_else(empty);
    }
    let Ok(file) = File::open(&path) else {
        return empty();
    };
    let Ok(value) = serde_json::from_reader::<_, Value>(BufReader::new(file)) else {
        return empty();
    };
    let data = match value {
        Value::Object(data) => data,
        _ => Map::new(),
    };
    let settings = data.get(&key).cloned().unwrap_or_else(empty);
    cache.insert(path, CachedSettings { version, data });
    settings
}

pub fn get_user_bg_path(paths: &DrawPaths, userid: &str, server: &str) -> Option<PathBuf> {
    if !matches!(server, "jp" | "cn" | "tw" | "en" | "kr")
        || userid.is_empty()
        || !userid.bytes().all(|byte| byte.is_ascii_digit())
    {
        return None;
    }
    Some(
        paths
            .static_dir
            .join("profile_bg")
            .join(server)
            .join(format!("{userid}.jpg")),
    )
}
