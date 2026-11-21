//! Shared drawing context and asset retrieval.

use std::{
    any::{Any, TypeId},
    collections::{HashMap, HashSet},
    num::NonZeroUsize,
    path::{Component, Path},
    sync::Arc,
    time::{Duration, Instant},
};

use lru::LruCache;
use parking_lot::{Mutex, RwLock};
use reqwest::{Client, StatusCode, redirect::Policy};
use serde_json::Value;
use tokio::sync::watch;

use crate::core::{
    AssetImage, AssetSourcePolicy, DrawError, Result, config::DrawConfig,
    master_data::MasterDataStore, paths::DrawPaths, resolve_server_name,
};

const ASSET_FAILURE_TTL: Duration = Duration::from_secs(30);

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
struct AssetKey {
    path: String,
    raw: String,
    pjsk_type: i32,
    download: bool,
}

type SharedImage = Arc<AssetImage>;
type AssetFlight = watch::Receiver<Option<Option<SharedImage>>>;

struct AssetCache {
    images: LruCache<AssetKey, SharedImage>,
    flights: HashMap<AssetKey, AssetFlight>,
    failures: HashMap<AssetKey, Instant>,
}

impl AssetCache {
    fn new() -> Self {
        Self {
            images: LruCache::new(NonZeroUsize::new(256).unwrap()),
            flights: HashMap::new(),
            failures: HashMap::new(),
        }
    }
}

#[derive(Clone)]
pub struct AppContext {
    pub paths: DrawPaths,
    pub config: DrawConfig,
    pub master_data: Arc<MasterDataStore>,
    client: Client,
    asset_cache: Arc<Mutex<AssetCache>>,
    extensions: Arc<RwLock<HashMap<TypeId, Arc<dyn Any + Send + Sync>>>>,
}

impl AppContext {
    pub fn new(paths: DrawPaths, config: DrawConfig) -> Result<Self> {
        let client = Client::builder()
            .no_proxy()
            .redirect(Policy::none())
            .pool_max_idle_per_host(16)
            .build()
            .map_err(|error| DrawError::Render(error.to_string()))?;
        Ok(Self {
            master_data: Arc::new(MasterDataStore::new(&paths, &config)),
            paths,
            config,
            client,
            asset_cache: Arc::new(Mutex::new(AssetCache::new())),
            extensions: Arc::new(RwLock::new(HashMap::new())),
        })
    }

