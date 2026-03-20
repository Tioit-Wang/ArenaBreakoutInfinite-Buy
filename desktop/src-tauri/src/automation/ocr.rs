use std::io::Cursor;
use std::net::{SocketAddr, TcpStream};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::{Context, Result};
use base64::Engine;
use image::{DynamicImage, GrayImage, ImageFormat};
use reqwest::Url;
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::app::types::{OcrStatus, UmiOcrConfig};
use crate::config::paths::AppPaths;
use crate::storage::repository::Repository;

#[derive(Clone)]
pub struct OcrManager {
    paths: Arc<AppPaths>,
    _repo: Arc<Repository>,
    child: Arc<Mutex<Option<Child>>>,
}

impl OcrManager {
    pub fn new(paths: Arc<AppPaths>, repo: Arc<Repository>) -> Self {
        Self {
            paths,
            _repo: repo,
            child: Arc::new(Mutex::new(None)),
        }
    }

    pub fn status(&self, config: &UmiOcrConfig) -> OcrStatus {
        let ready = probe_endpoint(&config.base_url);
        let exe_path = self.resolve_exe_path(config);
        let started = self
            .child
            .lock()
            .ok()
            .and_then(|guard| guard.as_ref().map(|_| true))
            .unwrap_or(false);
        OcrStatus {
            managed: config.auto_start,
            ready,
            using_existing: ready && !started,
            started,
            base_url: config.base_url.clone(),
            exe_path: exe_path.as_ref().map(|path| path.display().to_string()),
            message: if ready {
                "Umi-OCR endpoint reachable".to_string()
            } else {
                "Umi-OCR endpoint unavailable".to_string()
            },
        }
    }

    pub fn ensure_started(&self, config: &UmiOcrConfig) -> Result<OcrStatus> {
        if !config.auto_start || probe_endpoint(&config.base_url) {
            return Ok(self.status(config));
        }
        let Some(exe_path) = self.resolve_exe_path(config) else {
            return Ok(OcrStatus {
                managed: config.auto_start,
                ready: false,
                using_existing: false,
                started: false,
                base_url: config.base_url.clone(),
                exe_path: None,
                message: "未找到可启动的 Umi-OCR.exe".to_string(),
            });
        };
        let child = Command::new(&exe_path)
            .current_dir(exe_path.parent().unwrap_or(&self.paths.root_dir))
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .with_context(|| format!("failed to spawn {}", exe_path.display()))?;
        *self.child.lock().expect("ocr mutex poisoned") = Some(child);
        std::thread::sleep(Duration::from_secs_f64(config.startup_wait_sec.min(2.0)));
        Ok(self.status(config))
    }

    pub fn stop(&self) -> Result<()> {
        if let Some(mut child) = self.child.lock().expect("ocr mutex poisoned").take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        Ok(())
    }

    fn resolve_exe_path(&self, config: &UmiOcrConfig) -> Option<std::path::PathBuf> {
        let trimmed = config.exe_path.trim();
        if trimmed.is_empty() {
            return None;
        }
        let path = std::path::PathBuf::from(trimmed);
        if path.is_file() {
            return Some(path);
        }
        None
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OcrTextBlock {
    pub text: String,
}

pub async fn recognize_text(config: &UmiOcrConfig, image: &GrayImage) -> Result<Vec<OcrTextBlock>> {
    let payload = post_umi_ocr(config, image).await?;
    if payload
        .get("code")
        .and_then(|value| value.as_i64())
        .unwrap_or_default()
        == 101
    {
        return Ok(Vec::new());
    }
    let mut out = Vec::new();
    if let Some(items) = payload.get("data").and_then(|value| value.as_array()) {
        for item in items {
            if let Some(text) = item.get("text").and_then(|value| value.as_str()) {
                let trimmed = text.trim();
                if !trimmed.is_empty() {
                    out.push(OcrTextBlock {
                        text: trimmed.to_string(),
                    });
                }
            }
        }
    }
    Ok(out)
}

async fn post_umi_ocr(config: &UmiOcrConfig, image: &GrayImage) -> Result<serde_json::Value> {
    let mut bytes = Vec::new();
    DynamicImage::ImageLuma8(image.clone())
        .write_to(&mut Cursor::new(&mut bytes), ImageFormat::Png)
        .context("failed to encode OCR image as png")?;
    let base64 = base64::engine::general_purpose::STANDARD.encode(bytes);
    let endpoint = format!("{}/api/ocr", config.base_url.trim_end_matches('/'));
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs_f64(config.timeout_sec.max(0.1)))
        .build()
        .context("failed to build reqwest OCR client")?;
    let response = client
        .post(&endpoint)
        .json(&json!({
            "base64": base64,
            "options": {
                "data.format": "dict"
            }
        }))
        .send()
        .await
        .with_context(|| format!("failed to call {endpoint}"))?;
    let response = response.error_for_status()?;
    let payload = response
        .json::<serde_json::Value>()
        .await
        .context("failed to decode OCR response json")?;
    let code = payload
        .get("code")
        .and_then(|value| value.as_i64())
        .unwrap_or_default();
    if code == 100 || code == 101 {
        return Ok(payload);
    }
    Err(anyhow::anyhow!(
        "Umi-OCR returned code={} payload={}",
        code,
        payload
    ))
}

fn probe_endpoint(base_url: &str) -> bool {
    let Ok(url) = Url::parse(base_url) else {
        return false;
    };
    let host = url.host_str().unwrap_or("127.0.0.1");
    let port = url.port_or_known_default().unwrap_or(1224);
    let Ok(addresses) = std::net::ToSocketAddrs::to_socket_addrs(&(host, port)) else {
        return false;
    };
    addresses
        .into_iter()
        .any(|addr| try_connect(addr, Duration::from_millis(400)))
}

fn try_connect(addr: SocketAddr, timeout: Duration) -> bool {
    TcpStream::connect_timeout(&addr, timeout).is_ok()
}
