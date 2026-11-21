use std::{
    collections::{BTreeMap, HashMap, HashSet},
    net::IpAddr,
    num::NonZeroUsize,
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Duration,
};

use crate::core::{AssetSourcePolicy, RenderRequestContext, context::AppContext};
use crate::gfx::{GfxContext, TextAnchor, draw_rounded_rect, resize_lanczos};
use image::{Rgba, RgbaImage};
use lru::LruCache;
use serde_json::{Value, json};
use tokio::{
    sync::{Semaphore, watch},
    time::{Instant, timeout, timeout_at},
};

use crate::renderers::common::{BOLD, label, rgba, rounded_panel_overlay, rows};

pub(super) const TEXT: [u8; 3] = [45, 45, 55];
pub(super) const MUTED: [u8; 3] = [110, 110, 120];
pub(super) const ACCENT: [u8; 3] = [88, 92, 118];
pub(super) const WARN: [u8; 3] = [220, 50, 50];
pub(super) const OK: [u8; 3] = [0, 170, 80];
pub struct MySekaiPaths {
    pub pictures: PathBuf,
    pub profile_cache: PathBuf,
    pub cn_msr_groups: PathBuf,
}

impl MySekaiPaths {
    pub fn new(paths: &crate::core::paths::DrawPaths) -> Self {
        let state_cn_msr = paths.ondemand_dir.join("state/cn_msr_allowed_groups.json");
        let legacy_cn_msr = paths.static_dir.join("cn_msr_allowed_groups.json");
        let cn_msr_groups = if state_cn_msr.exists() || !legacy_cn_msr.exists() {
            state_cn_msr
        } else {
            legacy_cn_msr
        };
        Self {
            pictures: paths.static_dir.join("pics/mysekai"),
            profile_cache: paths.ondemand_dir.join("profile/mysekai"),
            cn_msr_groups,
        }
    }
}
const TTL: Duration = Duration::from_secs(15);
const BODY_LIMIT: usize = 16 * 1024 * 1024;
type Picture = Arc<RgbaImage>;
pub(super) type Size = (u32, u32);

pub(super) use crate::core::context::truthy;

pub(super) struct MySekaiAssetRequest {
    fast: bool,
    deadline: Instant,
    limit: Semaphore,
    missing: Mutex<BTreeMap<String, Value>>,
}

impl MySekaiAssetRequest {
    pub(super) fn new(payload: &Value) -> Self {
        let budget = payload["asset_wait_budget"]
            .as_f64()
            .or_else(|| payload["asset_wait_budget"].as_str()?.trim().parse().ok())
            .or_else(|| {
                payload["asset_wait_budget"]
                    .as_bool()
                    .map(|v| f64::from(u8::from(v)))
            })
            .filter(|v| v.is_finite())
            .unwrap_or(5.)
            .clamp(0., 5.);
        let concurrency = super::compact_schema::integer(&payload["asset_fetch_concurrency"])
            .unwrap_or(8)
            .clamp(1, 8);
        Self {
            fast: truthy(&payload["fast_render"]),
            deadline: Instant::now() + Duration::from_secs_f64(budget),
            limit: Semaphore::new(concurrency as usize),
            missing: Mutex::default(),
        }
    }

    pub(super) fn metadata(&self) -> Value {
        let missing = self.missing.lock().unwrap();
        json!({"assets_complete": missing.is_empty(), "missing_assets": missing.values().collect::<Vec<_>>()})
    }

    fn exhausted(&self) -> bool {
        self.fast && Instant::now() >= self.deadline
    }

    pub(super) fn missing(&self, app: &AppContext, region: i32, path: &str, reason: &str) {
        let key = format!("{region}:{path}");
        self.missing.lock().unwrap().insert(
            key.clone(),
            json!({
                "key": key, "region": app.server_name(region), "path": path, "reason": reason
            }),
        );
    }
}

#[derive(Clone, Hash, PartialEq, Eq)]
struct Key {
    path: String,
    region: i32,
    remote_only: bool,
}

