//! Reusable Project SEKAI data and drawing components.

pub mod card;
pub mod event_data;
pub mod honor;
pub mod profile_bg;
pub mod profile_header;
pub mod ui;

pub use card::*;
pub use event_data::*;
pub use honor::*;
pub use profile_bg::*;
pub use profile_header::*;
pub use ui::*;

use serde_json::Value;

fn integer(value: &Value) -> Option<i64> {
    value
        .as_i64()
        .or_else(|| value.as_str()?.trim().parse().ok())
}

pub(crate) use crate::core::context::truthy;

fn string(value: &Value) -> &str {
    value.as_str().unwrap_or_default()
}
