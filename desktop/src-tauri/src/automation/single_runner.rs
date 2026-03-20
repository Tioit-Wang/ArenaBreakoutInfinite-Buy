use std::collections::HashMap;
use std::path::Path;
use std::process::Command;
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
};
use std::time::{Duration, Instant};

use anyhow::{Context, Result, anyhow, bail};
use image::GrayImage;
use tokio::time::sleep;
use uuid::Uuid;

use crate::app::types::{
    AppConfig, AutomationEvent, GoodsRecord, PriceHistoryRecord, PurchaseHistoryRecord,
    SingleTaskRecord, TemplateConfig, iso_to_epoch, now_iso,
};
use crate::automation::capture::CapturedImage;
use crate::automation::common::{
    avg_price_roi, crop_gray, infer_qty_from_max, parse_digits, parse_price_text,
    price_with_premium, resize, threshold, top_half,
};
use crate::automation::debug_recorder::{DebugRecorder, RoundStatus};
use crate::automation::ocr::OcrTextBlock;
use crate::automation::ocr::recognize_text;
use crate::automation::capture::capture_full_screen;
use crate::automation::session_support::{
    SessionDebugSupport, TemplateProbeEntry, center_of_box, global_box_to_local, local_box_to_global,
    optional_str, resolve_path, split_args,
};
use crate::automation::vision::MatchBox;
use crate::config::paths::AppPaths;
use crate::storage::repository::Repository;

const STEP_1: &str = "步骤1-全局启动与准备";
const STEP_3: &str = "步骤3-障碍清理与初始化检查";
const STEP_4: &str = "步骤4-搜索与列表定位";
const STEP_6: &str = "步骤6-价格读取与阈值判定";
const STEP_8: &str = "步骤8-会话内循环与退出条件";
const STEP_CAPTURE: &str = "调试模式";

type SharedEmitter = Arc<Mutex<Box<dyn FnMut(AutomationEvent) + Send>>>;

type ScreenPoint = (i32, i32);

#[derive(Clone)]
pub struct SingleRunRequest {
    pub task: SingleTaskRecord,
    pub goods: GoodsRecord,
    pub config: AppConfig,
    pub templates: Vec<TemplateConfig>,
    pub paths: Arc<AppPaths>,
    pub repo: Arc<Repository>,
    pub stop_requested: Arc<AtomicBool>,
}

#[allow(dead_code)]
#[derive(Debug, Clone, Copy)]
struct ClickPoints {
    center: ScreenPoint,
    top_left: ScreenPoint,
    top_right: ScreenPoint,
    bottom_left: ScreenPoint,
    bottom_right: ScreenPoint,
}

#[derive(Debug, Clone, Copy)]
struct CachedClickTarget {
    rect: MatchBox,
    points: ClickPoints,
}

#[derive(Default)]
struct DetailCache {
    controls: HashMap<String, CachedClickTarget>,
    qty_mid: Option<ScreenPoint>,
}

#[derive(Debug, Default, Clone, Copy)]
struct PurchaseOutcome {
    bought: i64,
    round_completed: bool,
    restock_triggered: bool,
}

struct SingleSession {
    request: SingleRunRequest,
    emitter: SharedEmitter,
    session_id: String,
    templates: HashMap<String, TemplateProbeEntry>,
    goods_target: Option<CachedClickTarget>,
    detail: DetailCache,
    detail_open_hint: bool,
    ocr_miss_streak: u32,
    last_avg_ok: Instant,
    completed_rounds: u32,
    last_restock_trigger_at: Option<Instant>,
    debug: DebugRecorder,
}

pub async fn run_single_flow(
    request: SingleRunRequest,
    emit: impl FnMut(AutomationEvent) + Send + 'static,
    session_id: String,
) -> Result<()> {
    let emitter: SharedEmitter = Arc::new(Mutex::new(Box::new(emit)));
    let mut session = SingleSession::new(request, emitter, session_id);
    session.run().await
}

impl Drop for SingleSession {
    fn drop(&mut self) {
        let status = if self.request.stop_requested.load(Ordering::Relaxed) {
            RoundStatus::Stopped
        } else {
            RoundStatus::Failed
        };
        if let Some(flush) = self.debug.flush_active_round_on_drop(status) {
            self.log(
                "info",
                STEP_CAPTURE,
                format!(
                    "调试轮次已写入，round={} status={} steps={} truncated={} dir={}",
                    flush.round_index,
                    flush.status.as_str(),
                    flush.step_count,
                    flush.truncated,
                    flush.round_dir.display()
                ),
                None,
            );
        }
    }
}