struct AssetCache {
    images: LruCache<(Key, Option<Size>), Picture>,
    negative: LruCache<Key, Instant>,
    flights: HashMap<Key, watch::Receiver<Option<Picture>>>,
}

struct SharedAssets {
    cache: Mutex<AssetCache>,
    limit: Semaphore,
}

impl Default for SharedAssets {
    fn default() -> Self {
        let capacity = NonZeroUsize::new(512).unwrap();
        Self {
            cache: Mutex::new(AssetCache {
                images: LruCache::new(capacity),
                negative: LruCache::new(capacity),
                flights: HashMap::new(),
            }),
            limit: Semaphore::new(8),
        }
    }
}

#[derive(Clone)]
pub(super) struct Assets {
    ctx: RenderRequestContext,
    gfx: Arc<GfxContext>,
    state: Arc<MySekaiAssetRequest>,
    shared: Arc<SharedAssets>,
}

impl Assets {
    pub(super) fn new(ctx: &RenderRequestContext, gfx: Arc<GfxContext>, payload: &Value) -> Self {
        static INIT: Mutex<()> = Mutex::new(());
        let _guard = INIT.lock().unwrap();
        let shared = ctx.app.extension().unwrap_or_else(|| {
            let cache = Arc::new(SharedAssets::default());
            ctx.app.insert_extension(cache.clone());
            cache
        });
        Self {
            ctx: ctx.clone(),
            gfx,
            shared,
            state: Arc::new(MySekaiAssetRequest::new(payload)),
        }
    }

    pub(super) fn metadata(&self) -> Value {
        self.state.metadata()
    }

    pub(super) fn missing(&self, region: i32, path: &str, reason: &str) {
        self.state.missing(&self.ctx.app, region, path, reason);
    }

    pub(super) fn picture(&self, path: &str, size: Option<Size>) -> Option<Picture> {
        let path = normalize(path)?;
        self.gfx
            .cache
            .open_pjsk_image(
                MySekaiPaths::new(&self.ctx.app.paths).pictures.join(path),
                size,
            )
            .ok()
    }

    pub(super) fn placeholder(&self, size: Size, text: &str) -> Picture {
        let mut image = RgbaImage::from_pixel(size.0, size.1, rgba([230, 230, 235], 255));
        draw_rounded_rect(
            &mut image,
            (1., 1., size.0 as f32 - 1., size.1 as f32 - 1.),
            (size.0 / 10).max(4) as f32,
            None,
            Some((rgba([180, 180, 190], 255), 2.)),
        );
        let font_size = (size.1 / 2).max(12) as f32;
        label(
            &self.gfx,
            &mut image,
            (
                size.0 as i32 / 2,
                (size.1 as f32 / 2. - font_size / 2. - 2.) as i32,
            ),
            text,
            (BOLD, font_size, [130, 130, 140], TextAnchor::Ma),
        );
        Arc::new(image)
    }

    pub(super) async fn rip(
        &self,
        path: &str,
        region: i32,
        size: Option<Size>,
        remote_only: bool,
        fallback: &str,
    ) -> Picture {
        if let Some(image) = self.load(path, region, size, remote_only).await {
            return image;
        }
        let reason = if self.state.exhausted() {
            "budget-exhausted"
        } else {
            "missing"
        };
        self.missing(region, normalize(path).as_deref().unwrap_or(path), reason);
        self.placeholder(size.unwrap_or((64, 64)), fallback)
    }

    pub(super) async fn load(
        &self,
        path: &str,
        region: i32,
        size: Option<Size>,
        remote_only: bool,
    ) -> Option<Picture> {
        let key = Key {
            path: normalize(path)?,
            region,
            remote_only,
        };
        let cached = {
            let mut cache = self.shared.cache.lock().unwrap();
            if let Some(image) = cache.images.get(&(key.clone(), size)) {
                return Some(image.clone());
            }
            cache.images.get(&(key.clone(), None)).cloned()
        };
        let raw = match cached {
            Some(image) => image,
            None => match self.probe(&key).await {
                Some(image) => {
                    self.shared
                        .cache
                        .lock()
                        .unwrap()
                        .images
                        .put((key.clone(), None), image.clone());
                    image
                }
                None => self.wait_for_remote(&key).await?,
            },
        };
        let image = if size.is_some() {
            tokio::task::spawn_blocking(move || resized(raw, size))
                .await
                .ok()?
        } else {
            raw
        };
        self.shared
            .cache
            .lock()
            .unwrap()
            .images
            .put((key, size), image.clone());
        Some(image)
    }

