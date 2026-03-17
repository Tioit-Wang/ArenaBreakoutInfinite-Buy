use tauri::State;

use crate::app::state::AppState;
use crate::app::types::{MultiTaskRecord, SingleTaskRecord};

#[tauri::command]
pub fn single_tasks_list(state: State<'_, AppState>) -> Result<Vec<SingleTaskRecord>, String> {
    state
        .repo
        .list_single_tasks()
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn single_tasks_save(
    state: State<'_, AppState>,
    task: SingleTaskRecord,
) -> Result<SingleTaskRecord, String> {
    state
        .repo
        .save_single_task(&task)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn single_tasks_reorder(
    state: State<'_, AppState>,
    task_ids: Vec<String>,
) -> Result<Vec<SingleTaskRecord>, String> {
    state
        .repo
        .reorder_single_tasks(&task_ids)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn single_tasks_delete(state: State<'_, AppState>, id: String) -> Result<(), String> {
    state
        .repo
        .delete_single_task(&id)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn multi_tasks_list(state: State<'_, AppState>) -> Result<Vec<MultiTaskRecord>, String> {
    state
        .repo
        .list_multi_tasks()
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn multi_tasks_save(
    state: State<'_, AppState>,
    task: MultiTaskRecord,
) -> Result<MultiTaskRecord, String> {
    state
        .repo
        .save_multi_task(&task)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn multi_tasks_reorder(
    state: State<'_, AppState>,
    task_ids: Vec<String>,
) -> Result<Vec<MultiTaskRecord>, String> {
    state
        .repo
        .reorder_multi_tasks(&task_ids)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn multi_tasks_delete(state: State<'_, AppState>, id: String) -> Result<(), String> {
    state
        .repo
        .delete_multi_task(&id)
        .map_err(|error| error.to_string())
}
