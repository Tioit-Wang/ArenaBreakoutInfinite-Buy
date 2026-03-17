use tauri::{AppHandle, Emitter, State};

use crate::app::state::AppState;
use crate::app::types::{AutomationRunState, OcrStatus};
use crate::automation::input::{click_point, type_text};
use crate::automation::window::WindowSnapshot;
use crate::automation::window::list_windows;
use crate::runtime::events::{OCR_STATUS_EVENT, SIDECAR_STATUS_EVENT};

#[tauri::command]
pub fn automation_start_single(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<AutomationRunState, String> {
    let config = state
        .config_service
        .get()
        .map_err(|error| error.to_string())?;
    let ocr_status = state
        .ocr
        .ensure_started(&config.umi_ocr)
        .map_err(|error| error.to_string())?;
    let _ = app.emit(OCR_STATUS_EVENT, &ocr_status);
    let _ = app.emit(SIDECAR_STATUS_EVENT, &ocr_status);
    let tasks = state
        .repo
        .list_single_tasks()
        .map_err(|error| error.to_string())?;
    let task = tasks
        .iter()
        .find(|item| item.enabled)
        .cloned()
        .or_else(|| tasks.first().cloned())
        .ok_or_else(|| "no single tasks configured".to_string())?;
    let goods = state.repo.list_goods().map_err(|error| error.to_string())?;
    let goods = goods
        .into_iter()
        .find(|item| item.id == task.item_id)
        .ok_or_else(|| format!("goods not found for item_id={}", task.item_id))?;
    let templates = state
        .repo
        .list_templates()
        .map_err(|error| error.to_string())?;
    state
        .automation
        .start_single(app, task, goods, config, templates, state.paths.clone())
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn automation_start_multi(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<AutomationRunState, String> {
    let config = state
        .config_service
        .get()
        .map_err(|error| error.to_string())?;
    let ocr_status = state
        .ocr
        .ensure_started(&config.umi_ocr)
        .map_err(|error| error.to_string())?;
    let _ = app.emit(OCR_STATUS_EVENT, &ocr_status);
    let _ = app.emit(SIDECAR_STATUS_EVENT, &ocr_status);
    let tasks = state
        .repo
        .list_multi_tasks()
        .map_err(|error| error.to_string())?;
    let enabled_tasks = tasks
        .into_iter()
        .filter(|item| item.enabled)
        .collect::<Vec<_>>();
    if enabled_tasks.is_empty() {
        return Err("no enabled multi tasks configured".to_string());
    }
    let templates = state
        .repo
        .list_templates()
        .map_err(|error| error.to_string())?;
    state
        .automation
        .start_multi(app, enabled_tasks, config, templates, state.paths.clone())
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn automation_pause(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<AutomationRunState, String> {
    state
        .automation
        .pause(&app)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn automation_resume(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<AutomationRunState, String> {
    state
        .automation
        .resume(&app)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn automation_stop(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<AutomationRunState, String> {
    let runtime = state
        .automation
        .stop(&app)
        .map_err(|error| error.to_string())?;
    let _ = state.ocr.stop();
    let config = state
        .config_service
        .get()
        .map_err(|error| error.to_string())?;
    let ocr_status = state.ocr.status(&config.umi_ocr);
    let _ = app.emit(OCR_STATUS_EVENT, &ocr_status);
    let _ = app.emit(SIDECAR_STATUS_EVENT, &ocr_status);
    Ok(runtime)
}

#[tauri::command]
pub fn ocr_status(state: State<'_, AppState>) -> Result<OcrStatus, String> {
    let config = state
        .config_service
        .get()
        .map_err(|error| error.to_string())?;
    Ok(state.ocr.status(&config.umi_ocr))
}

#[tauri::command]
pub fn ocr_start(app: AppHandle, state: State<'_, AppState>) -> Result<OcrStatus, String> {
    let config = state
        .config_service
        .get()
        .map_err(|error| error.to_string())?;
    let status = state
        .ocr
        .ensure_started(&config.umi_ocr)
        .map_err(|error| error.to_string())?;
    let _ = app.emit(OCR_STATUS_EVENT, &status);
    let _ = app.emit(SIDECAR_STATUS_EVENT, &status);
    Ok(status)
}

#[tauri::command]
pub fn ocr_stop(app: AppHandle, state: State<'_, AppState>) -> Result<OcrStatus, String> {
    state.ocr.stop().map_err(|error| error.to_string())?;
    let config = state
        .config_service
        .get()
        .map_err(|error| error.to_string())?;
    let status = state.ocr.status(&config.umi_ocr);
    let _ = app.emit(OCR_STATUS_EVENT, &status);
    let _ = app.emit(SIDECAR_STATUS_EVENT, &status);
    Ok(status)
}

#[tauri::command]
pub fn ocr_restart(app: AppHandle, state: State<'_, AppState>) -> Result<OcrStatus, String> {
    state.ocr.stop().map_err(|error| error.to_string())?;
    let config = state
        .config_service
        .get()
        .map_err(|error| error.to_string())?;
    let status = state
        .ocr
        .ensure_started(&config.umi_ocr)
        .map_err(|error| error.to_string())?;
    let _ = app.emit(OCR_STATUS_EVENT, &status);
    let _ = app.emit(SIDECAR_STATUS_EVENT, &status);
    Ok(status)
}

#[tauri::command]
pub fn automation_list_windows() -> Vec<WindowSnapshot> {
    list_windows()
}

#[tauri::command]
pub fn automation_probe_click(x: i32, y: i32) -> Result<(), String> {
    click_point(x, y).map_err(|error| error.to_string())
}

#[tauri::command]
pub fn automation_probe_type_text(value: String) -> Result<(), String> {
    type_text(&value).map_err(|error| error.to_string())
}
