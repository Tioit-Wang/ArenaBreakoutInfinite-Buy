use std::path::Path;

use anyhow::{Context, Result};
use image::RgbaImage;
use serde::{Deserialize, Serialize};
use xcap::Window;

use crate::automation::input::click_point;
use crate::automation::vision::{MatchBox, locate_template_in_image};

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct WindowSnapshot {
    pub title: String,
    pub process_name: String,
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
}

#[derive(Debug, Clone, Default)]
pub struct WindowHint {
    pub title: String,
    pub app_name: String,
}

#[derive(Debug, Clone)]
pub struct CapturedWindow {
    pub hint: WindowHint,
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
    pub image: RgbaImage,
}

pub fn list_windows() -> Vec<WindowSnapshot> {
    let mut windows = Vec::new();
    if let Ok(all) = enum_windows() {
        for window in all {
            let title = window.title().unwrap_or_default();
            let process_name = window.app_name().unwrap_or_default();
            let x = window.x().unwrap_or_default();
            let y = window.y().unwrap_or_default();
            let width = window.width().unwrap_or_default() as i32;
            let height = window.height().unwrap_or_default() as i32;
            if title.trim().is_empty() && process_name.trim().is_empty() {
                continue;
            }
            if width <= 0 || height <= 0 {
                continue;
            }
            if window.is_minimized().unwrap_or(false) {
                continue;
            }
            windows.push(WindowSnapshot {
                title,
                process_name,
                x,
                y,
                width,
                height,
            });
        }
    }
    windows
}

pub fn enum_windows() -> Result<Vec<Window>> {
    Ok(Window::all()
        .context("failed to enumerate windows")?
        .into_iter()
        .filter(|window| !window.is_minimized().unwrap_or(false))
        .filter(|window| window.width().unwrap_or_default() >= 200)
        .filter(|window| window.height().unwrap_or_default() >= 120)
        .collect())
}

pub fn capture_window(window: Window) -> Result<CapturedWindow> {
    Ok(CapturedWindow {
        hint: WindowHint {
            title: window.title().unwrap_or_default(),
            app_name: window.app_name().unwrap_or_default(),
        },
        x: window.x().unwrap_or_default(),
        y: window.y().unwrap_or_default(),
        width: window.width().unwrap_or_default() as i32,
        height: window.height().unwrap_or_default() as i32,
        image: window.capture_image().context("failed to capture window")?,
    })
}

pub fn capture_matching_window(hint: &WindowHint) -> Result<Option<CapturedWindow>> {
    for window in enum_windows()? {
        let title = window.title().unwrap_or_default();
        let app_name = window.app_name().unwrap_or_default();
        if title == hint.title && app_name == hint.app_name {
            return Ok(Some(capture_window(window)?));
        }
    }
    Ok(None)
}

pub fn capture_probe_window() -> Result<Option<CapturedWindow>> {
    let windows = enum_windows()?;
    if let Some(window) = windows
        .iter()
        .find(|window| window.is_focused().unwrap_or(false) && !is_self_window(window))
    {
        return capture_window(window.clone()).map(Some);
    }
    if let Some(window) = windows
        .iter()
        .filter(|window| !is_self_window(window))
        .max_by_key(|window| {
            (window.width().unwrap_or_default() as i64) * (window.height().unwrap_or_default() as i64)
        })
    {
        return capture_window(window.clone()).map(Some);
    }
    if let Some(window) = windows.iter().find(|window| window.is_focused().unwrap_or(false)) {
        return capture_window(window.clone()).map(Some);
    }
    Ok(None)
}

pub fn find_in_window(
    window: &CapturedWindow,
    template_path: &Path,
    confidence: f64,
    region: Option<MatchBox>,
) -> Result<Option<MatchBox>> {
    locate_template_in_image(&window.image, template_path, confidence, region)
}

pub fn click_box(window: &CapturedWindow, local: MatchBox) -> Result<()> {
    let (x, y) = to_global(window, local);
    click_point(x, y)
}

pub fn to_global(window: &CapturedWindow, local: MatchBox) -> (i32, i32) {
    (
        window.x + local.0 + local.2 / 2,
        window.y + local.1 + local.3 / 2,
    )
}

fn is_self_window(window: &Window) -> bool {
    window.pid().unwrap_or_default() == std::process::id()
}