    pub fn insert_extension<T: Send + Sync + 'static>(&self, value: Arc<T>) {
        self.extensions.write().insert(TypeId::of::<T>(), value);
    }

    pub fn extension<T: Send + Sync + 'static>(&self) -> Option<Arc<T>> {
        self.extensions
            .read()
            .get(&TypeId::of::<T>())
            .and_then(|value| Arc::clone(value).downcast::<T>().ok())
    }

    pub fn placeholder(paths: DrawPaths, config: DrawConfig) -> Self {
        Self::new(paths, config).expect("failed to initialize asset HTTP client")
    }

    pub fn server_name(&self, pjsk_type: i32) -> &str {
        resolve_server_name(&self.config.server_map, pjsk_type)
    }

    pub fn clear_caches(&self) {
        *self.asset_cache.lock() = AssetCache::new();
        self.master_data.clear_cache();
    }

    pub async fn get_asset(
        &self,
        path: &str,
        raw: &str,
        pjsk_type: i32,
        policy: AssetSourcePolicy,
        download: bool,
    ) -> Result<Option<SharedImage>> {
        match policy {
            AssetSourcePolicy::Default => {
                Ok(self.get_default_asset(path, raw, pjsk_type, download).await)
            }
            AssetSourcePolicy::LocalOnly => self
                .fetch_local_only_asset(path, raw, pjsk_type)
                .await
                .map(Some),
        }
    }

    pub async fn get_asset_cached(
        &self,
        path: &str,
        raw: &str,
        pjsk_type: i32,
        policy: AssetSourcePolicy,
        download: bool,
    ) -> Result<Option<SharedImage>> {
        if policy == AssetSourcePolicy::LocalOnly {
            return self.get_asset(path, raw, pjsk_type, policy, download).await;
        }

        let key = AssetKey {
            path: path.into(),
            raw: raw.into(),
            pjsk_type,
            download,
        };
        let mut flight = {
            let mut cache = self.asset_cache.lock();
            if let Some(image) = cache.images.get(&key) {
                return Ok(Some(Arc::clone(image)));
            }
            if cache
                .failures
                .get(&key)
                .is_some_and(|at| at.elapsed() < ASSET_FAILURE_TTL)
            {
                return Ok(None);
            }
            if let Some(flight) = cache
                .flights
                .get(&key)
                .filter(|flight| flight.has_changed().is_ok())
            {
                flight.clone()
            } else {
                let (sender, flight) = watch::channel(None);
                cache.flights.insert(key.clone(), flight.clone());
                let app = self.clone();
                let identity = flight.clone();
                // The fetch can finish even if its original caller is cancelled.
                tokio::spawn(async move {
                    let image = app
                        .get_default_asset(&key.path, &key.raw, key.pjsk_type, key.download)
                        .await;
                    let mut cache = app.asset_cache.lock();
                    if cache
                        .flights
                        .get(&key)
                        .is_some_and(|current| current.same_channel(&identity))
                    {
                        cache.flights.remove(&key);
                        if let Some(image) = &image {
                            cache.failures.remove(&key);
                            cache.images.put(key, Arc::clone(image));
                        } else {
                            cache
                                .failures
                                .retain(|_, at| at.elapsed() < ASSET_FAILURE_TTL);
                            cache.failures.insert(key, Instant::now());
                        }
                    }
                    sender.send_replace(Some(image));
                });
                flight
            }
        };
        let image = flight.wait_for(Option::is_some).await.map_err(|_| {
            DrawError::Render("Asset fetch worker exited before publishing a result".into())
        })?;
        Ok(image.as_ref().unwrap().clone())
    }

    pub async fn ensure_asset_path(
        &self,
        path: &str,
        raw: &str,
        pjsk_type: i32,
        download: bool,
    ) -> Option<std::path::PathBuf> {
        if [path, raw].into_iter().any(|part| {
            part.contains('\\')
                || Path::new(part)
                    .components()
                    .any(|component| !matches!(component, Component::Normal(_) | Component::CurDir))
        }) {
            return None;
        }
        let file = self
            .paths
            .ondemand_dir
            .join(self.server_name(pjsk_type))
            .join(path)
            .join(raw);
        if download && !tokio::fs::try_exists(&file).await.unwrap_or(false) {
            self.request_asset_download(path, raw, pjsk_type).await;
        }
        if tokio::fs::try_exists(&file).await.unwrap_or(false) {
            Some(file)
        } else {
            None
        }
    }

    async fn get_default_asset(
        &self,
        path: &str,
        raw: &str,
        pjsk_type: i32,
        download: bool,
    ) -> Option<SharedImage> {
        let file = self
            .ensure_asset_path(path, raw, pjsk_type, download)
            .await?;
        tokio::task::spawn_blocking(move || {
            let image = image::ImageReader::open(file)
                .ok()?
                .with_guessed_format()
                .ok()?
                .decode()
                .ok()?;
            Some(Arc::new(image.into_rgba8()))
        })
        .await
        .ok()
        .flatten()
    }

    async fn request_asset_download(&self, path: &str, raw: &str, pjsk_type: i32) {
        let base = self.config.helper_url.trim_end_matches('/');
        if base.is_empty() {
            return;
        }
        let response = self
            .client
            .post(format!("{base}/assets/fetch"))
            .query(&[
                ("region", self.server_name(pjsk_type)),
                ("path", path),
                ("raw", raw),
            ])
            .timeout(asset_timeout(self.config.asset_fetch_timeout_secs, 90))
            .send()
            .await;
        let response = match response {
            Ok(response) if response.status().is_success() => response,
            Ok(response) => {
                tracing::warn!(path, raw, status = %response.status(), "Asset helper request failed");
                return;
            }
            Err(error) => {
                tracing::warn!(path, raw, %error, "Asset helper request failed");
                return;
            }
        };
        match response.json::<Value>().await {
            Ok(payload) if truthy(&payload["ok"]) => {}
            Ok(payload) => {
                tracing::warn!(path, raw, error = %payload["error"], "Asset helper reported a download failure")
            }
            Err(error) => tracing::warn!(path, raw, %error, "Asset helper request failed"),
        }
    }

    async fn fetch_local_only_asset(
        &self,
        path: &str,
        raw: &str,
        pjsk_type: i32,
    ) -> Result<SharedImage> {
        let trimmed_path = path.trim_matches('/');
        let trimmed_raw = raw.trim_start_matches('/');
        let relative = if trimmed_path.is_empty() {
            trimmed_raw.to_string()
        } else {
            format!("{trimmed_path}/{trimmed_raw}")
        };
        let fail = |reason: String| DrawError::LocalOnlyAsset(format!("{relative}: {reason}"));
        if relative.split('/').any(|part| part == "..") {
            return Err(fail("invalid asset path".into()));
        }

        let server = self.server_name(pjsk_type);
        let base = self.config.servers_yaml[server]["rip"]["sources"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|source| source["name"].as_str() == Some("local-updater"))
            .filter_map(|source| source["base_url"].as_str())
            .map(|url| url.trim().trim_end_matches('/'))
            .find(|url| !url.is_empty())
            .ok_or_else(|| fail(format!("{server} has no local-updater asset source")))?;
        let encoded = relative
            .split('/')
            .map(|part| urlencoding::encode(part).into_owned())
            .collect::<Vec<_>>()
            .join("/");
        let response = self
            .client
            .get(format!("{base}/{encoded}"))
            .timeout(asset_timeout(self.config.local_asset_timeout_secs, 15))
            .send()
            .await
            .map_err(|error| fail(error.to_string()))?;
        let status = response.status();
        if status == StatusCode::NOT_FOUND {
            return Err(DrawError::LocalOnlyAssetNotFound(relative));
        }
        if !status.is_success() {
            return Err(fail(format!("local-updater returned HTTP {status}")));
        }
        let bytes = response
            .bytes()
            .await
            .map_err(|error| fail(error.to_string()))?;
        let image = tokio::task::spawn_blocking(move || {
            image::load_from_memory(&bytes).map(|image| image.into_rgba8())
        })
        .await
        .map_err(|error| fail(error.to_string()))?
        .map_err(|error| fail(format!("image decode failed: {error}")))?;
        Ok(Arc::new(image))
    }

    pub fn cardtype(&self, card_id: i64, card_costumes: &Value, costumes: &Value) -> i32 {
        let hair: HashSet<_> = rows(costumes)
            .filter(|item| item["partType"].as_str() == Some("hair"))
            .map(|item| &item["id"])
            .collect();
        i32::from(rows(card_costumes).any(|item| {
            item["cardId"].as_i64() == Some(card_id) && hair.contains(&item["costume3dId"])
        }))
    }

    pub fn is_fes_card(&self, card: &Value, card_supplies: Option<&Value>, pjsk_type: i32) -> bool {
        let cards;
        let card = if let Some(id) = card.as_i64() {
            let Ok(index) = self
                .master_data
                .master_data_by_id("cards.json", pjsk_type, "id")
            else {
                return false;
            };
            cards = index;
            let Some(card) = cards.get(&id.to_string()) else {
                return false;
            };
            card
        } else {
            card
        };
        let Some(supply_id) = card.get("cardSupplyId").filter(|id| truthy(id)) else {
            return false;
        };
        let loaded;
        let supplies = match card_supplies {
            Some(supplies) => supplies,
            None => {
                let Ok(data) = self
                    .master_data
                    .load_master_data("cardSupplies.json", pjsk_type)
                else {
                    return false;
                };
                loaded = data;
                &loaded
            }
        };
        rows(supplies)
            .find(|supply| supply.get("id") == Some(supply_id))
            .is_some_and(|supply| {
                matches!(
                    supply["cardSupplyType"].as_str(),
                    Some("colorful_festival_limited" | "bloom_festival_limited")
                )
            })
    }

    pub fn getcharaname(
        &self,
        character_id: i64,
        game_characters: Option<&Value>,
        pjsk_type: i32,
    ) -> Result<Option<String>> {
        let loaded;
        let characters = match game_characters {
            Some(characters) => characters,
            None => {
                loaded = self
                    .master_data
                    .load_master_data("gameCharacters.json", pjsk_type)?;
                &loaded
            }
        };
        Ok(rows(characters)
            .find(|item| item["id"].as_i64() == Some(character_id))
            .map(|item| {
                let first = item["firstName"].as_str().unwrap_or_default();
                let given = item["givenName"].as_str().unwrap_or_default();
                format!("{first}{given}")
            }))
    }
}

fn rows(data: &Value) -> impl Iterator<Item = &Value> {
    data.as_array()
        .into_iter()
        .flatten()
        .filter(|item| item.is_object())
}

pub(crate) fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::String(value) => !value.is_empty(),
        Value::Number(value) => value.as_f64() != Some(0.0),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn asset_timeout(seconds: f64, default: u64) -> Duration {
    Duration::try_from_secs_f64(seconds).unwrap_or_else(|_| Duration::from_secs(default))
}
