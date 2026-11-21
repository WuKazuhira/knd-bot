//! YAML configuration for the drawing service.
use serde_json::Value;
use std::collections::BTreeMap;

#[derive(Debug, Clone)]
pub struct DrawConfig {
    pub server_map: BTreeMap<i32, String>,
    pub servers_yaml: Value,
    pub chart_preview_base_url: String,
    pub helper_url: String,
    pub asset_fetch_timeout_secs: f64,
    pub local_asset_timeout_secs: f64,
}

use crate::core::{DrawError, Result, default_server_map, paths::DrawPaths};
use serde_yaml::{Mapping, Value as Yaml};
use std::{
    io::ErrorKind,
    path::{Path, PathBuf},
};

impl DrawConfig {
    pub fn load(paths: &DrawPaths) -> Result<Self> {
        let dir = paths.config_dir.join("pjsk");
        let mut settings = load_yaml(&dir.join("settings.yaml"))?;
        let mut servers = load_yaml(&dir.join("servers.yaml"))?;
        let local_path = std::env::var_os("PJSK_LOCAL_CONFIG")
            .map(PathBuf::from)
            .unwrap_or_else(|| dir.join("local.yaml"));
        let mut local = load_yaml(&local_path)?;
        for (key, base) in [("settings", &mut settings), ("servers", &mut servers)] {
            match local.remove(key) {
                None | Some(Yaml::Null) => {}
                Some(Yaml::Mapping(overrides)) => merge(base, overrides),
                _ => {
                    return Err(DrawError::Render(format!(
                        "{}: {key} must be a mapping",
                        local_path.display()
                    )));
                }
            }
        }

        let mut server_map = default_server_map();
        match settings.get("server_map") {
            None | Some(Yaml::Null) => {}
            Some(Yaml::Mapping(map)) if map.is_empty() => {}
            Some(map) => {
                let map = map
                    .as_mapping()
                    .ok_or_else(|| DrawError::Render("server_map must be a mapping".into()))?;
                server_map.clear();
                for (key, value) in map {
                    let id = match key {
                        Yaml::Number(number) => {
                            number.as_i64().and_then(|id| i32::try_from(id).ok())
                        }
                        Yaml::String(id) => id.trim().parse().ok(),
                        _ => None,
                    }
                    .ok_or_else(|| DrawError::Render(format!("Invalid server ID: {key:?}")))?;
                    let name = value.as_str().ok_or_else(|| {
                        DrawError::Render(format!("Invalid server name for ID {id}"))
                    })?;
                    server_map.insert(id, name.to_owned());
                }
            }
        }

        Ok(Self {
            server_map,
            servers_yaml: serde_json::to_value(Yaml::Mapping(servers))?,
            chart_preview_base_url: settings
                .get("endpoints")
                .and_then(|endpoints| endpoints.get("chart_preview_base_url"))
                .and_then(Yaml::as_str)
                .unwrap_or_default()
                .trim_end_matches('/')
                .to_owned(),
            helper_url: std::env::var("PJSK_HELPER_URL")
                .unwrap_or_else(|_| "http://host.docker.internal:45558".into())
                .trim_end_matches('/')
                .to_owned(),
            asset_fetch_timeout_secs: timeout("PJSK_DRAW_ASSET_FETCH_TIMEOUT", 90.0)?,
            local_asset_timeout_secs: timeout("PJSK_DRAW_LOCAL_ASSET_TIMEOUT", 15.0)?,
        })
    }
}

fn load_yaml(path: &Path) -> Result<Mapping> {
    let bytes = match std::fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(Mapping::new()),
        Err(error) => return Err(error.into()),
    };
    match serde_yaml::from_slice(&bytes) {
        Ok(Yaml::Mapping(mapping)) => Ok(mapping),
        Ok(Yaml::Null) => Ok(Mapping::new()),
        Ok(_) => Err(DrawError::Render(format!(
            "{} must contain a YAML mapping",
            path.display()
        ))),
        Err(error) => Err(DrawError::Render(format!("{}: {error}", path.display()))),
    }
}

fn merge(base: &mut Mapping, overrides: Mapping) {
    for (key, value) in overrides {
        match (base.get_mut(&key), value) {
            (Some(Yaml::Mapping(existing)), Yaml::Mapping(overrides)) => merge(existing, overrides),
            (_, value) => {
                base.insert(key, value);
            }
        }
    }
}

fn timeout(key: &str, default: f64) -> Result<f64> {
    match std::env::var(key) {
        Err(std::env::VarError::NotPresent) => Ok(default),
        value => value
            .ok()
            .and_then(|value| value.trim().parse::<f64>().ok())
            .filter(|value| std::time::Duration::try_from_secs_f64(*value).is_ok())
            .ok_or_else(|| DrawError::Render(format!("Invalid timeout in {key}"))),
    }
}
