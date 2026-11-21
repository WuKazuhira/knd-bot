//! 各 PJSK 指令渲染器实现与注册入口。

mod b30;
mod cardbox;
mod cardinfo;
mod common;
mod deck;
mod diffrank;
mod event;
mod findcard;
mod gacha;
mod guess;
pub mod mappreview;
mod mysekai;
mod notify;
mod profile;
mod rop;
mod sk;
mod sk_me_curve;
mod song;
mod world_bloom_frame;
mod ycm;

pub use mysekai::MySekaiPaths;

use std::sync::Arc;

use crate::core::{Result, registry::TaskRegistry};

pub fn register_all(registry: &mut TaskRegistry) -> Result<()> {
    registry.register(Arc::new(notify::NotifyRows))?;
    registry.register(Arc::new(notify::VliveCards))?;
    registry.register(Arc::new(rop::Rop))?;
    registry.register(Arc::new(findcard::FindCard::Unit))?;
    registry.register(Arc::new(findcard::FindCard::Attribute))?;
    registry.register(Arc::new(gacha::Gacha))?;
    registry.register(Arc::new(song::Song))?;
    registry.register(Arc::new(cardinfo::CardInfo))?;
    registry.register(Arc::new(cardbox::CardBox))?;
    registry.register(Arc::new(world_bloom_frame::WorldBloomFrame))?;
    registry.register(Arc::new(ycm::Ycm))?;
    registry.register(Arc::new(b30::B30))?;
    registry.register(Arc::new(diffrank::DiffRank))?;
    registry.register(Arc::new(deck::Deck))?;
    registry.register(Arc::new(event::EventInfo))?;
    registry.register(Arc::new(event::EventCatalog))?;
    registry.register(Arc::new(mappreview::MapPreview::Map))?;
    registry.register(Arc::new(mappreview::MapPreview::Skill))?;
    registry.register(Arc::new(guess::Guess::Chart))?;
    registry.register(Arc::new(guess::Guess::Jacket))?;
    registry.register(Arc::new(guess::Guess::Card))?;
    registry.register(Arc::new(guess::Guess::Lyrics))?;
    registry.register(Arc::new(profile::Profile))?;
    for renderer in [
        sk::Sk::Single,
        sk::Sk::Multi,
        sk::Sk::RankTable,
        sk::Sk::WlRankTable,
        sk::Sk::Forecast,
        sk::Sk::WlForecast,
        sk::Sk::ForecastCurve,
        sk::Sk::Cf,
        sk::Sk::CfRange,
        sk::Sk::Csb,
    ] {
        registry.register(Arc::new(renderer))?;
    }
    registry.register(Arc::new(sk_me_curve::MeCurve::Total))?;
    registry.register(Arc::new(sk_me_curve::MeCurve::Wl))?;
    for renderer in mysekai::MySekai::ALL {
        registry.register(Arc::new(renderer))?;
    }
    Ok(())
}
