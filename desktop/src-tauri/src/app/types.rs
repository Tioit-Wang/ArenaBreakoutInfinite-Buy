use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PathsSnapshot {
    pub root_dir: String,
    pub data_dir: String,
    pub images_dir: String,
    pub assets_dir: String,
    pub debug_dir: String,
    pub logs_dir: String,
    pub cache_dir: String,
    pub db_path: String,
    pub bundled_umi_dir: Option<String>,
    pub bundled_resources_dir: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GameConfig {
    pub exe_path: String,
    pub launch_args: String,
    pub startup_timeout_sec: u64,
    pub launcher_timeout_sec: u64,
    pub launch_click_delay_sec: u64,
}

impl Default for GameConfig {
    fn default() -> Self {
        Self {
            exe_path: String::new(),
            launch_args: String::new(),
            startup_timeout_sec: 180,
            launcher_timeout_sec: 60,
            launch_click_delay_sec: 20,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UmiOcrConfig {
    pub base_url: String,
    pub timeout_sec: f64,
    pub auto_start: bool,
    pub startup_wait_sec: f64,
    pub exe_path: String,
}

impl Default for UmiOcrConfig {
    fn default() -> Self {
        Self {
            base_url: "http://127.0.0.1:1224".to_string(),
            timeout_sec: 2.5,
            auto_start: true,
            startup_wait_sec: 20.0,
            exe_path: String::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HotkeyConfig {
    pub toggle: String,
}

impl Default for HotkeyConfig {
    fn default() -> Self {
        Self {
            toggle: "CommandOrControl+Alt+T".to_string(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DebugConfig {
    pub enabled: bool,
    pub save_roi_on_fail: bool,
    pub overlay_sec: f64,
    pub step_sleep: f64,
    pub save_overlay_images: bool,
    #[serde(default)]
    pub save_single_capture_images: bool,
}

impl Default for DebugConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            save_roi_on_fail: false,
            overlay_sec: 5.0,
            step_sleep: 0.0,
            save_overlay_images: false,
            save_single_capture_images: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AvgPriceAreaConfig {
    pub distance_from_buy_top: i64,
    pub height: i64,
    pub scale: f64,
}

impl Default for AvgPriceAreaConfig {
    fn default() -> Self {
        Self {
            distance_from_buy_top: 5,
            height: 45,
            scale: 1.0,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MultiSnipeTuning {
    pub buy_result_timeout_sec: f64,
    pub buy_result_poll_step_sec: f64,
    pub poll_step_sec: f64,
    pub probe_step_sec: f64,
    pub post_click_wait_sec: f64,
    pub roi_pre_capture_wait_sec: f64,
    pub ocr_max_workers: u32,
    pub ocr_round_window_sec: f64,
    pub ocr_round_step_sec: f64,
    pub ocr_round_fail_limit: u32,
    pub post_close_detail_sec: f64,
    pub post_success_click_sec: f64,
    pub post_nav_sec: f64,
    pub detail_open_settle_sec: f64,
    pub detail_cache_verify_timeout_sec: f64,
    pub anchor_stabilize_sec: f64,
    pub ocr_miss_penalty_threshold: u32,
    pub penalty_confirm_delay_sec: f64,
    pub penalty_wait_sec: f64,
    pub fast_chain_mode: bool,
    pub fast_chain_max: u32,
    pub fast_chain_interval_ms: f64,
    pub relocate_after_fail: u32,
}

impl Default for MultiSnipeTuning {
    fn default() -> Self {
        Self {
            buy_result_timeout_sec: 0.35,
            buy_result_poll_step_sec: 0.01,
            poll_step_sec: 0.02,
            probe_step_sec: 0.06,
            post_click_wait_sec: 0.2,
            roi_pre_capture_wait_sec: 0.05,
            ocr_max_workers: 4,
            ocr_round_window_sec: 0.25,
            ocr_round_step_sec: 0.015,
            ocr_round_fail_limit: 6,
            post_close_detail_sec: 0.05,
            post_success_click_sec: 0.05,
            post_nav_sec: 0.05,
            detail_open_settle_sec: 0.05,
            detail_cache_verify_timeout_sec: 0.18,
            anchor_stabilize_sec: 0.05,
            ocr_miss_penalty_threshold: 10,
            penalty_confirm_delay_sec: 5.0,
            penalty_wait_sec: 180.0,
            fast_chain_mode: true,
            fast_chain_max: 10,
            fast_chain_interval_ms: 35.0,
            relocate_after_fail: 3,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct AppConfig {
    pub game: GameConfig,
    pub umi_ocr: UmiOcrConfig,
    pub hotkeys: HotkeyConfig,
    pub debug: DebugConfig,
    pub avg_price_area: AvgPriceAreaConfig,
    pub multi_snipe_tuning: MultiSnipeTuning,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TemplateConfig {
    pub id: String,
    pub slug: String,
    pub name: String,
    pub kind: String,
    pub path: String,
    pub confidence: f64,
    pub notes: Option<String>,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GoodsRecord {
    pub id: String,
    pub name: String,
    pub search_name: String,
    pub big_category: String,
    pub sub_category: String,
    pub exchangeable: bool,
    pub craftable: bool,
    pub favorite: bool,
    pub image_path: String,
    pub price: Option<i64>,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SingleTaskRecord {
    pub id: String,
    pub item_id: String,
    pub item_name: String,
    pub enabled: bool,
    pub price_threshold: i64,
    pub price_premium_pct: f64,
    pub restock_price: i64,
    pub restock_premium_pct: f64,
    pub target_total: i64,
    pub purchased: i64,
    pub duration_min: i64,
    pub time_start: Option<String>,
    pub time_end: Option<String>,
    pub order_index: i64,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MultiTaskRecord {
    pub id: String,
    pub item_id: String,
    pub name: String,
    pub enabled: bool,
    pub price: i64,
    pub premium_pct: f64,
    pub purchase_mode: String,
    pub target_total: i64,
    pub purchased: i64,
    pub order_index: i64,
    pub image_path: String,
    pub big_category: String,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PriceHistoryRecord {
    pub id: String,
    pub item_id: String,
    pub item_name: String,
    pub category: Option<String>,
    pub price: i64,
    pub observed_at: String,
    #[serde(default)]
    pub observed_at_epoch: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PurchaseHistoryRecord {
    pub id: String,
    pub item_id: String,
    pub item_name: String,
    pub category: Option<String>,
    pub price: i64,
    pub qty: i64,
    pub amount: i64,
    pub task_id: Option<String>,
    pub task_name: Option<String>,
    pub used_max: Option<bool>,
    pub purchased_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct HistorySummary {
    pub price_count: i64,
    pub price_min: i64,
    pub price_max: i64,
    pub price_avg: i64,
    pub latest_price: i64,
    pub purchase_count: i64,
    pub purchase_qty: i64,
    pub purchase_amount: i64,
    pub purchase_avg: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ItemPriceTrendPoint {
    pub day: String,
    pub min_price: i64,
    pub max_price: i64,
    pub avg_price: i64,
    pub latest_price: i64,
    pub sample_count: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ItemPriceTrendResponse {
    pub item_id: String,
    pub item_name: String,
    pub from: String,
    pub to: String,
    pub points: Vec<ItemPriceTrendPoint>,
    pub latest_price: Option<i64>,
    pub range_min_price: Option<i64>,
    pub range_max_price: Option<i64>,
    pub range_avg_price: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LegacyCandidate {
    pub root: String,
    pub display_name: String,
    pub files: Vec<String>,
    pub output_dir: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ImportReport {
    pub id: String,
    pub source_root: String,
    pub status: String,
    pub goods_imported: usize,
    pub single_tasks_imported: usize,
    pub multi_tasks_imported: usize,
    pub price_rows_imported: usize,
    pub purchase_rows_imported: usize,
    pub finished_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OcrStatus {
    pub managed: bool,
    pub ready: bool,
    pub using_existing: bool,
    pub started: bool,
    pub base_url: String,
    pub exe_path: Option<String>,
    pub message: String,
}

impl Default for OcrStatus {
    fn default() -> Self {
        Self {
            managed: false,
            ready: false,
            using_existing: false,
            started: false,
            base_url: UmiOcrConfig::default().base_url,
            exe_path: None,
            message: "OCR runtime not initialized".to_string(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AutomationRunState {
    pub session_id: Option<String>,
    pub mode: Option<String>,
    pub state: String,
    pub detail: Option<String>,
    pub started_at: Option<String>,
    pub updated_at: String,
    pub can_pause: bool,
    pub can_resume: bool,
}

impl Default for AutomationRunState {
    fn default() -> Self {
        Self {
            session_id: None,
            mode: None,
            state: "idle".to_string(),
            detail: None,
            started_at: None,
            updated_at: Utc::now().to_rfc3339(),
            can_pause: false,
            can_resume: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AutomationEvent {
    pub session_id: String,
    pub mode: String,
    pub kind: String,
    pub level: String,
    pub message: String,
    pub step: Option<String>,
    pub progress: Option<f64>,
    pub payload: Value,
    pub created_at: String,
}

impl AutomationEvent {
    pub fn log(
        session_id: impl Into<String>,
        mode: impl Into<String>,
        level: impl Into<String>,
        message: impl Into<String>,
        step: Option<String>,
        progress: Option<f64>,
    ) -> Self {
        Self {
            session_id: session_id.into(),
            mode: mode.into(),
            kind: "log".to_string(),
            level: level.into(),
            message: message.into(),
            step,
            progress,
            payload: json!({}),
            created_at: Utc::now().to_rfc3339(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeLogEntry {
    pub id: Option<i64>,
    pub session_id: Option<String>,
    pub level: String,
    pub scope: String,
    pub message: String,
    pub created_at: String,
    pub payload: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AppBootstrap {
    pub paths: PathsSnapshot,
    pub config: AppConfig,
    pub templates: Vec<TemplateConfig>,
    pub goods: Vec<GoodsRecord>,
    pub single_tasks: Vec<SingleTaskRecord>,
    pub multi_tasks: Vec<MultiTaskRecord>,
    pub runtime: AutomationRunState,
    pub ocr_status: OcrStatus,
    pub legacy_candidates: Vec<LegacyCandidate>,
    pub recent_logs: Vec<RuntimeLogEntry>,
}

pub fn now_iso() -> String {
    let now: DateTime<Utc> = Utc::now();
    now.to_rfc3339()
}

pub fn iso_to_epoch(raw: &str) -> i64 {
    DateTime::parse_from_rfc3339(raw)
        .map(|value| value.timestamp())
        .unwrap_or_default()
}
