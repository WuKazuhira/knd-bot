use std::sync::Arc;

use axum::{
    Json, Router,
    body::{Body, Bytes},
    extract::{DefaultBodyLimit, Path, State, rejection::BytesRejection},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use base64::{Engine, engine::general_purpose::STANDARD};
use pjsk_draw_server::core::{
    DrawError, config::DrawConfig, context::AppContext, guess_image_media_type, paths::DrawPaths,
    registry::TaskRegistry,
};
use pjsk_draw_server::gfx::GfxContext;
use serde_json::{Value, json};
use tower_http::trace::{DefaultMakeSpan, DefaultOnResponse, TraceLayer};
use tracing::Level;

#[derive(Clone)]
struct ServerState {
    app: Arc<AppContext>,
    gfx: Arc<GfxContext>,
    registry: Arc<TaskRegistry>,
}

fn router(paths: DrawPaths, config: DrawConfig) -> anyhow::Result<Router> {
    let app = Arc::new(AppContext::new(paths, config)?);
    let gfx = Arc::new(GfxContext::load(&app.paths.font_dir)?);
    app.insert_extension(gfx.clone());
    let mut registry = TaskRegistry::new();
    pjsk_draw_server::renderers::register_all(&mut registry)?;

    Ok(Router::new()
        .route("/health", get(health))
        .route("/readyz", get(ready))
        .route("/renderers", get(list_renderers))
        .route("/cache/clear", post(clear_caches))
        .route(
            "/render/{name}",
            post(render).layer(DefaultBodyLimit::max(64 * 1024 * 1024)),
        )
        .layer(
            TraceLayer::new_for_http()
                .make_span_with(DefaultMakeSpan::new().level(Level::INFO))
                .on_response(DefaultOnResponse::new().level(Level::INFO)),
        )
        .with_state(ServerState {
            app,
            gfx,
            registry: Arc::new(registry),
        }))
}

async fn health(State(state): State<ServerState>) -> Json<Value> {
    Json(json!({ "status": "ok", "renderers": state.registry.names().len() }))
}

async fn ready(State(state): State<ServerState>) -> (StatusCode, Json<Value>) {
    let renderers = state.registry.names().len();
    let temp_dir = state.app.paths.ondemand_dir.join("temp");
    let probe = tokio::task::spawn_blocking(move || {
        std::fs::create_dir_all(&temp_dir)?;
        tempfile::Builder::new()
            .prefix(".pjsk-draw-ready-")
            .tempfile_in(temp_dir)?
            .close()
    })
    .await;
    let error = match probe {
        Ok(Ok(())) => None,
        Ok(Err(error)) => Some(error.to_string()),
        Err(error) => Some(error.to_string()),
    };
    let ready = error.is_none() && renderers > 0;
    let mut payload = json!({ "ready": ready, "renderers": renderers });
    if let Some(error) = error {
        payload["error"] = json!(error);
    }
    let status = if ready {
        StatusCode::OK
    } else {
        StatusCode::SERVICE_UNAVAILABLE
    };
    (status, Json(payload))
}

async fn list_renderers(State(state): State<ServerState>) -> Json<Value> {
    Json(json!({ "renderers": state.registry.names() }))
}

async fn clear_caches(State(state): State<ServerState>) -> Json<Value> {
    state.app.clear_caches();
    state.gfx.cache.clear_runtime_caches();
    Json(json!({ "status": "ok" }))
}

async fn render(
    State(state): State<ServerState>,
    Path(name): Path<String>,
    body: Result<Bytes, BytesRejection>,
) -> Response {
    let bytes = match body {
        Ok(bytes) => bytes,
        Err(error) => {
            let status = error.status();
            let detail = if status == StatusCode::PAYLOAD_TOO_LARGE {
                "请求体超过 64 MiB"
            } else {
                "无法读取请求体"
            };
            return (status, Json(json!({ "detail": detail }))).into_response();
        }
    };
    let Ok(payload) = serde_json::from_slice::<Value>(&bytes) else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "detail": "无效的 JSON 请求体" })),
        )
            .into_response();
    };
    if !payload.is_object() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "detail": "载荷必须是 JSON 对象" })),
        )
            .into_response();
    }

    let mut output = match state.registry.dispatch(state.app, &name, payload).await {
        Ok(output) => output,
        Err(error) => {
            let status = match error {
                DrawError::UnknownRenderer(_) => StatusCode::NOT_FOUND,
                DrawError::InvalidPayload(_) => StatusCode::BAD_REQUEST,
                _ => StatusCode::INTERNAL_SERVER_ERROR,
            };
            return (status, Json(json!({ "detail": error.to_string() }))).into_response();
        }
    };
    if output.images.len() == 1 && output.meta.is_empty() {
        let bytes = output.images.pop().unwrap();
        let media_type = guess_image_media_type(&bytes);
        return ([(header::CONTENT_TYPE, media_type)], Body::from(bytes)).into_response();
    }
    let images: Vec<_> = output
        .images
        .iter()
        .map(|image| STANDARD.encode(image))
        .collect();
    Json(json!({ "images": images, "meta": output.meta })).into_response()
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();
    let paths = DrawPaths::discover()?;
    let config = DrawConfig::load(&paths)?;
    let app = router(paths, config)?;
    let host = std::env::var("PJSK_DRAW_HOST").unwrap_or_else(|_| "0.0.0.0".into());
    let port = std::env::var("PJSK_DRAW_PORT")
        .unwrap_or_else(|_| "45560".into())
        .parse::<u16>()?;
    let listener = tokio::net::TcpListener::bind((host.as_str(), port)).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{body::to_bytes, extract::Request};
    use tower::ServiceExt;

    async fn request(app: &Router, method: &str, path: &str, body: &'static str) -> Response {
        app.clone()
            .oneshot(
                Request::builder()
                    .method(method)
                    .uri(path)
                    .body(Body::from(body))
                    .unwrap(),
            )
            .await
            .unwrap()
    }

    #[tokio::test]
    async fn http_routes_and_png_rendering() {
        let temp = tempfile::tempdir().unwrap();
        let mut paths = DrawPaths::from_root(temp.path());
        paths.ondemand_dir = temp.path().join("ondemand");
        let config = DrawConfig::load(&paths).unwrap();
        let app = router(paths, config).unwrap();
        let mut expected_registry = TaskRegistry::new();
        pjsk_draw_server::renderers::register_all(&mut expected_registry).unwrap();
        let expected_names = expected_registry.names();
        let expected_count = expected_names.len();
        for renderer in ["notify_rows", "sk_me_curve_total", "sk_me_curve_wl"] {
            assert!(expected_names.iter().any(|name| name == renderer));
        }
        for (path, expected) in [
            (
                "/health",
                json!({"status": "ok", "renderers": expected_count}),
            ),
            (
                "/readyz",
                json!({"ready": true, "renderers": expected_count}),
            ),
            ("/renderers", json!({"renderers": expected_names})),
        ] {
            let response = request(&app, "GET", path, "").await;
            assert_eq!(response.status(), StatusCode::OK);
            let bytes = to_bytes(response.into_body(), usize::MAX).await.unwrap();
            assert_eq!(serde_json::from_slice::<Value>(&bytes).unwrap(), expected);
        }
        for body in [
            "{}",
            r#"{"title":"New song","rows":[["Song"]],"footer":"PJSK"}"#,
        ] {
            let response = request(&app, "POST", "/render/notify_rows", body).await;
            assert_eq!(response.status(), StatusCode::OK);
            assert_eq!(response.headers()[header::CONTENT_TYPE], "image/png");
            let bytes = to_bytes(response.into_body(), usize::MAX).await.unwrap();
            assert!(bytes.starts_with(b"\x89PNG\r\n\x1a\n"));
        }
        for (path, body, status) in [
            ("/render/unknown", "{}", StatusCode::NOT_FOUND),
            ("/render/notify_rows", "null", StatusCode::BAD_REQUEST),
            ("/render/notify_rows", "", StatusCode::BAD_REQUEST),
            (
                "/render/notify_rows",
                "invalid json",
                StatusCode::BAD_REQUEST,
            ),
            (
                "/render/notify_rows",
                r#"{"rows":true}"#,
                StatusCode::BAD_REQUEST,
            ),
            ("/cache/clear", "", StatusCode::OK),
        ] {
            assert_eq!(request(&app, "POST", path, body).await.status(), status);
        }

        let chunk = Bytes::from(vec![b' '; 1024 * 1024]);
        let chunks = (0..65).map(move |_| Ok::<_, std::convert::Infallible>(chunk.clone()));
        let oversized = Request::post("/render/notify_rows")
            .body(Body::from_stream(futures_util::stream::iter(chunks)))
            .unwrap();
        let response = app.oneshot(oversized).await.unwrap();
        assert_eq!(response.status(), StatusCode::PAYLOAD_TOO_LARGE);
    }
}
