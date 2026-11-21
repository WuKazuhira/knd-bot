//! Runtime paths for the drawing service.
use std::path::PathBuf;

#[derive(Debug, Clone)]
pub struct DrawPaths {
    pub project_root: PathBuf,
    pub config_dir: PathBuf,
    pub data_dir: PathBuf,
    pub resource_dir: PathBuf,
    pub font_dir: PathBuf,
    pub pjsk_data_dir: PathBuf,
    pub static_dir: PathBuf,
    pub ondemand_dir: PathBuf,
    pub temp_dir: PathBuf,
}

impl DrawPaths {
    pub fn discover() -> crate::core::Result<Self> {
        let cwd = std::env::current_dir()?;
        Ok(Self::from_root(Self::discover_root(&cwd)))
    }

    pub fn from_root(project_root: impl Into<PathBuf>) -> Self {
        let project_root = resolve(project_root.into());
        Self::with_overrides(project_root, |key| std::env::var_os(key).map(PathBuf::from))
    }

    fn discover_root(start: &std::path::Path) -> PathBuf {
        start
            .ancestors()
            .find(|path| path.join("config/pjsk").is_dir())
            .or_else(|| {
                start
                    .ancestors()
                    .filter(|path| path.join("Cargo.toml").is_file())
                    .last()
            })
            .unwrap_or(start)
            .to_owned()
    }

    fn with_overrides(project_root: PathBuf, get: impl Fn(&str) -> Option<PathBuf>) -> Self {
        let config_dir =
            resolve(get("KNDBOT_CONFIG_DIR").unwrap_or_else(|| project_root.join("config")));
        let data_dir = resolve(get("KNDBOT_DATA_DIR").unwrap_or_else(|| project_root.join("data")));
        let resource_dir =
            resolve(get("KNDBOT_RESOURCE_DIR").unwrap_or_else(|| data_dir.join("resources")));
        let temp_dir = resolve(get("KNDBOT_TEMP_DIR").unwrap_or_else(|| data_dir.join("temp")));
        let pjsk_data_dir = data_dir.join("pjsk");
        Self {
            project_root,
            config_dir,
            data_dir,
            font_dir: resource_dir.join("font"),
            resource_dir,
            static_dir: pjsk_data_dir.join("static"),
            ondemand_dir: pjsk_data_dir.join("ondemand"),
            pjsk_data_dir,
            temp_dir,
        }
    }
}

// Resolve existing ancestors, including symlinks, even if the leaf has yet to be created.
fn resolve(path: PathBuf) -> PathBuf {
    let path = if path.as_os_str().is_empty() {
        PathBuf::from(".")
    } else {
        path
    };
    let absolute = std::path::absolute(&path).unwrap_or(path);
    let mut resolved = PathBuf::new();
    for component in absolute.components() {
        match component {
            std::path::Component::ParentDir => {
                resolved.pop();
            }
            _ => {
                resolved.push(component);
                if let Ok(canonical) = resolved.canonicalize() {
                    resolved = canonical;
                }
            }
        }
    }
    resolved
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paths_discover_workspace_and_derive_overrides() {
        let crate_dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
        let root = DrawPaths::discover_root(crate_dir);
        assert!(crate_dir.starts_with(&root));
        assert!(root.join("config/pjsk").is_dir() || root.join("Cargo.toml").is_file());

        let data = root.join("custom-data");
        let paths =
            DrawPaths::with_overrides(root, |key| (key == "KNDBOT_DATA_DIR").then(|| data.clone()));
        assert_eq!(paths.ondemand_dir, data.join("pjsk/ondemand"));
        assert_eq!(paths.static_dir, data.join("pjsk/static"));
        assert_eq!(paths.font_dir, data.join("resources/font"));
        assert_eq!(paths.temp_dir, data.join("temp"));
    }
}
