use serde::{Deserialize, Serialize};
use xcap::Window;

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

pub fn list_windows() -> Vec<WindowSnapshot> {
    let mut windows = Vec::new();
    if let Ok(all) = Window::all() {
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