impl SingleSession {
    fn new(request: SingleRunRequest, emitter: SharedEmitter, session_id: String) -> Self {
        let templates = request
            .templates
            .iter()
            .map(|item| {
                (
                    item.slug.clone(),
                    TemplateProbeEntry {
                        path: resolve_path(&request.paths, &item.path),
                        confidence: item.confidence.max(0.1),
                    },
                )
            })
            .collect();
        let debug_dir = request.paths.debug_dir.clone();
        let debug_enabled = request.config.debug.single_enabled;
        let debug_session_id = session_id.clone();
        Self {
            request,
            emitter,
            session_id,
            templates,
            goods_target: None,
            detail: DetailCache::default(),
            detail_open_hint: false,
            ocr_miss_streak: 0,
            last_avg_ok: Instant::now(),
            completed_rounds: 0,
            last_restock_trigger_at: None,
            debug: DebugRecorder::new(&debug_dir, "single", &debug_session_id, debug_enabled),
        }
    }

    async fn run(&mut self) -> Result<()> {
        self.validate()?;
        self.announce_debug_session();
        self.log("info", STEP_1, "开始执行单商品会话", Some(0.05));
        self.ensure_ready().await?;
        self.clear_obstacles().await?;
        self.build_search_context().await?;

        let mut task = self.request.task.clone();
        while !target_reached(&task) {
            self.debug.begin_round();
            let outcome = self.purchase_once(&mut task).await?;
            if outcome.bought > 0 {
                task.purchased += outcome.bought;
                task.updated_at = now_iso();
                self.request.repo.save_single_task(&task)?;
            }
            if outcome.round_completed {
                self.completed_rounds = self.completed_rounds.saturating_add(1);
                if outcome.restock_triggered {
                    self.last_restock_trigger_at = Some(Instant::now());
                }
                if self.apply_round_cooldown_if_needed().await? {
                    self.last_restock_trigger_at = None;
                }
            }
            if self.apply_restock_window_cooldown_if_needed().await? {
                self.last_restock_trigger_at = None;
            }
            if self.ocr_miss_streak
                >= self
                    .request
                    .config
                    .multi_snipe_tuning
                    .ocr_miss_penalty_threshold
                    .max(1)
                && self.last_avg_ok.elapsed()
                    >= Duration::from_secs_f64(
                        self.request
                            .config
                            .multi_snipe_tuning
                            .penalty_confirm_delay_sec
                            .max(2.0),
                    )
            {
                self.handle_penalty().await?;
            }
            self.nap(Duration::from_secs_f64(
                self.request
                    .config
                    .multi_snipe_tuning
                    .poll_step_sec
                    .max(0.02),
            ))
            .await;
            self.finish_debug_round(RoundStatus::Completed);
        }

        self.log(
            "info",
            STEP_8,
            format!("会话结束，已购 {}/{}", task.purchased, task.target_total),
            Some(1.0),
        );
        Ok(())
    }

    fn validate(&self) -> Result<()> {
        if self.request.goods.search_name.trim().is_empty() {
            bail!("goods.search_name is empty");
        }
        let goods_image = resolve_path(&self.request.paths, &self.request.goods.image_path);
        if !goods_image.exists() {
            bail!("goods image missing: {}", goods_image.display());
        }
        Ok(())
    }

    async fn ensure_ready(&mut self) -> Result<()> {
        if self.find_scene_visible().await? {
            self.log(
                "info",
                STEP_1,
                "已检测到首页/市场标识，跳过启动",
                Some(0.08),
            );
            return Ok(());
        }
        let game = self.request.config.game.clone();
        if game.exe_path.trim().is_empty() || !Path::new(&game.exe_path).exists() {
            bail!("game.exe_path is invalid");
        }
        let mut command = Command::new(&game.exe_path);
        for arg in split_args(&game.launch_args) {
            command.arg(arg);
        }
        if let Some(dir) = Path::new(&game.exe_path).parent() {
            command.current_dir(dir);
        }
        command
            .spawn()
            .with_context(|| format!("failed to launch {}", game.exe_path))?;
        let launch_button = self
            .wait_any_template(
                "btn_launch",
                Duration::from_secs(game.launcher_timeout_sec.max(1)),
            )
            .await?
            .ok_or_else(|| anyhow!("launch button not found"))?;
        self.nap(Duration::from_secs(game.launch_click_delay_sec))
            .await;
        self.click_box_step("click_launch_button", launch_button)?;
        self.log(
            "info",
            STEP_1,
            "已点击启动按钮，等待首页/市场标识",
            Some(0.1),
        );
        let scene_visible = self
            .wait_any_scene(Duration::from_secs(game.startup_timeout_sec.max(1)))
            .await?;
        if !scene_visible {
            return Err(anyhow!("home/market indicator not found"));
        }
        Ok(())
    }

