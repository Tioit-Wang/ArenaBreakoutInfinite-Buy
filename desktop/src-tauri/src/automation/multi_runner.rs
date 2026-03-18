use std::collections::HashMap;
use std::path::{Path, PathBuf};
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
use crate::automation::input::{click_point, type_text};
use crate::automation::ocr::recognize_text;
use crate::automation::vision::{MatchBox, locate_template_in_image};
use crate::config::paths::AppPaths;
use crate::storage::repository::Repository;

const STEP_PREPARE: &str = "步骤1-准备多商品上下文";
const STEP_FAVORITES: &str = "步骤2-刷新并进入收藏页";
const STEP_SCAN: &str = "步骤3-批量读价与筛选";
const STEP_BUY: &str = "步骤4-进入详情并购买";

type SharedEmitter = Arc<Mutex<Box<dyn FnMut(AutomationEvent) + Send>>>;

#[derive(Clone)]
pub struct MultiRunRequest {
    pub tasks: Vec<MultiTaskRecord>,
    pub config: AppConfig,
    pub templates: Vec<TemplateConfig>,
    pub paths: Arc<AppPaths>,
    pub repo: Arc<Repository>,
    pub pause_flag: Arc<AtomicBool>,
}

#[derive(Debug, Clone)]
struct TemplateEntry {
    path: PathBuf,
    confidence: f64,
}

#[derive(Clone)]
struct PriceScanJob {
    task_id: String,
    card_box: MatchBox,
    image: GrayImage,
}

#[derive(Clone)]
struct PriceScanResult {
    task_id: String,
    card_box: MatchBox,
    price: Option<i64>,
}

struct MultiSession {
    request: MultiRunRequest,
    tasks: Vec<MultiTaskRecord>,
    emitter: SharedEmitter,
    session_id: String,
    templates: HashMap<String, TemplateEntry>,
    detail: HashMap<String, MatchBox>,
    qty_mid: Option<MatchBox>,
    grid_region: Option<MatchBox>,
    card_cache: HashMap<String, MatchBox>,
    fail_counts: HashMap<String, u32>,
    ocr_miss_streak: u32,
    last_ocr_ok: Instant,
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

impl MultiSession {
    fn new(request: MultiRunRequest, emitter: SharedEmitter, session_id: String) -> Self {
        let templates = request
            .templates
            .iter()
            .map(|item| {
                (
                    item.slug.clone(),
                    TemplateEntry {
                        path: resolve_path(&request.paths, &item.path),
                        confidence: item.confidence.max(0.1),
                    },
                )
            })
            .collect();
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
        }
    }

