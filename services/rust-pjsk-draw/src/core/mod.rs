//! 核心基础设施：配置、路径、主数据读取与缓存、资源下载与上下文、渲染任务注册表。

use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;
use std::sync::Arc;

use async_trait::async_trait;
use image::RgbaImage;
use serde_json::Value;
use thiserror::Error;

pub mod config;
pub mod context;
pub mod master_data;
pub mod paths;
pub mod registry;

#[derive(Debug, Error)]
pub enum DrawError {
    #[error("未知的绘图任务: {0}")]
    UnknownRenderer(String),
    #[error("载荷格式错误: {0}")]
    InvalidPayload(String),
    #[error("不支持的 asset_source_policy: {0}")]
    InvalidAssetPolicy(String),
    #[error("严格本地资产失败: {0}")]
    LocalOnlyAsset(String),
    #[error("严格本地资产不存在: {0}")]
    LocalOnlyAssetNotFound(String),
    #[error("主数据不存在: {0}")]
    MasterDataNotFound(String),
    #[error("I/O 错误: {0}")]
    Io(#[from] std::io::Error),
    #[error("JSON 解析错误: {0}")]
    Json(#[from] serde_json::Error),
    #[error("渲染失败: {0}")]
    Render(String),
}

pub type Result<T> = std::result::Result<T, DrawError>;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum AssetSourcePolicy {
    Default,
    LocalOnly,
}

impl AssetSourcePolicy {
    pub fn parse(raw: Option<&str>) -> Result<Self> {
        let normalized = raw.unwrap_or("default").trim().to_ascii_lowercase();
        match normalized.as_str() {
            "" | "default" => Ok(Self::Default),
            "local_only" => Ok(Self::LocalOnly),
            other => Err(DrawError::InvalidAssetPolicy(other.to_string())),
        }
    }
}

#[derive(Debug, Clone)]
pub struct RenderOutput {
    pub images: Vec<Vec<u8>>,
    pub meta: serde_json::Map<String, Value>,
}

impl RenderOutput {
    pub fn single(bytes: Vec<u8>) -> Self {
        Self {
            images: vec![bytes],
            meta: serde_json::Map::new(),
        }
    }

    pub fn multi(images: Vec<Vec<u8>>) -> Self {
        Self {
            images,
            meta: serde_json::Map::new(),
        }
    }

    pub fn with_meta(images: Vec<Vec<u8>>, meta: serde_json::Map<String, Value>) -> Self {
        Self { images, meta }
    }
}

#[async_trait]
pub trait Renderer: Send + Sync {
    fn name(&self) -> &'static str;
    async fn render(&self, ctx: &RenderRequestContext, payload: Value) -> Result<RenderOutput>;
}

/// 单次渲染请求的上下文（绑定了共享服务状态 + 请求级 asset policy）。
#[derive(Clone)]
pub struct RenderRequestContext {
    pub app: Arc<context::AppContext>,
    pub policy: AssetSourcePolicy,
}

pub fn default_server_map() -> BTreeMap<i32, String> {
    BTreeMap::from([
        (0, "jp".to_string()),
        (1, "tw".to_string()),
        (2, "cn".to_string()),
    ])
}

pub fn guess_image_media_type(data: &[u8]) -> &'static str {
    if data.starts_with(b"\x89PNG\r\n\x1a\n") {
        "image/png"
    } else if data.starts_with(b"\xff\xd8\xff") {
        "image/jpeg"
    } else if data.starts_with(b"GIF87a") || data.starts_with(b"GIF89a") {
        "image/gif"
    } else if data.len() >= 12 && &data[0..4] == b"RIFF" && &data[8..12] == b"WEBP" {
        "image/webp"
    } else {
        "application/octet-stream"
    }
}

pub fn resolve_server_name(server_map: &BTreeMap<i32, String>, pjsk_type: i32) -> &str {
    server_map
        .get(&pjsk_type)
        .map(String::as_str)
        .unwrap_or("jp")
}

/// 用于测试或离线占位校验的公共辅助类型导出。
pub type AssetImage = RgbaImage;
pub type MasterIndex = HashMap<String, Value>;
pub type SharedPath = PathBuf;