    async fn clear_obstacles(&mut self) -> Result<()> {
        if self
            .locate_active("buy_ok", Duration::from_millis(80), None)
            .await?
            .is_some()
        {
            self.dismiss_overlay().await?;
        }
        if self.detail_open_hint {
            if let Some(target) = self.detail.controls.get("btn_close").copied() {
                self.click_target_step("dismiss_cached_detail", target)?;
                self.nap(Duration::from_secs_f64(
                    self.request
                        .config
                        .multi_snipe_tuning
                        .post_close_detail_sec
                        .max(0.05),
                ))
                .await;
                self.detail_open_hint = false;
                self.log("debug", STEP_3, "使用缓存关闭遗留详情", None);
                return Ok(());
            }
        }
        if let Some(box_rect) = self
            .locate_active("btn_close", Duration::from_millis(80), None)
            .await?
        {
            if self
                .locate_active("btn_buy", Duration::from_millis(50), None)
                .await?
                .is_some()
            {
                self.click_box_step("dismiss_overlay_buy_ok", box_rect)?;
                self.nap(Duration::from_secs_f64(
                    self.request
                        .config
                        .multi_snipe_tuning
                        .post_close_detail_sec
                        .max(0.05),
                ))
                .await;
                self.detail_open_hint = false;
            }
        }
        self.log("debug", STEP_3, "障碍清理完成", None);
        Ok(())
    }

    async fn build_search_context(&mut self) -> Result<()> {
        match self.detect_scene().await? {
            "home" => {
                self.clear_cached_positions();
                self.navigate("btn_market").await?;
            }
            "market" => {
                self.navigate("btn_home").await?;
                self.navigate("btn_market").await?;
            }
            _ => bail!("unable to determine scene before search"),
        }
        let input_box = self
            .locate_active("input_search", Duration::from_secs(2), None)
            .await?
            .ok_or_else(|| anyhow!("search input not found"))?;
        self.click_box_step("focus_search_input", input_box)?;
        self.nap(Duration::from_millis(30)).await;
        let search_name = self.request.goods.search_name.clone();
        self.type_text_step(
            "type_search_name",
            Some(input_box),
            &search_name,
        )?;
        self.nap(Duration::from_millis(30)).await;
        let search_box = self
            .locate_active("btn_search", Duration::from_secs(1), None)
            .await?
            .ok_or_else(|| anyhow!("search button not found"))?;
        self.click_box_step("click_search_button", search_box)?;
        self.nap(Duration::from_millis(40)).await;
        let goods_box = self
            .locate_goods(Duration::from_secs_f64(2.5), None)
            .await?
            .ok_or_else(|| anyhow!("goods template not found"))?;
        self.goods_target = Some(cached_click_target(goods_box));
        self.log("info", STEP_4, "已建立搜索上下文并缓存商品卡片", Some(0.32));
        Ok(())
    }