    async fn run(&mut self) -> Result<()> {
        self.validate()?;
        self.log("info", STEP_PREPARE, "开始执行多商品会话", Some(0.04));
        self.ensure_ready().await?;

        while self.has_pending_tasks() {
            self.wait_paused().await;
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
        click_box_global(launch_button)?;
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
            click_box_global(recent_box)?;
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
        click_box_global(favorites_box)?;
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
                    .locate_template_in_capture(&screen, &goods_path, 0.80, self.grid_region)?
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
        let screen = self.capture_screen()?;
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
            jobs.push(PriceScanJob {
                task_id: task.id.clone(),
                card_box,
                image: gray,
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
        let matched = self.locate_template_in_capture(screen, &goods_path, 0.80, self.grid_region)?;
        let Some(goods_box) = matched else {
            return Ok(None);
        };
        let card_box = infer_card_from_goods_match(goods_box);
        self.card_cache.insert(task.id.clone(), card_box);
        Ok(Some(card_box))
    }

    async fn ocr_price_jobs(&self, jobs: Vec<PriceScanJob>) -> Result<Vec<PriceScanResult>> {
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
                    let image = threshold(resize(job.image, 2.5));
                    let texts = recognize_text(&config, &image).await?;
                    let price = texts
                        .iter()
                        .filter_map(|item| parse_price_text(&item.text))
                        .max();
                    Ok::<PriceScanResult, anyhow::Error>(PriceScanResult {
                        task_id: job.task_id,
                        card_box: job.card_box,
                        price,
                    })
                }));
            }
            for handle in handles {
                let scan = handle
                    .await
                    .map_err(|error| anyhow!(error.to_string()))??;
                out.push(scan);
            }
        }
        Ok(out)
    }

    async fn purchase_once(&mut self, task: &MultiTaskRecord, card_box: MatchBox) -> Result<i64> {
        self.capture_screen()?;
        click_box_global(card_box)?;
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
        click_box_global(buy_box)?;
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
                click_point(center.0, center.1)?;
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
                    click_box_global(max_box)?;
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
        click_box_global(qty_box)?;
        self.nap(Duration::from_millis(30)).await;
        type_text(&qty.to_string())?;
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
        let texts = recognize_text(&self.request.config.umi_ocr, &image).await?;
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
        let gray = crop_gray(&screen.image, roi)?;
        let image = threshold(resize(
            top_half(gray),
            self.request.config.avg_price_area.scale,
        ));
        let texts = recognize_text(&self.request.config.umi_ocr, &image).await?;
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
            click_box_global(ok_box)?;
        } else if let Some(buy_box) = self.detail.get("btn_buy").copied() {
            click_box_global(buy_box)?;
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
            click_box_global(close_box)?;
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
            if let Some(box_rect) = self.locate_template_in_capture(
                &screen,
                &self.template(key)?.path,
                self.template(key)?.confidence,
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
            click_box_global(box_rect)?;
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
        click_box_global(box_rect)?;
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

    async fn wait_any_template(
        &mut self,
        slug: &str,
        timeout: Duration,
    ) -> Result<Option<MatchBox>> {
        let deadline = Instant::now() + timeout;
        loop {
            let screen = self.capture_screen()?;
            if let Some(box_rect) = self.locate_template_in_capture(
                &screen,
                &self.template(slug)?.path,
                self.template(slug)?.confidence,
                None,
            )? {
                return Ok(Some(box_rect));
            }
            if timeout.is_zero() || Instant::now() >= deadline {
                return Ok(None);
            }
            self.nap(Duration::from_millis(120)).await;
        }
    }

    async fn locate_active(
        &mut self,
        slug: &str,
        timeout: Duration,
        region: Option<MatchBox>,
    ) -> Result<Option<MatchBox>> {
        let deadline = Instant::now() + timeout;
        loop {
            let screen = self.capture_screen()?;
            if let Some(box_rect) = self.locate_template_in_capture(
                &screen,
                &self.template(slug)?.path,
                self.template(slug)?.confidence,
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

    fn capture_screen(&self) -> Result<CapturedImage> {
        capture_full_screen()
    }

    fn detect_scene_in_capture(&self, screen: &CapturedImage) -> Result<&'static str> {
        if self.locate_template_in_capture(
            screen,
            &self.template("home_indicator")?.path,
            self.template("home_indicator")?.confidence,
            None,
        )?.is_some() {
            return Ok("home");
        }
        if self.locate_template_in_capture(
            screen,
            &self.template("market_indicator")?.path,
            self.template("market_indicator")?.confidence,
            None,
        )?.is_some() {
            return Ok("market");
        }
        if self.locate_template_in_capture(
            screen,
            &self.template("btn_buy")?.path,
            self.template("btn_buy")?.confidence,
            None,
        )?.is_some()
            && self.locate_template_in_capture(
                screen,
                &self.template("btn_close")?.path,
                self.template("btn_close")?.confidence,
                None,
            )?.is_some()
        {
            return Ok("detail");
        }
        Ok("unknown")
    }

    fn locate_template_in_capture(
        &self,
        screen: &CapturedImage,
        template_path: &Path,
        confidence: f64,
        region: Option<MatchBox>,
    ) -> Result<Option<MatchBox>> {
        let local_region = region.and_then(|rect| global_box_to_local(screen, rect));
        Ok(locate_template_in_image(&screen.image, template_path, confidence, local_region)?
            .map(|rect| local_box_to_global(screen, rect)))
    }

    fn template(&self, slug: &str) -> Result<&TemplateEntry> {
        self.templates
            .get(slug)
            .ok_or_else(|| anyhow!("template missing: {slug}"))
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

    async fn wait_paused(&self) {
        while self.request.pause_flag.load(Ordering::SeqCst) {
            sleep(Duration::from_millis(200)).await;
        }
    }

    async fn nap(&self, duration: Duration) {
        let deadline = Instant::now() + duration;
        loop {
            self.wait_paused().await;
            let now = Instant::now();
            if now >= deadline {
                return;
            }
            sleep((deadline - now).min(Duration::from_millis(40))).await;
        }
    }
}

fn resolve_path(paths: &AppPaths, raw: &str) -> PathBuf {
    let path = PathBuf::from(raw);
    if path.is_absolute() {
        path
    } else {
        paths.resolve_data_path(raw)
    }
}

fn split_args(raw: &str) -> Vec<String> {
    raw.split_whitespace().map(str::to_string).collect()
}

fn center_of_box(rect: MatchBox) -> (i32, i32) {
    (rect.0 + rect.2 / 2, rect.1 + rect.3 / 2)
}

fn click_box_global(rect: MatchBox) -> Result<()> {
    let center = center_of_box(rect);
    click_point(center.0, center.1)
}

fn global_box_to_local(screen: &CapturedImage, rect: MatchBox) -> Option<MatchBox> {
    let max_width = screen.image.width() as i32;
    let max_height = screen.image.height() as i32;
    let left = (rect.0 - screen.x).clamp(0, max_width.saturating_sub(1));
    let top = (rect.1 - screen.y).clamp(0, max_height.saturating_sub(1));
    let right = (rect.0 + rect.2 - screen.x).clamp(left + 1, max_width);
    let bottom = (rect.1 + rect.3 - screen.y).clamp(top + 1, max_height);
    if right <= left || bottom <= top {
        return None;
    }
    Some((left, top, right - left, bottom - top))
}

fn local_box_to_global(screen: &CapturedImage, rect: MatchBox) -> MatchBox {
    (rect.0 + screen.x, rect.1 + screen.y, rect.2, rect.3)
}

fn price_sane(unit_price: i64, task: &MultiTaskRecord) -> bool {
    let base = task.price;
    base <= 0 || unit_price * 2 > base
}

fn target_reached(task: &MultiTaskRecord) -> bool {
    task.target_total > 0 && task.purchased >= task.target_total
}

fn optional_str(value: &str) -> Option<String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
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
