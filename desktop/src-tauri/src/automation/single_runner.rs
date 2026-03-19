use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use anyhow::{Context, Result, anyhow, bail};
use chrono::Utc;
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
use crate::automation::input::{click_point, type_text};
use crate::automation::ocr::recognize_text;
use crate::automation::vision::{MatchBox, locate_template_in_image};
use crate::automation::capture::capture_full_screen;
use crate::config::paths::AppPaths;
use crate::storage::repository::Repository;

const STEP_1: &str = "步骤1-全局启动与准备";
const STEP_3: &str = "步骤3-障碍清理与初始化检查";
const STEP_4: &str = "步骤4-搜索与列表定位";
const STEP_6: &str = "步骤6-价格读取与阈值判定";
const STEP_8: &str = "步骤8-会话内循环与退出条件";
const STEP_CAPTURE: &str = "抓图存档";

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
}

#[derive(Debug, Clone)]
struct TemplateEntry {
    path: PathBuf,
    confidence: f64,
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
    templates: HashMap<String, TemplateEntry>,
    goods_target: Option<CachedClickTarget>,
    detail: DetailCache,
    detail_open_hint: bool,
    ocr_miss_streak: u32,
    last_avg_ok: Instant,
    completed_rounds: u32,
    last_restock_trigger_at: Option<Instant>,
    capture_seq: u64,
    capture_dir_announced: bool,
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

impl SingleSession {
    fn new(request: SingleRunRequest, emitter: SharedEmitter, session_id: String) -> Self {
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
            capture_seq: 0,
            capture_dir_announced: false,
        }
    }

