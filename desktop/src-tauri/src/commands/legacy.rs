use tauri::State;

use crate::app::state::AppState;
use crate::app::types::{ImportReport, LegacyCandidate};

#[tauri::command]
pub fn legacy_scan(state: State<'_, AppState>) -> Result<Vec<LegacyCandidate>, String> {
    state
        .legacy_importer
        .scan()
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn legacy_import(
    state: State<'_, AppState>,
    source_root: String,
) -> Result<ImportReport, String> {
    state
        .legacy_importer
        .import(&source_root)
        .map_err(|error| error.to_string())
}
