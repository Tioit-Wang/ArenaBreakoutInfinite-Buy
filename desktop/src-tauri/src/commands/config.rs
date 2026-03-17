use tauri::State;

use crate::app::state::AppState;
use crate::app::types::AppConfig;

#[tauri::command]
pub fn config_get(state: State<'_, AppState>) -> Result<AppConfig, String> {
    state
        .config_service
        .get()
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn config_save(state: State<'_, AppState>, config: AppConfig) -> Result<AppConfig, String> {
    state
        .config_service
        .save(&config)
        .map_err(|error| error.to_string())
}
