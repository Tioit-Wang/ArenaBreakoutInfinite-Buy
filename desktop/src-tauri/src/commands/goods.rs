use tauri::State;

use crate::app::state::AppState;
use crate::app::types::GoodsRecord;

#[tauri::command]
pub fn goods_list(state: State<'_, AppState>) -> Result<Vec<GoodsRecord>, String> {
    state.repo.list_goods().map_err(|error| error.to_string())
}

#[tauri::command]
pub fn goods_save(state: State<'_, AppState>, goods: GoodsRecord) -> Result<GoodsRecord, String> {
    state
        .repo
        .save_goods(&goods)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn goods_delete(state: State<'_, AppState>, id: String) -> Result<(), String> {
    state
        .repo
        .delete_goods(&id)
        .map_err(|error| error.to_string())
}
