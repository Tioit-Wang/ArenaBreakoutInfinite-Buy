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
use tauri::async_runtime;
use tokio::time::sleep;
use uuid::Uuid;

use crate::app::types::{
    AppConfig, AutomationEvent, MultiTaskRecord, PriceHistoryRecord, PurchaseHistoryRecord,
    TemplateConfig, iso_to_epoch, now_iso,
};
use crate::automation::capture::{CapturedImage, capture_full_screen};
use crate::automation::common::{
    avg_price_roi, bottom_roi_from_card, crop_gray, infer_card_from_goods_match, infer_qty_from_max,
    parse_digits, parse_price_text, price_with_premium, resize, threshold, top_half,
};
use crate::automation::debug_recorder::{DebugRecorder, RoundStatus};
use crate::automation::ocr::recognize_text;
use crate::automation::session_support::{
    SessionDebugSupport, TemplateProbeEntry, center_of_box, global_box_to_local, local_box_to_global,
    optional_str, resolve_path, split_args,
};
use crate::automation::vision::MatchBox;
use crate::config::paths::AppPaths;
use crate::storage::repository::Repository;

const STEP_PREPARE: &str = "步骤1-准备多商品上下文";
const STEP_FAVORITES: &str = "步骤2-刷新并进入收藏页";
const STEP_SCAN: &str = "步骤3-批量读价与筛选";
const STEP_BUY: &str = "步骤4-进入详情并购买";
const STEP_CAPTURE: &str = "调试模式";

type SharedEmitter = Arc<Mutex<Box<dyn FnMut(AutomationEvent) + Send>>>;

#[derive(Clone)]
pub struct MultiRunRequest {
    pub tasks: Vec<MultiTaskRecord>,
    pub config: AppConfig,
    pub templates: Vec<TemplateConfig>,
    pub paths: Arc<AppPaths>,
    pub repo: Arc<Repository>,
    pub stop_requested: Arc<AtomicBool>,
}

#[derive(Clone)]
struct PriceScanJob {
    task_id: String,
    task_name: String,
    card_box: MatchBox,
    roi: MatchBox,
    screen: Arc<CapturedImage>,
    image: GrayImage,
}

#[derive(Clone)]
struct PriceScanResult {
    task_id: String,
    task_name: String,
    card_box: MatchBox,
    roi: MatchBox,
    screen: Arc<CapturedImage>,
    price: Option<i64>,
    texts: Vec<String>,
    elapsed: Duration,
}

struct MultiSession {
    request: MultiRunRequest,
    tasks: Vec<MultiTaskRecord>,
    emitter: SharedEmitter,
    session_id: String,
    templates: HashMap<String, TemplateProbeEntry>,
    detail: HashMap<String, MatchBox>,
    qty_mid: Option<MatchBox>,
    grid_region: Option<MatchBox>,
    card_cache: HashMap<String, MatchBox>,
    fail_counts: HashMap<String, u32>,
    ocr_miss_streak: u32,
    last_ocr_ok: Instant,
    debug: DebugRecorder,
}

pub async fn run_multi_flow(
    request: MultiRunRequest,
    emit: impl FnMut(AutomationEvent) + Send + 'static,
    session_id: String,
) -> Result<()> {
    let emitter: SharedEmitter = Arc::new(Mutex::new(Box::new(emit)));
    let mut session = MultiSession::new(request, emitter, session_id);
    session.run().await
}

impl Drop for MultiSession {
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

impl MultiSession {
    fn new(request: MultiRunRequest, emitter: SharedEmitter, session_id: String) -> Self {
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
        let debug_enabled = request.config.debug.multi_enabled;
        let debug_session_id = session_id.clone();
        Self {
            tasks: request.tasks.clone(),
            request,
            emitter,
            session_id,
            templates,
            detail: HashMap::new(),
            qty_mid: None,
            grid_region: None,
            card_cache: HashMap::new(),
            fail_counts: HashMap::new(),
            ocr_miss_streak: 0,
            last_ocr_ok: Instant::now(),
            debug: DebugRecorder::new(&debug_dir, "multi", &debug_session_id, debug_enabled),
        }
    }