    async fn run(&mut self) -> Result<()> {
        self.validate()?;
        self.announce_capture_archive();
        self.log("info", STEP_1, "开始执行单商品会话", Some(0.05));
        self.ensure_ready().await?;
        self.clear_obstacles().await?;
        self.build_search_context().await?;

        let mut task = self.request.task.clone();
        while !target_reached(&task) {
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
        click_box_global(launch_button)?;
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
                click_point(target.points.center.0, target.points.center.1)?;
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
                click_box_global(box_rect)?;
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
        click_box_global(input_box)?;
        self.nap(Duration::from_millis(30)).await;
        type_text(&self.request.goods.search_name)?;
        self.nap(Duration::from_millis(30)).await;
        let search_box = self
            .locate_active("btn_search", Duration::from_secs(1), None)
            .await?
            .ok_or_else(|| anyhow!("search button not found"))?;
        click_box_global(search_box)?;
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
            click_point(buy_target.points.center.0, buy_target.points.center.1)?;
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

        click_point(buy_target.points.center.0, buy_target.points.center.1)?;
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
            click_point(buy_target.points.center.0, buy_target.points.center.1)?;
            self.nap(interval).await;
            click_point(buy_target.points.center.0, buy_target.points.center.1)?;
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
                click_point(max_target.points.center.0, max_target.points.center.1)?;
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
        click_point(qty_center.0, qty_center.1)?;
        self.nap(Duration::from_millis(30)).await;
        type_text(&qty.to_string())?;
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
        self.archive_gray_capture("read_qty_raw", roi, &gray);
        let image = threshold(resize(gray, 2.0));
        self.archive_gray_capture("read_qty_ocr", roi, &image);
        let texts = recognize_text(&self.request.config.umi_ocr, &image).await?;
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
        self.archive_gray_capture("avg_price_raw", roi, &gray);
        let image = threshold(resize(
            top_half(gray),
            self.request.config.avg_price_area.scale,
        ));
        self.archive_gray_capture("avg_price_ocr", roi, &image);
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
        Ok(!saw_fail && self.detail_visible())
    }

    async fn dismiss_overlay(&mut self) -> Result<()> {
        if let Some(buy_target) = self.detail.controls.get("btn_buy").copied() {
            self.capture_screen("dismiss_overlay_buy")?;
            click_point(buy_target.points.center.0, buy_target.points.center.1)?;
        } else if let Some(ok_box) = self
            .locate_active("buy_ok", Duration::from_millis(120), None)
            .await?
        {
            click_box_global(ok_box)?;
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
            click_point(close_target.points.center.0, close_target.points.center.1)?;
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
            click_point(goods_target.points.center.0, goods_target.points.center.1)?;
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
            click_box_global(goods_box)?;
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

    async fn wait_any_template(
        &mut self,
        slug: &str,
        timeout: Duration,
    ) -> Result<Option<MatchBox>> {
        let deadline = Instant::now() + timeout;
        loop {
            let screen = self.capture_screen(&format!("wait_any_template_{slug}"))?;
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
            let screen = self.capture_screen(&format!("locate_active_{slug}"))?;
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

    async fn locate_goods(
        &mut self,
        timeout: Duration,
        region: Option<MatchBox>,
    ) -> Result<Option<MatchBox>> {
        let deadline = Instant::now() + timeout;
        let goods_path = resolve_path(&self.request.paths, &self.request.goods.image_path);
        loop {
            let screen = self.capture_screen("locate_goods")?;
            if let Some(box_rect) = self.locate_template_in_capture(&screen, &goods_path, 0.80, region)? {
                return Ok(Some(box_rect));
            }
            if timeout.is_zero() || Instant::now() >= deadline {
                return Ok(None);
            }
            self.nap(Duration::from_millis(60)).await;
        }
    }

    fn capture_screen(&mut self, stage: &str) -> Result<CapturedImage> {
        let screen = capture_full_screen()?;
        self.archive_screen_capture(stage, &screen);
        Ok(screen)
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

    fn announce_capture_archive(&mut self) {
        if !self.request.config.debug.save_single_capture_images || self.capture_dir_announced {
            return;
        }
        self.capture_dir_announced = true;
        self.log(
            "info",
            STEP_CAPTURE,
            format!(
                "已启用单商品抓图存档，目录={}",
                self.capture_archive_dir().display()
            ),
            None,
        );
    }

    fn capture_archive_dir(&self) -> PathBuf {
        self.request
            .paths
            .debug_dir
            .join("single-captures")
            .join(&self.session_id)
    }

    fn archive_screen_capture(&mut self, stage: &str, screen: &CapturedImage) {
        if !self.request.config.debug.save_single_capture_images {
            return;
        }
        self.persist_capture(
            stage,
            Some((screen.x, screen.y, screen.width, screen.height)),
            None,
            |path| screen.image.save(path),
        );
    }

    fn archive_gray_capture(&mut self, stage: &str, roi: MatchBox, image: &GrayImage) {
        if !self.request.config.debug.save_single_capture_images {
            return;
        }
        self.persist_capture(stage, None, Some(roi), |path| image.save(path));
    }

    fn persist_capture<F>(
        &mut self,
        stage: &str,
        screen: Option<(i32, i32, i32, i32)>,
        roi: Option<MatchBox>,
        save: F,
    ) where
        F: FnOnce(&Path) -> image::ImageResult<()>,
    {
        self.announce_capture_archive();
        let path = self.next_capture_path(stage, screen, roi);
        if let Some(parent) = path.parent()
            && let Err(error) = fs::create_dir_all(parent)
        {
            self.log(
                "error",
                STEP_CAPTURE,
                format!("创建抓图目录失败：{} ({error})", parent.display()),
                None,
            );
            return;
        }
        if let Err(error) = save(&path) {
            self.log(
                "error",
                STEP_CAPTURE,
                format!("保存抓图失败：{} ({error})", path.display()),
                None,
            );
        }
    }

    fn next_capture_path(
        &mut self,
        stage: &str,
        screen: Option<(i32, i32, i32, i32)>,
        roi: Option<MatchBox>,
    ) -> PathBuf {
        self.capture_seq = self.capture_seq.saturating_add(1);
        let timestamp = Utc::now().format("%Y%m%dT%H%M%S%.3fZ").to_string();
        let mut parts = vec![
            format!("{:06}", self.capture_seq),
            timestamp,
            sanitize_capture_component(stage),
        ];
        if let Some((x, y, width, height)) = screen {
            parts.push(format!("screen_{x}_{y}_{width}_{height}"));
            parts.push(format!("{}x{}", width.max(0), height.max(0)));
        }
        if let Some((x, y, width, height)) = roi {
            parts.push(format!("roi_{x}_{y}_{width}_{height}"));
        }
        self.capture_archive_dir()
            .join(format!("{}.png", parts.join("__")))
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

fn optional_str(value: &str) -> Option<String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
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

fn sanitize_capture_component(raw: &str) -> String {
    let sanitized = raw
        .trim()
        .chars()
        .map(|ch| match ch {
            '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*' => '_',
            c if c.is_whitespace() => '_',
            c if c.is_control() => '_',
            c => c,
        })
        .collect::<String>();
    let collapsed = sanitized
        .split('_')
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join("_");
    if collapsed.is_empty() {
        "capture".to_string()
    } else {
        collapsed.chars().take(72).collect()
    }
}