    async fn purchase_once(&mut self, task: &mut SingleTaskRecord) -> Result<PurchaseOutcome> {
        if !self.open_detail().await? {
            self.log("info", STEP_6, "打开详情失败，等待下一轮", Some(0.46));
            return Ok(PurchaseOutcome::default());
        }
        let Some(unit_price) = self.read_avg_price().await? else {
            self.ocr_miss_streak += 1;
            self.clear_obstacles().await?;
            return Ok(PurchaseOutcome::default());
        };
        self.last_avg_ok = Instant::now();
        self.ocr_miss_streak = 0;
        let normal_limit = price_with_premium(task.price_threshold, task.price_premium_pct);
        let restock_limit = price_with_premium(task.restock_price, task.restock_premium_pct);
        if !price_sane(unit_price, task) {
            let _ = self.close_detail().await?;
            return Ok(PurchaseOutcome::default());
        }
        self.record_price(unit_price)?;

        let mut outcome = PurchaseOutcome {
            round_completed: true,
            ..PurchaseOutcome::default()
        };

        let (qty, used_max, fast_mode) = if task.restock_price > 0 && unit_price <= restock_limit {
            outcome.restock_triggered = true;
            let (qty, used_max) = self.prepare_restock_qty().await?;
            (
                qty,
                used_max,
                self.request.config.multi_snipe_tuning.fast_chain_mode,
            )
        } else if task.price_threshold > 0 && unit_price <= normal_limit {
            (
                if self.request.goods.big_category.trim() == "弹药" {
                    10
                } else {
                    1
                },
                false,
                self.request.config.multi_snipe_tuning.fast_chain_mode,
            )
        } else {
            let _ = self.close_detail().await?;
            return Ok(outcome);
        };

        let buy_target = self
            .resolve_detail_control("btn_buy", Duration::from_millis(300))
            .await?
            .ok_or_else(|| anyhow!("btn_buy missing"))?;
        let max_chain = self.request.config.multi_snipe_tuning.fast_chain_max.max(1) as usize;
        let interval = Duration::from_secs_f64(
            self.request
                .config
                .multi_snipe_tuning
                .fast_chain_interval_ms
                .max(30.0)
                / 1000.0,
        );

        if !fast_mode || max_chain <= 1 {
            self.click_target_step("click_buy_button", buy_target)?;
            self.nap(Duration::from_secs_f64(
                self.request
                    .config
                    .multi_snipe_tuning
                    .buy_click_settle_sec
                    .max(0.0),
            ))
            .await;
            if !self.wait_buy_ok().await? {
                let _ = self.close_detail().await?;
                return Ok(outcome);
            }
            self.record_purchase(task, unit_price, qty, used_max)?;
            self.dismiss_overlay().await?;
            outcome.bought = qty;
            return Ok(outcome);
        }

        self.click_target_step("click_buy_button", buy_target)?;
        self.nap(Duration::from_secs_f64(
            self.request
                .config
                .multi_snipe_tuning
                .buy_click_settle_sec
                .max(0.0),
        ))
        .await;
        let mut bought = 0;
        for idx in 0..max_chain {
            if !self.wait_buy_ok().await? {
                let _ = self.close_detail().await?;
                outcome.bought = bought;
                return Ok(outcome);
            }
            bought += qty;
            self.record_purchase(task, unit_price, qty, used_max)?;
            if task.target_total > 0 && task.purchased + bought >= task.target_total {
                self.dismiss_overlay().await?;
                let _ = self.close_detail().await?;
                outcome.bought = bought;
                return Ok(outcome);
            }
            if idx + 1 >= max_chain {
                self.dismiss_overlay().await?;
                outcome.bought = bought;
                return Ok(outcome);
            }
            self.click_target_step("fast_chain_click_buy_1", buy_target)?;
            self.nap(interval).await;
            self.click_target_step("fast_chain_click_buy_2", buy_target)?;
            self.nap(interval).await;
        }
        outcome.bought = bought;
        Ok(outcome)
    }

    async fn prepare_restock_qty(&mut self) -> Result<(i64, bool)> {
        if self.request.goods.big_category.trim() == "弹药" {
            if let Some(max_target) = self
                .resolve_detail_control("btn_max", Duration::from_millis(220))
                .await?
            {
                self.click_target_step("click_btn_max", max_target)?;
                self.nap(Duration::from_millis(60)).await;
                return Ok((self.read_qty().await?.unwrap_or(120).max(1), true));
            }
            if self.focus_type_qty(120).await? {
                return Ok((self.read_qty().await?.unwrap_or(120).max(1), false));
            }
            return Ok((10, false));
        }
        if self.focus_type_qty(5).await? {
            return Ok((self.read_qty().await?.unwrap_or(5).max(1), false));
        }
        Ok((1, false))
    }

    async fn focus_type_qty(&mut self, qty: i64) -> Result<bool> {
        let Some(qty_center) = self.resolve_qty_mid().await? else {
            return Ok(false);
        };
        let qty_roi = self.qty_roi();
        self.click_point_step("focus_qty_input", qty_center, qty_roi)?;
        self.nap(Duration::from_millis(30)).await;
        self.type_text_step("type_qty_value", qty_roi, &qty.to_string())?;
        self.nap(Duration::from_millis(30)).await;
        Ok(true)
    }

    async fn read_qty(&mut self) -> Result<Option<i64>> {
        let Some(roi) = self.qty_roi() else {
            return Ok(None);
        };
        let captured = self.capture_screen("read_qty_window")?;
        let local_roi =
            global_box_to_local(&captured, roi).ok_or_else(|| anyhow!("qty roi is outside screen"))?;
        let gray = crop_gray(&captured.image, local_roi)?;
        let image = threshold(resize(gray, 2.0));
        let texts = self
            .recognize_text_step("read_qty", &captured, roi, &image)
            .await?;
        Ok(texts
            .into_iter()
            .filter_map(|item| parse_digits(&item.text))
            .max())
    }