    async fn probe(&self, key: &Key) -> Option<Picture> {
        if key.remote_only {
            return None;
        }
        let candidates = local_candidates(&self.ctx.app, key);
        let gfx = self.gfx.clone();
        tokio::task::spawn_blocking(move || {
            candidates.into_iter().find_map(|path| {
                path.is_file()
                    .then(|| gfx.cache.open_pjsk_image(path, None).ok())
                    .flatten()
            })
        })
        .await
        .ok()
        .flatten()
    }

    async fn wait_for_remote(&self, key: &Key) -> Option<Picture> {
        if self.state.exhausted() || self.ctx.policy == AssetSourcePolicy::LocalOnly {
            return None;
        }
        let mut receiver = {
            let mut cache = self.shared.cache.lock().unwrap();
            if let Some(image) = cache.images.get(&(key.clone(), None)) {
                return Some(image.clone());
            }
            if cache
                .negative
                .get(key)
                .is_some_and(|expiry| *expiry > Instant::now())
            {
                return None;
            }
            cache.negative.pop(key);
            if let Some(receiver) = cache
                .flights
                .get(key)
                .filter(|flight| flight.has_changed().is_ok())
            {
                receiver.clone()
            } else {
                let (sender, receiver) = watch::channel(None);
                cache.flights.insert(key.clone(), receiver.clone());
                let assets = self.clone();
                let key = key.clone();
                tokio::spawn(async move {
                    let fetched = timeout(Duration::from_secs(10), assets.fetch(&key))
                        .await
                        .ok()
                        .flatten();
                    let image = match fetched {
                        Some(image) => Some(image),
                        None => assets.probe(&key).await,
                    };
                    let mut cache = assets.shared.cache.lock().unwrap();
                    if let Some(image) = &image {
                        cache.images.put((key.clone(), None), image.clone());
                        cache.negative.pop(&key);
                    } else {
                        cache.negative.put(key.clone(), Instant::now() + TTL);
                    }
                    sender.send_replace(image);
                    cache.flights.remove(&key);
                });
                receiver
            }
        };
        if self.state.fast {
            timeout_at(self.state.deadline, receiver.changed())
                .await
                .ok()?
                .ok()?;
        } else {
            receiver.changed().await.ok()?;
        }
        receiver.borrow().clone()
    }

    async fn fetch(&self, key: &Key) -> Option<Picture> {
        let _global = self.shared.limit.acquire().await.ok()?;
        let _request = self.state.limit.acquire().await.ok()?;
        if key.remote_only {
            return fetch_configured(&self.ctx.app, key).await;
        }
        let (parent, file) = key.path.rsplit_once('/').unwrap_or(("", &key.path));
        self.ctx
            .app
            .get_asset(parent, file, key.region, self.ctx.policy, true)
            .await
            .ok()
            .flatten()
    }
}

pub(super) fn resized(image: Picture, size: Option<Size>) -> Picture {
    match size {
        Some((w, h)) if image.dimensions() != (w, h) => Arc::new(resize_lanczos(&image, w, h)),
        _ => image,
    }
}

fn normalize(path: &str) -> Option<String> {
    let path = path.replace('\\', "/");
    let parts: Vec<_> = path
        .trim_matches('/')
        .split('/')
        .skip_while(|p| matches!(*p, "startapp" | "ondemand"))
        .collect();
    if parts.is_empty()
        || parts
            .iter()
            .any(|p| matches!(*p, "" | "." | "..") || p.contains([':', '\0']))
    {
        return None;
    }
    Some(parts.join("/"))
}

