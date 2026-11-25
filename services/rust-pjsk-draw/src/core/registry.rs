use std::collections::BTreeMap;
use std::sync::Arc;

use serde_json::Value;

use crate::core::{
    AssetSourcePolicy, DrawError, RenderOutput, RenderRequestContext, Renderer, Result,
    context::AppContext,
};

#[derive(Default)]
pub struct TaskRegistry {
    renderers: BTreeMap<String, Arc<dyn Renderer>>,
}

impl TaskRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn register(&mut self, renderer: Arc<dyn Renderer>) -> Result<()> {
        let name = renderer.name();
        if self.renderers.contains_key(name) {
            return Err(DrawError::Render(format!("绘图任务名重复注册: {name}")));
        }
        self.renderers.insert(name.to_string(), renderer);
        Ok(())
    }

    pub fn names(&self) -> Vec<String> {
        self.renderers.keys().cloned().collect()
    }

    pub fn get(&self, name: &str) -> Option<&Arc<dyn Renderer>> {
        self.renderers.get(name)
    }

    pub async fn dispatch(
        &self,
        app: Arc<AppContext>,
        name: &str,
        payload: Value,
    ) -> Result<RenderOutput> {
        let renderer = self
            .get(name)
            .ok_or_else(|| DrawError::UnknownRenderer(name.into()))?;
        let payload = match payload {
            Value::Null => Value::Object(Default::default()),
            Value::Object(_) => payload,
            _ => return Err(DrawError::InvalidPayload("Expected a JSON object".into())),
        };
        let policy = dispatch_policy(name, payload.get("asset_source_policy"))?;
        renderer
            .render(&RenderRequestContext { app, policy }, payload)
            .await
    }
}

fn dispatch_policy(name: &str, value: Option<&Value>) -> Result<AssetSourcePolicy> {
    let raw = value
        .filter(|value| crate::core::context::truthy(value))
        .map(|value| {
            value
                .as_str()
                .ok_or_else(|| DrawError::InvalidAssetPolicy(value.to_string()))
        })
        .transpose()?;
    if raw.is_some_and(|raw| raw.trim().is_empty()) {
        return Err(DrawError::InvalidAssetPolicy(String::new()));
    }
    let policy = AssetSourcePolicy::parse(raw)?;
    if policy == AssetSourcePolicy::LocalOnly && name != "event_info" {
        return Err(DrawError::InvalidAssetPolicy(format!(
            "{name} does not support local_only"
        )));
    }
    Ok(policy)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn asset_policy_accepts_local_only_for_event_info() {
        assert_eq!(
            dispatch_policy("other", None).unwrap(),
            AssetSourcePolicy::Default
        );
        assert_eq!(
            dispatch_policy("event_info", Some(&json!(" LOCAL_ONLY "))).unwrap(),
            AssetSourcePolicy::LocalOnly
        );
        assert!(matches!(
            dispatch_policy("other", Some(&json!("local_only"))),
            Err(DrawError::InvalidAssetPolicy(_))
        ));
        assert!(dispatch_policy("event_info", Some(&json!("remote"))).is_err());
    }
}