    async fn read_avg_price(&mut self) -> Result<Option<i64>> {
        let rounds = self
            .request
            .config
            .multi_snipe_tuning
            .ocr_round_fail_limit
            .max(1);
        let window = Duration::from_secs_f64(
            self.request
                .config
                .multi_snipe_tuning
                .ocr_round_window_sec
                .max(0.2),
        );
        let step = Duration::from_secs_f64(
            self.request
                .config
                .multi_snipe_tuning
                .ocr_round_step_sec
                .max(0.015),
        );
        for _ in 0..rounds {
            let deadline = Instant::now() + window;
            while Instant::now() < deadline {
                if let Some(value) = self.try_read_avg_price().await? {
                    self.log("info", STEP_6, format!("识别到均价={value}"), Some(0.62));
                    return Ok(Some(value));
                }
                self.nap(step).await;
            }
        }
        Ok(None)
    }

    async fn try_read_avg_price(&mut self) -> Result<Option<i64>> {
        let buy_target = self
            .resolve_detail_control("btn_buy", Duration::from_millis(300))
            .await?
            .ok_or_else(|| anyhow!("btn_buy missing"))?;
        let captured = self.capture_screen("avg_price_window")?;
        let local_buy_box = global_box_to_local(&captured, buy_target.rect)
            .ok_or_else(|| anyhow!("btn_buy is outside screen"))?;
        let local_roi = avg_price_roi(
            local_buy_box,
            self.request.goods.exchangeable,
            &self.request.config,
            captured.width,
            captured.height,
        );
        let roi = local_box_to_global(&captured, local_roi);
        let gray = crop_gray(&captured.image, local_roi)?;
        let image = threshold(resize(
            top_half(gray),
            self.request.config.avg_price_area.scale,
        ));
        let texts = self
            .recognize_text_step("avg_price", &captured, roi, &image)
            .await?;
        Ok(texts
            .into_iter()
            .filter_map(|item| parse_price_text(&item.text))
            .next())
    }

    async fn wait_buy_ok(&mut self) -> Result<bool> {
        let timeout = Duration::from_secs_f64(
            self.request
                .config
                .multi_snipe_tuning
                .buy_result_timeout_sec
                .max(0.25),
        );
        let step = Duration::from_secs_f64(
            self.request
                .config
                .multi_snipe_tuning
                .buy_result_poll_step_sec
                .max(0.01),
        );
        let deadline = Instant::now() + timeout;
        let mut saw_fail = false;
        while Instant::now() < deadline {
            if self
                .locate_active("buy_ok", Duration::from_millis(0), None)
                .await?
                .is_some()
            {
                return Ok(true);
            }
            if self
                .locate_active("buy_fail", Duration::from_millis(0), None)
                .await?
                .is_some()
            {
                saw_fail = true;
            }
            self.nap(step).await;
        }
        Ok(!saw_fail && self.detail_visible())
    }

    async fn dismiss_overlay(&mut self) -> Result<()> {
        if let Some(buy_target) = self.detail.controls.get("btn_buy").copied() {
            self.capture_screen("dismiss_overlay_buy")?;
            self.click_target_step("dismiss_overlay_by_buy", buy_target)?;
        } else if let Some(ok_box) = self
            .locate_active("buy_ok", Duration::from_millis(120), None)
            .await?
        {
            self.click_box_step("dismiss_overlay_by_ok", ok_box)?;
        }
        self.nap(Duration::from_secs_f64(
            self.request
                .config
                .multi_snipe_tuning
                .post_success_click_sec
                .max(0.05),
        ))
        .await;
        Ok(())
    }

    async fn close_detail(&mut self) -> Result<bool> {
        if let Some(close_target) = self
            .resolve_detail_control("btn_close", Duration::from_millis(220))
            .await?
        {
            self.capture_screen("close_detail")?;
            self.click_target_step("click_close_detail", close_target)?;
            self.nap(Duration::from_secs_f64(
                self.request
                    .config
                    .multi_snipe_tuning
                    .post_close_detail_sec
                    .max(0.05),
            ))
            .await;
            self.detail_open_hint = false;
            return Ok(true);
        }
        Ok(false)
    }

    fn detail_visible(&self) -> bool {
        self.detail_open_hint
    }

