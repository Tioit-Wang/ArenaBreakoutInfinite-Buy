use tauri::State;
use uuid::Uuid;

use crate::app::state::AppState;
use crate::app::types::TemplateConfig;
use crate::automation::capture::{CaptureRegion, save_region_png};
use crate::automation::vision::{TemplateMatchResult, test_template};

#[tauri::command]
pub fn templates_list(state: State<'_, AppState>) -> Result<Vec<TemplateConfig>, String> {
    state
        .config_service
        .list_templates()
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn templates_save(
    state: State<'_, AppState>,
    template: TemplateConfig,
) -> Result<TemplateConfig, String> {
    state
        .config_service
        .upsert_template(&template)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn templates_test(
    state: State<'_, AppState>,
    path: String,
) -> Result<TemplateMatchResult, String> {
    let absolute = state.config_service.resolve_template_absolute_path(&path);
    let absolute = absolute.to_string_lossy().to_string();
    test_template(&absolute).map_err(|error| error.to_string())
}

#[tauri::command]
pub fn templates_import_image(
    state: State<'_, AppState>,
    slug: String,
    source_path: String,
) -> Result<String, String> {
    let relative_path = state.config_service.template_relative_path(&slug);
    let target = state
        .config_service
        .relative_to_absolute_data_path(&relative_path);
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    std::fs::copy(&source_path, &target).map_err(|error| error.to_string())?;
    Ok(relative_path)
}

#[tauri::command]
pub fn templates_capture_region(
    state: State<'_, AppState>,
    slug: String,
    region: CaptureRegion,
) -> Result<String, String> {
    let relative_path = state.config_service.template_relative_path(&slug);
    let target = state
        .config_service
        .relative_to_absolute_data_path(&relative_path);
    save_region_png(&region, &target).map_err(|error| error.to_string())?;
    Ok(relative_path)
}

#[tauri::command]
pub fn goods_import_image(
    state: State<'_, AppState>,
    source_path: String,
    big_category: String,
) -> Result<String, String> {
    let extension = std::path::Path::new(&source_path)
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("png");
    let relative_path = state
        .config_service
        .goods_relative_path(&big_category, &format!("{}.{}", Uuid::new_v4(), extension));
    let target = state
        .config_service
        .relative_to_absolute_data_path(&relative_path);
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    std::fs::copy(&source_path, &target).map_err(|error| error.to_string())?;
    Ok(relative_path)
}

#[tauri::command]
pub fn goods_capture_card_image(
    state: State<'_, AppState>,
    big_category: String,
    region: CaptureRegion,
) -> Result<String, String> {
    const CARD_W: i32 = 165;
    const CARD_H: i32 = 212;
    const TOP_H: i32 = 20;
    const BTM_H: i32 = 30;
    const MARG_LR: i32 = 30;
    const MARG_TB: i32 = 20;
    let mid_h = CARD_H - TOP_H - BTM_H;
    let inner = CaptureRegion {
        x: region.x + MARG_LR,
        y: region.y + TOP_H + MARG_TB,
        width: CARD_W - (MARG_LR * 2),
        height: mid_h - (MARG_TB * 2),
    };
    let relative_path = state
        .config_service
        .goods_relative_path(&big_category, &format!("{}.png", Uuid::new_v4()));
    let target = state
        .config_service
        .relative_to_absolute_data_path(&relative_path);
    save_region_png(&inner, &target).map_err(|error| error.to_string())?;
    Ok(relative_path)
}