    async fn run(&mut self) -> Result<()> {
        self.validate()?;
        self.announce_debug_session();
        self.log("info", STEP_PREPARE, "开始执行多商品会话", Some(0.04));
        self.ensure_ready().await?;

        while self.has_pending_tasks() {
            self.debug.begin_round();
            self.refresh_favorites().await?;
            let scans = self.scan_visible_prices().await?;
            if scans.iter().any(|item| item.price.is_some()) {
                self.ocr_miss_streak = 0;
                self.last_ocr_ok = Instant::now();
            } else {
                self.ocr_miss_streak = self.ocr_miss_streak.saturating_add(1);
            }

            for scan in scans {
                let Some(index) = self.tasks.iter().position(|item| item.id == scan.task_id) else {
                    continue;
                };
                if target_reached(&self.tasks[index]) {
                    continue;
                }
                let task = self.tasks[index].clone();
                let Some(price) = scan.price else {
                    continue;
                };
                let limit = price_with_premium(task.price, task.premium_pct);
                if task.price <= 0 || price > limit || !price_sane(price, &task) {
                    continue;
                }
                self.log(
                    "info",
                    STEP_BUY,
                    format!("{} 命中阈值，列表价={price}，上限={limit}", task.name),
                    Some(0.7),
                );
                let bought = self.purchase_once(&task, scan.card_box).await?;
                if bought > 0 {
                    self.tasks[index].purchased += bought;
                    self.tasks[index].updated_at = now_iso();
                    self.request.repo.save_multi_task(&self.tasks[index])?;
                }
            }

            if self.ocr_miss_streak
                >= self
                    .request
                    .config
                    .multi_snipe_tuning
                    .ocr_miss_penalty_threshold
                    .max(1)
                && self.last_ocr_ok.elapsed()
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

        self.log("info", STEP_BUY, "多商品会话结束", Some(1.0));
        Ok(())
    }

    fn validate(&self) -> Result<()> {
        let enabled = self
            .tasks
            .iter()
            .filter(|item| item.enabled)
            .collect::<Vec<_>>();
        if enabled.is_empty() {
            bail!("no enabled multi tasks configured");
        }
        for task in enabled {
            let goods_image = resolve_path(&self.request.paths, &task.image_path);
            if !goods_image.exists() {
                bail!("goods image missing for {}: {}", task.name, goods_image.display());
            }
        }
        Ok(())
    }

    fn has_pending_tasks(&self) -> bool {
        self.tasks
            .iter()
            .any(|item| item.enabled && (!target_reached(item) || item.target_total <= 0))
    }

    async fn ensure_ready(&mut self) -> Result<()> {
        if self.find_scene_visible().await? {
            self.log("info", STEP_PREPARE, "已检测到首页/市场窗口", Some(0.08));
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
        self.nap(Duration::from_secs(game.launch_click_delay_sec)).await;
        self.click_box_step("click_launch_button", launch_button)?;
        let scene_visible = self
            .wait_any_scene(Duration::from_secs(game.startup_timeout_sec.max(1)))
            .await?;
        if !scene_visible {
            return Err(anyhow!("home/market indicator not found"));
        }
        Ok(())
    }

    async fn refresh_favorites(&mut self) -> Result<()> {
        match self.detect_scene().await? {
            "home" => self.navigate("btn_market").await?,
            "market" => {}
            "detail" => {
                let _ = self.close_detail().await?;
                self.nap(Duration::from_secs_f64(
                    self.request
                        .config
                        .multi_snipe_tuning
                        .post_close_detail_sec
                        .max(0.05),
                ))
                .await;
                if self.detect_scene().await? == "home" {
                    self.navigate("btn_market").await?;
                }
            }
            _ => bail!("unable to determine scene before favorites refresh"),
        }

        if let Some(recent_box) = self
            .locate_active("recent_purchases_tab", Duration::from_secs(1), None)
            .await?
        {
            self.click_box_step("click_recent_tab", recent_box)?;
            self.nap(Duration::from_secs_f64(
                self.request
                    .config
                    .multi_snipe_tuning
                    .post_nav_sec
                    .max(0.05),
            ))
            .await;
        }

        let favorites_box = self
            .locate_active("favorites_tab", Duration::from_secs(2), None)
            .await?
            .ok_or_else(|| anyhow!("favorites tab not found"))?;
        self.click_box_step("click_favorites_tab", favorites_box)?;
        self.nap(Duration::from_secs_f64(
            self.request
                .config
                .multi_snipe_tuning
                .post_nav_sec
                .max(0.05),
        ))
        .await;
        let screen = self.capture_screen()?;
        let screen_bottom = screen.y + screen.height;
        let top = (favorites_box.1 + favorites_box.3 + 12).clamp(screen.y, screen_bottom.saturating_sub(1));
        self.grid_region = Some((
            screen.x,
            top,
            screen.width.max(1),
            (screen_bottom - top).max(1),
        ));
        self.wait_favorites_content_ready().await?;
        self.log("info", STEP_FAVORITES, "已刷新到收藏页", Some(0.28));
        Ok(())
    }

    async fn wait_favorites_content_ready(&mut self) -> Result<()> {
        let candidates = self
            .tasks
            .iter()
            .filter(|item| item.enabled)
            .take(4)
            .cloned()
            .collect::<Vec<_>>();
        let deadline = Instant::now()
            + Duration::from_secs_f64(
                self.request
                    .config
                    .multi_snipe_tuning
                    .anchor_stabilize_sec
                    .max(0.8)
                    + 1.0,
            );
        while Instant::now() < deadline {
            let screen = self.capture_screen()?;
            for task in &candidates {
                let goods_path = resolve_path(&self.request.paths, &task.image_path);
                if self
                    .locate_template_in_capture(
                        &screen,
                        "wait_favorites_goods_anchor",
                        "goods",
                        &goods_path,
                        0.80,
                        self.grid_region,
                    )?
                    .is_some()
                {
                    return Ok(());
                }
            }
            self.nap(Duration::from_secs_f64(
                self.request
                    .config
                    .multi_snipe_tuning
                    .probe_step_sec
                    .max(0.04),
            ))
            .await;
        }
        Ok(())
    }

    async fn scan_visible_prices(&mut self) -> Result<Vec<PriceScanResult>> {
        let screen = Arc::new(self.capture_screen()?);
        let mut jobs = Vec::new();
        let pending_tasks = self
            .tasks
            .iter()
            .filter(|item| item.enabled && !target_reached(item))
            .cloned()
            .collect::<Vec<_>>();
        for task in &pending_tasks {
            let Some(card_box) = self.locate_or_cache_card(&screen, task)? else {
                continue;
            };
            let price_roi = bottom_roi_from_card(card_box);
            let local_price_roi = global_box_to_local(&screen, price_roi)
                .ok_or_else(|| anyhow!("price roi is outside screen"))?;
            let gray = crop_gray(&screen.image, local_price_roi)?;
            let ocr_image = threshold(resize(gray.clone(), 2.5));
            jobs.push(PriceScanJob {
                task_id: task.id.clone(),
                task_name: task.name.clone(),
                card_box,
                roi: price_roi,
                screen: screen.clone(),
                image: ocr_image,
            });
        }
        let scans = self.ocr_price_jobs(jobs).await?;
        self.log(
            "info",
            STEP_SCAN,
            format!("本轮读取 {} 个收藏卡片价格", scans.len()),
            Some(0.5),
        );
        Ok(scans)
    }

    fn locate_or_cache_card(
        &mut self,
        screen: &CapturedImage,
        task: &MultiTaskRecord,
    ) -> Result<Option<MatchBox>> {
        if let Some(card) = self.card_cache.get(&task.id).copied() {
            return Ok(Some(card));
        }
        let goods_path = resolve_path(&self.request.paths, &task.image_path);
        let matched = self.locate_template_in_capture(
            screen,
            &format!("locate_card_{}", task.id),
            "goods",
            &goods_path,
            0.80,
            self.grid_region,
        )?;
        let Some(goods_box) = matched else {
            return Ok(None);
        };
        let card_box = infer_card_from_goods_match(goods_box);
        self.card_cache.insert(task.id.clone(), card_box);
        Ok(Some(card_box))
    }

    async fn ocr_price_jobs(&mut self, jobs: Vec<PriceScanJob>) -> Result<Vec<PriceScanResult>> {
        let max_workers = self
            .request
            .config
            .multi_snipe_tuning
            .ocr_max_workers
            .max(1) as usize;
        let mut out = Vec::new();
        for chunk in jobs.chunks(max_workers) {
            let mut handles = Vec::new();
            for job in chunk.iter().cloned() {
                let config = self.request.config.umi_ocr.clone();
                handles.push(async_runtime::spawn(async move {
                    let started = Instant::now();
                    let texts = recognize_text(&config, &job.image).await?;
                    let price = texts
                        .iter()
                        .filter_map(|item| parse_price_text(&item.text))
                        .max();
                    Ok::<PriceScanResult, anyhow::Error>(PriceScanResult {
                        task_id: job.task_id,
                        task_name: job.task_name,
                        card_box: job.card_box,
                        roi: job.roi,
                        screen: job.screen,
                        price,
                        texts: texts.into_iter().map(|item| item.text).collect(),
                        elapsed: started.elapsed(),
                    })
                }));
            }
            for handle in handles {
                let scan = handle
                    .await
                    .map_err(|error| anyhow!(error.to_string()))??;
                self.record_ocr_step(
                    &format!("scan_price_{}", scan.task_name),
                    &scan.screen,
                    scan.roi,
                    &scan.texts,
                    scan.elapsed,
                )?;
                out.push(scan);
            }
        }
        Ok(out)
    }

    async fn purchase_once(&mut self, task: &MultiTaskRecord, card_box: MatchBox) -> Result<i64> {
        self.click_box_step("open_detail_from_card", card_box)?;
        self.nap(Duration::from_secs_f64(
            self.request
                .config
                .multi_snipe_tuning
                .detail_open_settle_sec
                .max(0.05),
        ))
        .await;
        if !self.detail_visible().await? {
            self.bump_failure(task);
            return Ok(0);
        }

        self.cache_detail_controls().await?;
        let Some(unit_price) = self.read_avg_price(task).await? else {
            let _ = self.close_detail().await?;
            self.bump_failure(task);
            return Ok(0);
        };
        let ceiling = price_with_premium(task.price, task.premium_pct);
        if !price_sane(unit_price, task) || unit_price > ceiling {
            let _ = self.close_detail().await?;
            self.bump_failure(task);
            return Ok(0);
        }
        self.record_price(task, unit_price)?;

        let (qty, used_max) = self.prepare_qty(task).await?;
        let buy_box = self
            .detail
            .get("btn_buy")
            .copied()
            .ok_or_else(|| anyhow!("btn_buy missing"))?;
        self.click_box_step("click_buy_button", buy_box)?;
        if !self.wait_buy_ok().await? {
            let _ = self.close_detail().await?;
            self.bump_failure(task);
            return Ok(0);
        }

        let mut bought = qty;
        self.record_purchase(task, unit_price, qty, used_max)?;
        if self.request.config.multi_snipe_tuning.fast_chain_mode {
            let max_chain = self.request.config.multi_snipe_tuning.fast_chain_max.max(1) as usize;
            let interval = Duration::from_secs_f64(
                self.request
                    .config
                    .multi_snipe_tuning
                    .fast_chain_interval_ms
                    .max(30.0)
                    / 1000.0,
            );
            for _ in 1..max_chain {
                let center = center_of_box(buy_box);
                self.click_point_step("fast_chain_click_buy", center, Some(buy_box))?;
                self.nap(interval).await;
                if !self.wait_buy_ok().await? {
                    break;
                }
                bought += qty;
                self.record_purchase(task, unit_price, qty, used_max)?;
            }
        }

        self.dismiss_overlay().await?;
        let _ = self.close_detail().await?;
        self.fail_counts.insert(task.id.clone(), 0);
        Ok(bought)
    }

    async fn prepare_qty(&mut self, task: &MultiTaskRecord) -> Result<(i64, bool)> {
        let is_ammo = task.big_category.trim() == "弹药";
        if task.purchase_mode.trim().eq_ignore_ascii_case("restock") {
            if is_ammo {
                if let Some(max_box) = self.detail.get("btn_max").copied() {
                    self.click_box_step("click_btn_max", max_box)?;
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
            return Ok((1, false));
        }

        let qty = if is_ammo { 10 } else { 1 };
        if qty > 1 {
            let _ = self.focus_type_qty(qty).await?;
        }
        Ok((qty, false))
    }

    async fn focus_type_qty(&mut self, qty: i64) -> Result<bool> {
        if self.qty_mid.is_none() {
            self.cache_qty_mid();
        }
        let Some(qty_box) = self.qty_mid else {
            return Ok(false);
        };
        self.click_box_step("focus_qty_input", qty_box)?;
        self.nap(Duration::from_millis(30)).await;
        let qty_roi = self.qty_roi();
        self.type_text_step("type_qty_value", qty_roi, &qty.to_string())?;
        self.nap(Duration::from_millis(30)).await;
        Ok(true)
    }

    async fn read_qty(&mut self) -> Result<Option<i64>> {
        let Some(roi) = self.qty_roi() else {
            return Ok(None);
        };
        let screen = self.capture_screen()?;
        let local_roi =
            global_box_to_local(&screen, roi).ok_or_else(|| anyhow!("qty roi is outside screen"))?;
        let gray = crop_gray(&screen.image, local_roi)?;
        let image = threshold(resize(gray, 2.0));
        let started = Instant::now();
        let texts = recognize_text(&self.request.config.umi_ocr, &image).await?;
        let lines = texts.iter().map(|item| item.text.clone()).collect::<Vec<_>>();
        self.record_ocr_step("read_qty", &screen, roi, &lines, started.elapsed())?;
        Ok(texts
            .into_iter()
            .filter_map(|item| parse_digits(&item.text))
            .max())
    }

    async fn read_avg_price(&mut self, task: &MultiTaskRecord) -> Result<Option<i64>> {
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
                if let Some(value) = self.try_read_avg_price(task).await? {
                    return Ok(Some(value));
                }
                self.nap(step).await;
            }
        }
        Ok(None)
    }

    async fn try_read_avg_price(&mut self, _task: &MultiTaskRecord) -> Result<Option<i64>> {
        let buy_box = self
            .detail
            .get("btn_buy")
            .copied()
            .ok_or_else(|| anyhow!("btn_buy missing"))?;
        let screen = self.capture_screen()?;
        let local_buy_box = global_box_to_local(&screen, buy_box)
            .ok_or_else(|| anyhow!("btn_buy is outside screen"))?;
        let roi = avg_price_roi(
            local_buy_box,
            false,
            &self.request.config,
            screen.width,
            screen.height,
        );
        let global_roi = local_box_to_global(&screen, roi);
        let gray = crop_gray(&screen.image, roi)?;
        let image = threshold(resize(
            top_half(gray),
            self.request.config.avg_price_area.scale,
        ));
        let started = Instant::now();
        let texts = recognize_text(&self.request.config.umi_ocr, &image).await?;
        let lines = texts.iter().map(|item| item.text.clone()).collect::<Vec<_>>();
        self.record_ocr_step("avg_price", &screen, global_roi, &lines, started.elapsed())?;
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
        Ok(!saw_fail && self.detail_visible().await?)
    }

    async fn dismiss_overlay(&mut self) -> Result<()> {
        if let Some(ok_box) = self
            .locate_active("buy_ok", Duration::from_millis(120), None)
            .await?
        {
            self.click_box_step("dismiss_overlay_by_ok", ok_box)?;
        } else if let Some(buy_box) = self.detail.get("btn_buy").copied() {
            self.click_box_step("dismiss_overlay_by_buy", buy_box)?;
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
        if let Some(close_box) = self.detail.get("btn_close").copied() {
            self.click_box_step("click_close_detail", close_box)?;
            self.nap(Duration::from_secs_f64(
                self.request
                    .config
                    .multi_snipe_tuning
                    .post_close_detail_sec
                    .max(0.05),
            ))
            .await;
            self.detail.clear();
            self.qty_mid = None;
            return Ok(true);
        }
        Ok(false)
    }

    async fn detail_visible(&mut self) -> Result<bool> {
        Ok(self
            .locate_active("btn_buy", Duration::from_millis(60), None)
            .await?
            .is_some()
            && self
                .locate_active("btn_close", Duration::from_millis(60), None)
                .await?
                .is_some())
    }

    async fn cache_detail_controls(&mut self) -> Result<()> {
        let screen = self.capture_screen()?;
        for key in ["btn_buy", "btn_close", "qty_minus", "qty_plus", "btn_max"] {
            let template = self.template_probe_entry(key)?;
            if let Some(box_rect) = self.locate_template_in_capture(
                &screen,
                &format!("cache_detail_{key}"),
                key,
                &template.path,
                template.confidence,
                None,
            )? {
                self.detail.insert(key.to_string(), box_rect);
            }
        }
        self.cache_qty_mid();
        Ok(())
    }

    fn cache_qty_mid(&mut self) {
        if let (Some(minus), Some(plus)) = (
            self.detail.get("qty_minus").copied(),
            self.detail.get("qty_plus").copied(),
        ) {
            let mx = minus.0 + minus.2 / 2;
            let my = minus.1 + minus.3 / 2;
            let px = plus.0 + plus.2 / 2;
            let py = plus.1 + plus.3 / 2;
            self.qty_mid = Some((((mx + px) / 2) - 2, ((my + py) / 2) - 2, 4, 4));
        } else if let Some(max_box) = self.detail.get("btn_max").copied() {
            self.qty_mid = Some(infer_qty_from_max(max_box));
        }
    }

    fn qty_roi(&self) -> Option<MatchBox> {
        if let (Some(minus), Some(plus)) = (
            self.detail.get("qty_minus").copied(),
            self.detail.get("qty_plus").copied(),
        ) {
            let left = minus.0 + minus.2 + 2;
            let right = plus.0 - 2;
            if right > left {
                let cy = (minus.1 + minus.3 / 2 + plus.1 + plus.3 / 2) / 2;
                return Some((left, cy - 18, right - left, 36));
            }
        }
        self.qty_mid.map(|mid| {
            let cx = mid.0 + mid.2 / 2;
            let cy = mid.1 + mid.3 / 2;
            (cx - 40, cy - 18, 80, 36)
        })
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
        Ok(())
    }

    async fn detect_scene(&mut self) -> Result<&'static str> {
        let screen = self.capture_screen()?;
        self.detect_scene_in_capture(&screen)
    }

    async fn wait_any_scene(&mut self, timeout: Duration) -> Result<bool> {
        let deadline = Instant::now() + timeout;
        loop {
            let screen = self.capture_screen()?;
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

    fn capture_screen(&self) -> Result<CapturedImage> {
        capture_full_screen()
    }

    fn record_price(&self, task: &MultiTaskRecord, price: i64) -> Result<()> {
        let observed_at = now_iso();
        self.request.repo.insert_price_history(&PriceHistoryRecord {
            id: format!("price-{}", Uuid::new_v4()),
            item_id: task.item_id.clone(),
            item_name: task.name.clone(),
            category: optional_str(&task.big_category),
            price,
            observed_at_epoch: iso_to_epoch(&observed_at),
            observed_at,
        })
    }

    fn record_purchase(
        &self,
        task: &MultiTaskRecord,
        price: i64,
        qty: i64,
        used_max: bool,
    ) -> Result<()> {
        self.request
            .repo
            .insert_purchase_history(&PurchaseHistoryRecord {
                id: format!("buy-{}", Uuid::new_v4()),
                item_id: task.item_id.clone(),
                item_name: task.name.clone(),
                category: optional_str(&task.big_category),
                price,
                qty,
                amount: price * qty,
                task_id: Some(task.id.clone()),
                task_name: Some(task.name.clone()),
                used_max: Some(used_max),
                purchased_at: now_iso(),
            })
    }

    fn bump_failure(&mut self, task: &MultiTaskRecord) {
        let count = self.fail_counts.entry(task.id.clone()).or_insert(0);
        *count += 1;
        if *count >= self.request.config.multi_snipe_tuning.relocate_after_fail.max(1) {
            self.card_cache.remove(&task.id);
            *count = 0;
        }
    }

    fn log(&self, level: &str, step: &str, message: impl Into<String>, progress: Option<f64>) {
        if let Ok(mut emit) = self.emitter.lock() {
            (emit)(AutomationEvent::log(
                self.session_id.clone(),
                "multi".to_string(),
                level.to_string(),
                message.into(),
                Some(step.to_string()),
                progress,
            ));
        }
    }

    async fn nap(&self, duration: Duration) {
        sleep(duration).await;
    }
}

impl SessionDebugSupport for MultiSession {
    fn debug_recorder(&mut self) -> &mut DebugRecorder {
        &mut self.debug
    }

    fn debug_recorder_ref(&self) -> &DebugRecorder {
        &self.debug
    }

    fn debug_mode_label(&self) -> &'static str {
        "收藏商品"
    }

    fn emit_debug_log(&self, level: &str, message: String) {
        self.log(level, STEP_CAPTURE, message, None);
    }

    fn capture_screen_for_debug(&mut self, _stage: &str) -> Result<CapturedImage> {
        self.capture_screen()
    }

    fn template_probe_entry(&self, slug: &str) -> Result<TemplateProbeEntry> {
        self.templates
            .get(slug)
            .cloned()
            .ok_or_else(|| anyhow!("template missing: {slug}"))
    }
}

fn price_sane(unit_price: i64, task: &MultiTaskRecord) -> bool {
    let base = task.price;
    base <= 0 || unit_price * 2 > base
}

fn target_reached(task: &MultiTaskRecord) -> bool {
    task.target_total > 0 && task.purchased >= task.target_total
}

#[cfg(test)]
mod tests {
    use super::{optional_str, price_sane, split_args, target_reached};
    use crate::app::types::MultiTaskRecord;

    fn sample_task() -> MultiTaskRecord {
        MultiTaskRecord {
            id: "task-1".to_string(),
            item_id: "item-1".to_string(),
            name: "测试物品".to_string(),
            enabled: true,
            price: 10_000,
            premium_pct: 5.0,
            purchase_mode: "normal".to_string(),
            target_total: 20,
            purchased: 3,
            order_index: 0,
            image_path: "images/goods/_default.png".to_string(),
            big_category: "杂物".to_string(),
            created_at: "2026-03-17T00:00:00Z".to_string(),
            updated_at: "2026-03-17T00:00:00Z".to_string(),
        }
    }

    #[test]
    fn splits_launch_args_by_whitespace() {
        assert_eq!(
            split_args("--foo bar   baz"),
            vec!["--foo".to_string(), "bar".to_string(), "baz".to_string()]
        );
    }

    #[test]
    fn price_sanity_rejects_too_low_numbers() {
        let task = sample_task();
        assert!(!price_sane(4_000, &task));
        assert!(price_sane(6_000, &task));
    }

    #[test]
    fn target_reached_requires_threshold_and_count() {
        let mut task = sample_task();
        assert!(!target_reached(&task));
        task.purchased = 20;
        assert!(target_reached(&task));
        task.target_total = 0;
        assert!(!target_reached(&task));
    }

    #[test]
    fn optional_str_trims_empty_values() {
        assert_eq!(optional_str("  "), None);
        assert_eq!(optional_str(" 杂物 "), Some("杂物".to_string()));
    }
}