fn local_candidates(app: &AppContext, key: &Key) -> Vec<PathBuf> {
    let (parent, name) = key.path.rsplit_once('/').unwrap_or(("", &key.path));
    let mut variants = vec![key.path.clone()];
    if let Some(parent) = parent.strip_suffix("_rip") {
        variants.push(format!("{parent}/{name}"));
    } else if !parent.is_empty() {
        variants.push(format!("{parent}_rip/{name}"));
    }
    if key.path.starts_with("music/jacket/") || key.path.starts_with("thumbnail/music_jacket/") {
        let stem = name.strip_suffix(".png").unwrap_or(name);
        variants.extend([
            format!("thumbnail/music_jacket/{name}"),
            format!("music/jacket/{stem}_rip/{name}"),
        ]);
    }
    let numeric = name
        .strip_prefix("material")
        .and_then(|n| n.strip_suffix(".png"))
        .is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()));
    let region = app.server_name(key.region);
    let regions = if numeric {
        vec![region]
    } else {
        vec![region, "jp", "cn", ""]
    };
    let root = &app.paths.ondemand_dir;
    let mut candidates = Vec::new();
    for variant in variants {
        if let Some(rel) = variant.strip_prefix("mysekai/") {
            let pics = MySekaiPaths::new(&app.paths).pictures;
            candidates.extend([pics.join(rel), pics.join(name)]);
        }
        for root in [root.join("assets"), root.clone()] {
            for region in &regions {
                for prefix in ["", "startapp", "ondemand"] {
                    candidates.push(root.join(region).join(prefix).join(&variant));
                }
            }
        }
    }
    let mut seen = HashSet::new();
    candidates.retain(|p| seen.insert(p.clone()));
    candidates
}

fn configured_urls(app: &AppContext, key: &Key) -> Vec<(reqwest::Url, bool)> {
    let sources = &app.config.servers_yaml[app.server_name(key.region)]["rip"]["sources"];
    let directory = key.path.rsplit_once('/').map_or("", |(dir, _)| dir);
    let mut urls = Vec::new();
    let mut seen = HashSet::new();
    for source in rows(sources) {
        let name = source["name"].as_str().unwrap_or("");
        let local = name.eq_ignore_ascii_case("local-updater");
        let Some(base) = source["base_url"]
            .as_str()
            .and_then(|url| reqwest::Url::parse(url.trim()).ok())
        else {
            continue;
        };
        let Some(host) = base
            .host_str()
            .map(|host| host.trim_matches(['[', ']']).trim_end_matches('.'))
        else {
            continue;
        };
        if !matches!(base.scheme(), "http" | "https")
            || !base.username().is_empty()
            || base.password().is_some()
            || base.query().is_some()
            || base.fragment().is_some()
        {
            continue;
        }
        if local {
            if base.port() != Some(8082)
                || !matches!(
                    host,
                    "localhost"
                        | "127.0.0.1"
                        | "::1"
                        | "host.docker.internal"
                        | "gateway.docker.internal"
                )
            {
                continue;
            }
        } else if base.port() == Some(8082)
            || !host.contains('.') && host.parse::<IpAddr>().is_err()
            || [".localhost", ".local", ".internal"]
                .iter()
                .any(|suffix| host.ends_with(suffix))
        {
            continue;
        }
        let prefixes = rows(&source["prefixes"]);
        if !prefixes.is_empty()
            && !prefixes
                .iter()
                .filter_map(Value::as_str)
                .any(|prefix| directory.starts_with(prefix))
        {
            continue;
        }
        let relative = key.path.replace("_rip", "");
        let mut variants = Vec::new();
        match name {
            "haruki" => {
                if ["event", "gacha", "music/long", "mysekai", "virtual_live"]
                    .iter()
                    .any(|p| relative.starts_with(p))
                {
                    variants.push(format!("ondemand/{relative}"));
                } else if [
                    "bonds_honor",
                    "honor",
                    "thumbnail",
                    "character",
                    "music",
                    "rank_live",
                    "stamp",
                    "home/banner",
                    "player_frame",
                    "areaitem",
                ]
                .iter()
                .any(|p| relative.starts_with(p))
                {
                    variants.push(format!("startapp/{relative}"));
                }
            }
            "sekai.best" => variants.push(relative.clone()),
            _ if local => {
                variants.push(relative.clone());
                if let Some((dir, file)) = relative.rsplit_once('/') {
                    let stem = file.rsplit_once('.').map_or(file, |(stem, _)| stem);
                    variants.push(format!("{dir}/{stem}/{file}"));
                }
            }
            _ => {}
        }
        variants.push(key.path.clone());
        for variant in variants {
            let mut url = base.clone();
            if let Ok(mut segments) = url.path_segments_mut() {
                segments.pop_if_empty().extend(variant.split('/'));
            }
            if seen.insert(url.clone()) {
                urls.push((url, local));
            }
        }
    }
    urls
}