    async fn handle_penalty(&mut self) -> Result<()> {
        if self
            .locate_active("penalty_warning", Duration::from_millis(300), None)
            .await?
            .is_none()
        {
            self.ocr_miss_streak = 0;
            return Ok(());
        }
        self.nap(Duration::from_secs_f64(
            self.request
                .config
                .multi_snipe_tuning
                .penalty_confirm_delay_sec
                .max(5.0),
        ))
        .await;
        if let Some(box_rect) = self
            .locate_active("btn_penalty_confirm", Duration::from_secs(2), None)
            .await?
        {
            self.click_box_step("click_penalty_confirm", box_rect)?;
            self.nap(Duration::from_secs_f64(
                self.request
                    .config
                    .multi_snipe_tuning
                    .penalty_wait_sec
                    .max(10.0),
            ))
            .await;
        }
        self.ocr_miss_streak = 0;
        Ok(())
    }

    async fn navigate(&mut self, slug: &str) -> Result<()> {
        let box_rect = self
            .locate_active(slug, Duration::from_secs(2), None)
            .await?
            .ok_or_else(|| anyhow!("template {slug} not found"))?;
        self.click_box_step(&format!("navigate_{slug}"), box_rect)?;
        self.nap(Duration::from_secs_f64(
            self.request
                .config
                .multi_snipe_tuning
                .post_nav_sec
                .max(0.05),
        ))
        .await;
        if slug == "btn_home" {
            self.clear_cached_positions();
        }
        Ok(())
    }

    async fn open_detail(&mut self) -> Result<bool> {
        if self.detail_visible() {
            return Ok(true);
        }
        if let Some(goods_target) = self.goods_target {
            self.capture_screen("open_detail_cached_goods")?;
            self.click_target_step("open_detail_cached_goods", goods_target)?;
            self.nap(Duration::from_secs_f64(
                self.request
                    .config
                    .multi_snipe_tuning
                    .detail_open_settle_sec
                    .max(0.05),
            ))
            .await;
            self.detail_open_hint = true;
            if self.detail.controls.contains_key("btn_buy")
                || self
                    .resolve_detail_control("btn_buy", Duration::from_millis(300))
                    .await?
                    .is_some()
            {
                return Ok(true);
            }
        }
        if let Some(goods_box) = self.locate_goods(Duration::from_secs(2), None).await? {
            self.goods_target = Some(cached_click_target(goods_box));
            self.click_box_step("open_detail_located_goods", goods_box)?;
            self.nap(Duration::from_secs_f64(
                self.request
                    .config
                    .multi_snipe_tuning
                    .detail_open_settle_sec
                    .max(0.05),
            ))
            .await;
            self.detail_open_hint = true;
            if self
                .resolve_detail_control("btn_buy", Duration::from_millis(300))
                .await?
                .is_some()
            {
                return Ok(true);
            }
        }
        self.detail_open_hint = false;
        Ok(false)
    }

    fn qty_roi(&self) -> Option<MatchBox> {
        if let (Some(minus), Some(plus)) = (
            self.detail.controls.get("qty_minus").copied(),
            self.detail.controls.get("qty_plus").copied(),
        ) {
            let left = minus.rect.0 + minus.rect.2 + 2;
            let right = plus.rect.0 - 2;
            if right > left {
                let cy = (minus.rect.1 + minus.rect.3 / 2 + plus.rect.1 + plus.rect.3 / 2) / 2;
                return Some((left, cy - 18, right - left, 36));
            }
        }
        self.detail.qty_mid.map(|mid| (mid.0 - 40, mid.1 - 18, 80, 36))
    }

    fn clear_cached_positions(&mut self) {
        self.goods_target = None;
        self.detail = DetailCache::default();
        self.detail_open_hint = false;
    }

    async fn resolve_detail_control(
        &mut self,
        slug: &str,
        timeout: Duration,
    ) -> Result<Option<CachedClickTarget>> {
        if let Some(target) = self.detail.controls.get(slug).copied() {
            return Ok(Some(target));
        }
        if slug == "btn_max" && self.request.goods.big_category.trim() != "弹药" {
            return Ok(None);
        }
        let Some(box_rect) = self.locate_active(slug, timeout, None).await? else {
            return Ok(None);
        };
        let target = cached_click_target(box_rect);
        self.detail.controls.insert(slug.to_string(), target);
        Ok(Some(target))
    }

