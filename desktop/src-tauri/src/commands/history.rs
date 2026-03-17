use tauri::State;

use crate::app::state::AppState;
use crate::app::types::{HistorySummary, PriceHistoryRecord, PurchaseHistoryRecord};

#[tauri::command]
pub fn history_query_prices(
    state: State<'_, AppState>,
    item_id: Option<String>,
    limit: Option<u32>,
) -> Result<Vec<PriceHistoryRecord>, String> {
    state
        .repo
        .query_price_history(item_id.as_deref(), limit.unwrap_or(200))
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn history_query_purchases(
    state: State<'_, AppState>,
    item_id: Option<String>,
    limit: Option<u32>,
) -> Result<Vec<PurchaseHistoryRecord>, String> {
    state
        .repo
        .query_purchase_history(item_id.as_deref(), limit.unwrap_or(200))
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn history_query_summary(
    state: State<'_, AppState>,
    item_id: Option<String>,
) -> Result<HistorySummary, String> {
    state
        .repo
        .summarize_history(item_id.as_deref())
        .map_err(|error| error.to_string())
}
