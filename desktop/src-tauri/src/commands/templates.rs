use std::path::PathBuf;

use tauri::State;
use uuid::Uuid;

use crate::app::state::AppState;
use crate::app::types::TemplateConfig;
use crate::automation::capture::{CaptureRegion, capture_full_screen, save_region_png};
use crate::automation::common::goods_inner_region_from_outer;
use crate::automation::native_capture::{NativeCaptureOptions, select_region};
use crate::automation::vision::{
    TemplateFileValidationResult, TemplateMatchResult, TemplateProbeResult, probe_template_in_image_fast,
    test_template, validate_template_file,
};

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
pub fn templates_validate_file(
    state: State<'_, AppState>,
    path: String,
) -> Result<TemplateFileValidationResult, String> {
    let absolute = state.config_service.resolve_template_absolute_path(&path);
    validate_template_file(&absolute).map_err(|error| error.to_string())
}

#[tauri::command]
pub fn templates_probe_match(
    state: State<'_, AppState>,
    path: String,
    target: Option<String>,
) -> Result<TemplateProbeResult, String> {
    let absolute = state.config_service.resolve_template_absolute_path(&path);
    let threshold = resolve_template_threshold(&state, &path);
    let target = target.unwrap_or_else(|| "screen".to_string());
    if looks_like_path(&target) {
        let image = image::open(PathBuf::from(&target))
            .map_err(|error| error.to_string())?
            .to_rgba8();
        return probe_template_in_image_fast(&image, &absolute, threshold, None)
            .map(apply_python_probe_feedback)
            .map_err(|error| error.to_string());
    }
    let captured = capture_full_screen().map_err(|error| error.to_string())?;
    probe_template_in_image_fast(&captured.image, &absolute, threshold, None)
        .map(apply_python_probe_feedback)
        .map_err(|error| error.to_string())
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
pub fn templates_capture_interactive(
    state: State<'_, AppState>,
    slug: String,
) -> Result<String, String> {
    let region = select_region(NativeCaptureOptions::template())
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "capture cancelled".to_string())?;
    templates_capture_region(state, slug, region)
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
    let inner = goods_inner_region_from_outer(&region);
    let relative_path = state
        .config_service
        .goods_relative_path(&big_category, &format!("{}.png", Uuid::new_v4()));
    let target = state
        .config_service
        .relative_to_absolute_data_path(&relative_path);
    save_region_png(&inner, &target).map_err(|error| error.to_string())?;
    Ok(relative_path)
}

#[tauri::command]
pub fn goods_capture_card_interactive(
    state: State<'_, AppState>,
    big_category: String,
) -> Result<String, String> {
    let region = select_region(NativeCaptureOptions::goods_card())
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "capture cancelled".to_string())?;
    goods_capture_card_image(state, big_category, region)
}

fn resolve_template_threshold(state: &State<'_, AppState>, path: &str) -> f64 {
    let normalized = state.config_service.resolve_template_absolute_path(path);
    let Ok(items) = state.config_service.list_templates() else {
        return 0.85;
    };
    items.into_iter()
        .find(|item| state.config_service.resolve_template_absolute_path(&item.path) == normalized)
        .map(|item| item.confidence.max(0.1))
        .unwrap_or(0.85)
}

fn looks_like_path(value: &str) -> bool {
    let path = PathBuf::from(value);
    path.is_absolute() || path.exists()
}

fn apply_python_probe_feedback(mut result: TemplateProbeResult) -> TemplateProbeResult {
    if result.matched {
        result.message = "识别成功".to_string();
    } else {
        result.box_rect = None;
        result.message = "未匹配到".to_string();
    }
    result
}

#[cfg(test)]
mod tests {
    use crate::automation::capture::CaptureRegion;
    use crate::automation::vision::TemplateProbeResult;

    use super::apply_python_probe_feedback;

    #[test]
    fn keeps_match_box_and_sets_success_message() {
        let result = apply_python_probe_feedback(TemplateProbeResult {
            matched: true,
            confidence: 0.97,
            box_rect: Some(CaptureRegion {
                x: 12,
                y: 24,
                width: 36,
                height: 48,
            }),
            message: "模板命中，score=0.970".to_string(),
        });
        assert!(result.matched);
        assert_eq!(result.message, "识别成功");
        assert!(result.box_rect.is_some());
    }

    #[test]
    fn clears_box_and_sets_warning_message_when_probe_misses() {
        let result = apply_python_probe_feedback(TemplateProbeResult {
            matched: false,
            confidence: 0.61,
            box_rect: Some(CaptureRegion {
                x: 4,
                y: 8,
                width: 16,
                height: 20,
            }),
            message: "模板未命中，score=0.610，threshold=0.850".to_string(),
        });
        assert!(!result.matched);
        assert_eq!(result.message, "未匹配到");
        assert!(result.box_rect.is_none());
    }
}
