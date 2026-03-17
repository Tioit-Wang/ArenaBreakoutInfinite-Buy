use tauri::State;

use crate::app::state::AppState;
use crate::app::types::{
    HistorySummary, ItemPriceTrendResponse, PriceHistoryRecord, PurchaseHistoryRecord,
};
use crate::storage::repository::Repository;

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

#[tauri::command]
pub fn history_query_item_price_trend(
    state: State<'_, AppState>,
    item_id: String,
    from: String,
    to: String,
    timezone_offset_min: i32,
) -> Result<ItemPriceTrendResponse, String> {
    history_query_item_price_trend_impl(&state.repo, &item_id, &from, &to, timezone_offset_min)
}

fn history_query_item_price_trend_impl(
    repo: &Repository,
    item_id: &str,
    from: &str,
    to: &str,
    timezone_offset_min: i32,
) -> Result<ItemPriceTrendResponse, String> {
    repo.query_item_price_trend(item_id, from, to, timezone_offset_min)
        .map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::PathBuf;

    use crate::app::types::{GoodsRecord, PriceHistoryRecord, iso_to_epoch, now_iso};

    use super::*;

    fn temp_db_path(label: &str) -> PathBuf {
        let unique = format!(
            "arena-buyer-history-command-{label}-{}.sqlite3",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("system clock before unix epoch")
                .as_nanos()
        );
        std::env::temp_dir().join(unique)
    }

    fn cleanup_db(path: &PathBuf) {
        let _ = fs::remove_file(path);
    }

    fn sample_goods(item_id: &str, name: &str) -> GoodsRecord {
        GoodsRecord {
            id: item_id.to_string(),
            name: name.to_string(),
            search_name: name.to_string(),
            big_category: "物资".to_string(),
            sub_category: "测试".to_string(),
            exchangeable: false,
            craftable: false,
            favorite: false,
            image_path: "images/goods/_default.png".to_string(),
            price: None,
            created_at: now_iso(),
            updated_at: now_iso(),
        }
    }

    #[test]
    fn history_query_item_price_trend_impl_returns_empty_response_without_rows() -> anyhow::Result<()> {
        let path = temp_db_path("empty");
        let repo = Repository::new(path.clone());
        repo.init()?;
        repo.save_goods(&sample_goods("item-empty", "空物品"))?;

        let response = history_query_item_price_trend_impl(
            &repo,
            "item-empty",
            "2026-03-10T00:00:00Z",
            "2026-03-12T23:59:59Z",
            0,
        )
        .map_err(anyhow::Error::msg)?;

        assert_eq!(response.item_id, "item-empty");
        assert_eq!(response.item_name, "空物品");
        assert!(response.points.is_empty());
        assert_eq!(response.latest_price, None);
        assert_eq!(response.range_min_price, None);
        assert_eq!(response.range_max_price, None);
        assert_eq!(response.range_avg_price, None);

        cleanup_db(&path);
        Ok(())
    }

    #[test]
    fn history_query_item_price_trend_impl_returns_trend_payload() -> anyhow::Result<()> {
        let path = temp_db_path("filled");
        let repo = Repository::new(path.clone());
        repo.init()?;
        repo.save_goods(&sample_goods("item-1", "急救包"))?;
        repo.insert_price_history(&PriceHistoryRecord {
            id: "price-1".to_string(),
            item_id: "item-1".to_string(),
            item_name: "急救包".to_string(),
            category: Some("物资".to_string()),
            price: 88,
            observed_at: "2026-03-10T08:00:00Z".to_string(),
            observed_at_epoch: iso_to_epoch("2026-03-10T08:00:00Z"),
        })?;

        let response = history_query_item_price_trend_impl(
            &repo,
            "item-1",
            "2026-03-10T00:00:00Z",
            "2026-03-10T23:59:59Z",
            0,
        )
        .map_err(anyhow::Error::msg)?;

        assert_eq!(response.item_name, "急救包");
        assert_eq!(response.latest_price, Some(88));
        assert_eq!(response.points.len(), 1);
        assert_eq!(response.points[0].day, "2026-03-10");
        assert_eq!(response.points[0].max_price, 88);

        cleanup_db(&path);
        Ok(())
    }
}