fn public_address(address: IpAddr) -> bool {
    match address {
        IpAddr::V4(ip) => {
            let [a, b, c, _] = ip.octets();
            !(ip.is_private()
                || ip.is_loopback()
                || ip.is_link_local()
                || ip.is_documentation()
                || a == 0
                || a >= 224
                || a == 100 && (64..=127).contains(&b)
                || a == 192 && b == 0 && matches!(c, 0 | 2)
                || a == 198 && matches!(b, 18 | 19))
        }
        IpAddr::V6(ip) => {
            let segments = ip.segments();
            (segments[0] & 0xe000) == 0x2000
                && !matches!(segments[0], 0x2002 | 0x3fff)
                && !(segments[0] == 0x2001 && (segments[1] < 0x0200 || segments[1] == 0x0db8))
        }
    }
}

async fn fetch_configured(app: &AppContext, key: &Key) -> Option<Picture> {
    for (url, local) in configured_urls(app, key) {
        if let Ok(Some(image)) = timeout(Duration::from_secs(4), download(url, local)).await {
            return Some(image);
        }
    }
    None
}

async fn download(url: reqwest::Url, local: bool) -> Option<Picture> {
    let host = url.host_str()?.trim_matches(['[', ']']);
    let mut client = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(3));
    if !local {
        let addresses: Vec<_> = tokio::net::lookup_host((host, url.port_or_known_default()?))
            .await
            .ok()?
            .collect();
        if addresses.is_empty() || addresses.iter().any(|addr| !public_address(addr.ip())) {
            return None;
        }
        client = client.resolve_to_addrs(host, &addresses);
    }
    let mut response = client.build().ok()?.get(url).send().await.ok()?;
    if !response.status().is_success()
        || response
            .content_length()
            .is_some_and(|n| n > BODY_LIMIT as u64)
    {
        return None;
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.ok()? {
        if bytes.len() + chunk.len() > BODY_LIMIT {
            return None;
        }
        bytes.extend_from_slice(&chunk);
    }
    tokio::task::spawn_blocking(move || {
        let mut reader = image::ImageReader::new(std::io::Cursor::new(bytes))
            .with_guessed_format()
            .ok()?;
        let mut limits = image::Limits::default();
        limits.max_image_width = Some(8192);
        limits.max_image_height = Some(8192);
        reader.limits(limits);
        Some(Arc::new(reader.decode().ok()?.into_rgba8()))
    })
    .await
    .ok()
    .flatten()
}