    async fn resolve_qty_mid(&mut self) -> Result<Option<ScreenPoint>> {
        if let Some(qty_mid) = self.detail.qty_mid {
            return Ok(Some(qty_mid));
        }
        let minus = self
            .resolve_detail_control("qty_minus", Duration::from_millis(220))
            .await?;
        let plus = self
            .resolve_detail_control("qty_plus", Duration::from_millis(220))
            .await?;
        if let (Some(minus), Some(plus)) = (minus, plus) {
            let qty_mid = (
                (minus.points.center.0 + plus.points.center.0) / 2,
                (minus.points.center.1 + plus.points.center.1) / 2,
            );
            self.detail.qty_mid = Some(qty_mid);
            return Ok(Some(qty_mid));
        }
        if let Some(max_target) = self
            .resolve_detail_control("btn_max", Duration::from_millis(220))
            .await?
        {
            let inferred = infer_qty_from_max(max_target.rect);
            let qty_mid = center_of_box(inferred);
            self.detail.qty_mid = Some(qty_mid);
            return Ok(Some(qty_mid));
        }
        Ok(None)
    }

    async fn apply_round_cooldown_if_needed(&mut self) -> Result<bool> {
        let every_rounds = self
            .request
            .config
            .multi_snipe_tuning
            .round_cooldown_every_n_rounds;
        let cooldown_minutes = self
            .request
            .config
            .multi_snipe_tuning
            .round_cooldown_minutes;
        if every_rounds == 0 || cooldown_minutes <= 0.0 {
            return Ok(false);
        }
        if self.completed_rounds == 0 || self.completed_rounds % every_rounds != 0 {
            return Ok(false);
        }
        self.run_cooldown(
            format!("达到 {} 个成功详情轮，开始冷却 {} 分钟", every_rounds, cooldown_minutes),
            Duration::from_secs_f64(cooldown_minutes * 60.0),
        )
        .await?;
        Ok(true)
    }

    async fn apply_restock_window_cooldown_if_needed(&mut self) -> Result<bool> {
        let window_minutes = self
            .request
            .config
            .multi_snipe_tuning
            .restock_retrigger_window_minutes;
        let cooldown_minutes = self
            .request
            .config
            .multi_snipe_tuning
            .restock_miss_cooldown_minutes;
        let Some(last_triggered_at) = self.last_restock_trigger_at else {
            return Ok(false);
        };
        if window_minutes <= 0.0 || cooldown_minutes <= 0.0 {
            return Ok(false);
        }
        if last_triggered_at.elapsed() < Duration::from_secs_f64(window_minutes * 60.0) {
            return Ok(false);
        }
        self.run_cooldown(
            format!(
                "补货触发后 {} 分钟内未再次命中补货，开始冷却 {} 分钟",
                window_minutes, cooldown_minutes
            ),
            Duration::from_secs_f64(cooldown_minutes * 60.0),
        )
        .await?;
        Ok(true)
    }

    async fn run_cooldown(&mut self, reason: String, duration: Duration) -> Result<()> {
        self.log("info", STEP_8, reason, Some(0.9));
        self.nap(duration).await;
        self.log("info", STEP_8, "冷却结束，继续执行单商品会话", Some(0.92));
        Ok(())
    }

