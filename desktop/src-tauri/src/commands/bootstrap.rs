use tauri::State;

use crate::app::state::AppState;
use crate::app::types::AppBootstrap;
use crate::runtime::requirements::build_runtime_preflight;

#[tauri::command]
pub fn bootstrap(state: State<'_, AppState>) -> Result<AppBootstrap, String> {
    let config = state
        .config_service
        .get()
        .map_err(|error| error.to_string())?;
    let ocr_status = state.ocr.status(&config.umi_ocr);
    let runtime_preflight = build_runtime_preflight(&config);
    Ok(AppBootstrap {
        paths: state.paths.snapshot(),
        config,
        templates: state
            .repo
            .list_templates()
            .map_err(|error| error.to_string())?,
        goods: state.repo.list_goods().map_err(|error| error.to_string())?,
        single_tasks: state
            .repo
            .list_single_tasks()
            .map_err(|error| error.to_string())?,
        multi_tasks: state
            .repo
            .list_multi_tasks()
            .map_err(|error| error.to_string())?,
        runtime: state.automation.current_state(),
        ocr_status,
        runtime_preflight,
        legacy_candidates: state
            .legacy_importer
            .scan()
            .map_err(|error| error.to_string())?,
        recent_logs: Vec::new(),
    })
}