pub(super) fn panel(gfx: &GfxContext, width: u32, height: u32) -> RgbaImage {
    let mut image = RgbaImage::new(width, height);
    rounded_panel_overlay(
        gfx,
        &mut image,
        (0, 0, width as i32 - 1, height as i32 - 1),
        14,
        Rgba([255, 255, 255, 230]),
        None,
    );
    image
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::gfx::RenderContextGfxExt;
    use crate::renderers::common::RenderFixture;

    #[test]
    fn request_options_and_metadata() {
        let fixture = RenderFixture::new();
        let ctx = fixture.context();
        let request = MySekaiAssetRequest::new(&json!({
            "fast_render": true, "asset_wait_budget": -1, "asset_fetch_concurrency": 0
        }));
        assert!(request.exhausted());
        assert_eq!(request.limit.available_permits(), 1);
        assert_eq!(
            request.metadata(),
            json!({"assets_complete": true, "missing_assets": []})
        );
        request.missing(&ctx.app, 2, "a.png", "missing");
        request.missing(&ctx.app, 0, "z.png", "missing");
        request.missing(&ctx.app, 0, "a.png", "missing");
        request.missing(&ctx.app, 0, "a.png", "budget-exhausted");
        assert_eq!(
            request.metadata(),
            json!({"assets_complete": false, "missing_assets": [
                {"key": "0:a.png", "region": "jp", "path": "a.png", "reason": "budget-exhausted"},
                {"key": "0:z.png", "region": "jp", "path": "z.png", "reason": "missing"},
                {"key": "2:a.png", "region": "cn", "path": "a.png", "reason": "missing"}
            ]})
        );
        let defaults = MySekaiAssetRequest::new(
            &json!({"asset_wait_budget": "NaN", "asset_fetch_concurrency": 99}),
        );
        assert!(!defaults.fast);
        assert!(defaults.deadline > Instant::now());
        assert_eq!(defaults.limit.available_permits(), 8);
    }

    #[tokio::test]
    async fn local_asset_wins_after_fallback_and_never_feeds_remote_only() {
        let fixture = RenderFixture::new();
        let ctx = fixture.context();
        let assets = Assets::new(
            &ctx,
            ctx.gfx().unwrap(),
            &json!({"fast_render": true, "asset_wait_budget": 0}),
        );
        let path = "mysekai/item/icon.png";
        let _ = assets.rip(path, 0, Some((8, 8)), false, "?").await;
        assert_eq!(
            assets.metadata()["missing_assets"][0]["reason"],
            "budget-exhausted"
        );
        fixture.png(
            "ondemand/assets/jp/ondemand/mysekai/item/icon.png",
            (20, 10),
        );
        let local = assets
            .load("ondemand/mysekai/item/icon.png", 0, Some((8, 8)), false)
            .await
            .unwrap();
        assert_eq!(local.dimensions(), (8, 8));
        assert!(!assets.metadata()["assets_complete"].as_bool().unwrap());
        assert!(assets.load(path, 0, Some((8, 8)), true).await.is_none());
        let next = Assets::new(
            &ctx,
            ctx.gfx().unwrap(),
            &json!({"fast_render": true, "asset_wait_budget": 0}),
        );
        assert!(Arc::ptr_eq(
            &local,
            &next.load(path, 0, Some((8, 8)), false).await.unwrap()
        ));
        assert_eq!(next.metadata()["assets_complete"], true);
    }

    #[test]
    fn configured_urls_restrict_local_updater_and_escape_asset_paths() {
        let fixture = RenderFixture::new();
        fixture.write(
            "config/pjsk/servers.yaml",
            r#"
jp:
  rip:
    sources:
      - {name: local-updater, base_url: 'http://127.0.0.1:8081'}
      - {name: local-updater, base_url: 'http://127.0.0.1:8082/files'}
      - {name: haruki, base_url: 'https://assets.example.org/', prefixes: [mysekai]}
      - {name: invalid, base_url: 'https://user@assets.example.org/'}
"#,
        );
        let ctx = fixture.context();
        let key = Key {
            path: "mysekai/texture_rip/icon #1.png".into(),
            region: 0,
            remote_only: true,
        };
        let urls = configured_urls(&ctx.app, &key);
        assert_eq!(
            urls[0].0.as_str(),
            "http://127.0.0.1:8082/files/mysekai/texture/icon%20%231.png"
        );
        assert!(urls[0].1);
        assert!(urls.iter().any(
            |(url, local)| !local && url.path() == "/ondemand/mysekai/texture/icon%20%231.png"
        ));
        assert!(
            urls.iter()
                .all(|(url, _)| url.port() != Some(8081) && url.username().is_empty())
        );
        for address in [
            "127.0.0.1",
            "169.254.169.254",
            "100.64.1.1",
            "192.168.1.2",
            "::1",
            "::ffff:8.8.8.8",
            "fc00::1",
        ] {
            assert!(!public_address(address.parse().unwrap()));
        }
        assert!(public_address("8.8.8.8".parse().unwrap()));
        assert!(normalize("mysekai/../elsewhere").is_none());
    }

    #[tokio::test]
    async fn concurrent_waiters_share_one_remote_flight() {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let fixture = RenderFixture::new();
        let ctx = fixture.context();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let mut config = ctx.app.config.clone();
        config.helper_url = format!("http://{}", listener.local_addr().unwrap());
        let app = Arc::new(AppContext::new(ctx.app.paths.clone(), config).unwrap());
        let assets = Assets::new(
            &RenderRequestContext {
                app,
                policy: ctx.policy,
            },
            ctx.gfx().unwrap(),
            &json!({}),
        );
        let path = "mysekai/fixture/shared.png";
        let release = tokio::sync::Notify::new();
        let fake_helper = async {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = Vec::new();
            while !request.ends_with(b"\r\n\r\n") {
                request.push(stream.read_u8().await.unwrap());
            }
            assert!(request.starts_with(b"POST /assets/fetch?"));
            release.notified().await;
            fixture.png("ondemand/jp/mysekai/fixture/shared.png", (8, 12));
            stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 11\r\nConnection: close\r\n\r\n{\"ok\":true}").await.unwrap();
        };
        let readers = async {
            let mut first = std::pin::pin!(assets.load(path, 0, None, false));
            let mut second = std::pin::pin!(assets.load(path, 0, None, false));
            let key = Key {
                path: path.into(),
                region: 0,
                remote_only: false,
            };
            tokio::select! {
                _ = &mut first => panic!("first load completed before helper responded"),
                _ = &mut second => panic!("second load completed before helper responded"),
                _ = async {
                    loop {
                        let waiting = assets.shared.cache.lock().unwrap().flights.get(&key)
                            .map(|receiver| receiver.clone());
                        if let Some(receiver) = waiting {
                            assert!(!receiver.has_changed().unwrap());
                            break;
                        }
                        tokio::task::yield_now().await;
                    }
                } => {}
            }
            release.notify_one();
            let (first, second) = tokio::join!(first, second);
            let (first, second) = (first.unwrap(), second.unwrap());
            assert_eq!(first.dimensions(), (8, 12));
            assert!(Arc::ptr_eq(&first, &second));
        };
        timeout(Duration::from_secs(5), async {
            tokio::join!(fake_helper, readers)
        })
        .await
        .unwrap();
    }

    #[tokio::test]
    async fn negative_cache_expires_and_local_probe_bypasses_it() {
        let fixture = RenderFixture::new();
        let ctx = fixture.context();
        let assets = Assets::new(&ctx, ctx.gfx().unwrap(), &json!({}));
        let key = Key {
            path: "mysekai/thumbnail/fixture/test.png".into(),
            region: 0,
            remote_only: false,
        };
        assert!(assets.load(&key.path, 0, None, false).await.is_none());
        {
            let mut cache = assets.shared.cache.lock().unwrap();
            let now = Instant::now();
            let expiry = *cache.negative.peek(&key).unwrap();
            assert!(expiry > now + Duration::from_secs(14) && expiry <= now + TTL);
            cache
                .negative
                .put(key.clone(), now - Duration::from_secs(1));
        }
        assert!(assets.load(&key.path, 0, None, false).await.is_none());
        assert!(
            *assets
                .shared
                .cache
                .lock()
                .unwrap()
                .negative
                .peek(&key)
                .unwrap()
                > Instant::now()
        );
        fixture.png("static/pics/mysekai/thumbnail/fixture/test.png", (5, 6));
        assert_eq!(
            assets
                .load(&key.path, 0, None, false)
                .await
                .unwrap()
                .dimensions(),
            (5, 6)
        );
        assert_eq!(assets.metadata()["assets_complete"], true);
    }
}