    async fn detect_scene(&mut self) -> Result<&'static str> {
        let screen = self.capture_screen("detect_scene")?;
        self.detect_scene_in_capture(&screen)
    }

    async fn wait_any_scene(&mut self, timeout: Duration) -> Result<bool> {
        let deadline = Instant::now() + timeout;
        loop {
            let screen = self.capture_screen("wait_any_scene")?;
            let scene = self.detect_scene_in_capture(&screen)?;
            if matches!(scene, "home" | "market") {
                return Ok(true);
            }
            if timeout.is_zero() || Instant::now() >= deadline {
                return Ok(false);
            }
            self.nap(Duration::from_millis(180)).await;
        }
    }

    async fn find_scene_visible(&mut self) -> Result<bool> {
        self.wait_any_scene(Duration::from_millis(0)).await
    }

    async fn locate_goods(
        &mut self,
        timeout: Duration,
        region: Option<MatchBox>,
    ) -> Result<Option<MatchBox>> {
        let deadline = Instant::now() + timeout;
        let goods_path = resolve_path(&self.request.paths, &self.request.goods.image_path);
        loop {
            let screen = self.capture_screen("locate_goods")?;
            if let Some(box_rect) = self.locate_template_in_capture(
                &screen,
                "locate_goods",
                "goods",
                &goods_path,
                0.80,
                region,
            )? {
                return Ok(Some(box_rect));
            }
            if timeout.is_zero() || Instant::now() >= deadline {
                return Ok(None);
            }
            self.nap(Duration::from_millis(60)).await;
        }
    }

    fn capture_screen(&mut self, stage: &str) -> Result<CapturedImage> {
        let _ = stage;
        let screen = capture_full_screen()?;
        Ok(screen)
    }
    fn click_target_step(&mut self, stage: &str, target: CachedClickTarget) -> Result<()> {
        self.click_point_step(stage, target.points.center, Some(target.rect))
    }

    async fn recognize_text_step(
        &mut self,
        stage: &str,
        screen: &CapturedImage,
        roi: MatchBox,
        image: &GrayImage,
    ) -> Result<Vec<OcrTextBlock>> {
        let started = Instant::now();
        let texts = recognize_text(&self.request.config.umi_ocr, image).await?;
        let lines = texts.iter().map(|item| item.text.clone()).collect::<Vec<_>>();
        self.debug
            .record_ocr(screen, stage, roi, &lines, started.elapsed())?;
        Ok(texts)
    }

    fn record_price(&self, price: i64) -> Result<()> {
        let observed_at = now_iso();
        self.request.repo.insert_price_history(&PriceHistoryRecord {
            id: format!("price-{}", Uuid::new_v4()),
            item_id: self.request.goods.id.clone(),
            item_name: self.label(),
            category: optional_str(&self.request.goods.big_category),
            price,
            observed_at_epoch: iso_to_epoch(&observed_at),
            observed_at,
        })
    }

    fn record_purchase(
        &self,
        task: &SingleTaskRecord,
        price: i64,
        qty: i64,
        used_max: bool,
    ) -> Result<()> {
        self.request
            .repo
            .insert_purchase_history(&PurchaseHistoryRecord {
                id: format!("buy-{}", Uuid::new_v4()),
                item_id: self.request.goods.id.clone(),
                item_name: self.label(),
                category: optional_str(&self.request.goods.big_category),
                price,
                qty,
                amount: price * qty,
                task_id: Some(task.id.clone()),
                task_name: Some(task.item_name.clone()),
                used_max: Some(used_max),
                purchased_at: now_iso(),
            })
    }

    fn label(&self) -> String {
        if self.request.goods.name.trim().is_empty() {
            self.request.task.item_name.clone()
        } else {
            self.request.goods.name.clone()
        }
    }

    fn log(&self, level: &str, step: &str, message: impl Into<String>, progress: Option<f64>) {
        if let Ok(mut emit) = self.emitter.lock() {
            (emit)(AutomationEvent::log(
                self.session_id.clone(),
                "single".to_string(),
                level.to_string(),
                format!("{}: {}", self.label(), message.into()),
                Some(step.to_string()),
                progress,
            ));
        }
    }

    async fn nap(&self, duration: Duration) {
        sleep(duration).await;
    }
}

impl SessionDebugSupport for SingleSession {
    fn debug_recorder(&mut self) -> &mut DebugRecorder {
        &mut self.debug
    }

    fn debug_recorder_ref(&self) -> &DebugRecorder {
        &self.debug
    }

    fn debug_mode_label(&self) -> &'static str {
        "单商品"
    }

    fn emit_debug_log(&self, level: &str, message: String) {
        self.log(level, STEP_CAPTURE, message, None);
    }

    fn capture_screen_for_debug(&mut self, stage: &str) -> Result<CapturedImage> {
        self.capture_screen(stage)
    }

    fn template_probe_entry(&self, slug: &str) -> Result<TemplateProbeEntry> {
        self.templates
            .get(slug)
            .cloned()
            .ok_or_else(|| anyhow!("template missing: {slug}"))
    }
}

fn cached_click_target(rect: MatchBox) -> CachedClickTarget {
    let left = rect.0;
    let top = rect.1;
    let right = rect.0 + rect.2 - 1;
    let bottom = rect.1 + rect.3 - 1;
    CachedClickTarget {
        rect,
        points: ClickPoints {
            center: center_of_box(rect),
            top_left: (left, top),
            top_right: (right, top),
            bottom_left: (left, bottom),
            bottom_right: (right, bottom),
        },
    }
}

fn price_sane(unit_price: i64, task: &SingleTaskRecord) -> bool {
    let base = if task.restock_price > 0 {
        task.restock_price
    } else {
        task.price_threshold
    };
    base <= 0 || unit_price * 2 > base
}

fn target_reached(task: &SingleTaskRecord) -> bool {
    task.target_total > 0 && task.purchased >= task.target_total
}
