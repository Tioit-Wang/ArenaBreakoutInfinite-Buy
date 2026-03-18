use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
};
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
const STEP_5: &str = "步骤5-预缓存（预热）";
const STEP_6: &str = "步骤6-价格读取与阈值判定";
const STEP_8: &str = "步骤8-会话内循环与退出条件";
const STEP_CAPTURE: &str = "抓图存档";

type SharedEmitter = Arc<Mutex<Box<dyn FnMut(AutomationEvent) + Send>>>;

#[derive(Clone)]
pub struct SingleRunRequest {
    pub task: SingleTaskRecord,
    pub goods: GoodsRecord,
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

struct SingleSession {
    request: SingleRunRequest,
    emitter: SharedEmitter,
    session_id: String,
    templates: HashMap<String, TemplateEntry>,
    goods_box: Option<MatchBox>,
    detail: HashMap<String, MatchBox>,
    qty_mid: Option<MatchBox>,
    avg_ocr_streak: u32,
    ocr_miss_streak: u32,
    last_avg_ok: Instant,
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
            goods_box: None,
            detail: HashMap::new(),
            qty_mid: None,
            avg_ocr_streak: 0,
            ocr_miss_streak: 0,
            last_avg_ok: Instant::now(),
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
        self.precache().await?;

        let mut task = self.request.task.clone();
        while !target_reached(&task) {
            self.wait_paused().await;
            let bought = self.purchase_once(&mut task).await?;
            if bought > 0 {
                task.purchased += bought;
                task.updated_at = now_iso();
                self.request.repo.save_single_task(&task)?;
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
            }
        }
        self.log("debug", STEP_3, "障碍清理完成", None);
        Ok(())
    }

    async fn build_search_context(&mut self) -> Result<()> {
        match self.detect_scene().await? {
            "home" => self.navigate("btn_market").await?,
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
        self.goods_box = Some(goods_box);
        self.log("info", STEP_4, "已建立搜索上下文并缓存商品卡片", Some(0.32));
        Ok(())
    }

    async fn precache(&mut self) -> Result<()> {
        for idx in 0..3 {
            if self.open_detail().await? {
                self.cache_detail_controls().await?;
                let _ = self.read_avg_price().await?;
                let _ = self.close_detail().await?;
                self.log("info", STEP_5, "预缓存完成", Some(0.4));
                return Ok(());
            }
            self.nap(Duration::from_secs(1 << idx)).await;
        }
        bail!("failed to precache detail")
    }

    async fn purchase_once(&mut self, task: &mut SingleTaskRecord) -> Result<i64> {
        if !self.open_detail().await? {
            self.log("info", STEP_5, "打开详情失败，等待下一轮", Some(0.46));
            return Ok(0);
        }
        self.cache_detail_controls().await?;
        let Some(unit_price) = self.read_avg_price().await? else {
            self.avg_ocr_streak += 1;
            self.ocr_miss_streak += 1;
            self.clear_obstacles().await?;
            return Ok(0);
        };
        self.last_avg_ok = Instant::now();
        self.ocr_miss_streak = 0;
        let normal_limit = price_with_premium(task.price_threshold, task.price_premium_pct);
        let restock_limit = price_with_premium(task.restock_price, task.restock_premium_pct);
        if !price_sane(unit_price, task) {
            let _ = self.close_detail().await?;
            return Ok(0);
        }
        self.record_price(unit_price)?;

        let (qty, used_max, fast_mode) = if task.restock_price > 0 && unit_price <= restock_limit {
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
            return Ok(0);
        };

        let buy_box = self
            .detail
            .get("btn_buy")
            .copied()
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
            click_box_global(buy_box)?;
            if !self.wait_buy_ok().await? {
                let _ = self.close_detail().await?;
                return Ok(0);
            }
            self.record_purchase(task, unit_price, qty, used_max)?;
            self.dismiss_overlay().await?;
            return Ok(qty);
        }

        click_box_global(buy_box)?;
        let mut bought = 0;
        for idx in 0..max_chain {
            if !self.wait_buy_ok().await? {
                let _ = self.close_detail().await?;
                return Ok(bought);
            }
            bought += qty;
            self.record_purchase(task, unit_price, qty, used_max)?;
            if task.target_total > 0 && task.purchased + bought >= task.target_total {
                self.dismiss_overlay().await?;
                let _ = self.close_detail().await?;
                return Ok(bought);
            }
            if idx + 1 >= max_chain {
                self.dismiss_overlay().await?;
                return Ok(bought);
            }
            let center = center_of_box(buy_box);
            click_point(center.0, center.1)?;
            self.nap(interval).await;
            click_point(center.0, center.1)?;
            self.nap(interval).await;
        }
        Ok(bought)
    }

    async fn prepare_restock_qty(&mut self) -> Result<(i64, bool)> {
        if self.request.goods.big_category.trim() == "弹药" {
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
        Ok((1, false))
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
        let buy_box = self
            .detail
            .get("btn_buy")
            .copied()
            .ok_or_else(|| anyhow!("btn_buy missing"))?;
        let captured = self.capture_screen("avg_price_window")?;
        let local_buy_box = global_box_to_local(&captured, buy_box)
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
        Ok(!saw_fail && self.detail_visible().await?)
    }

    async fn dismiss_overlay(&mut self) -> Result<()> {
        if let Some(buy_box) = self.detail.get("btn_buy").copied() {
            self.capture_screen("dismiss_overlay_buy")?;
            click_box_global(buy_box)?;
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
        if let Some(close_box) = self.detail.get("btn_close").copied() {
            self.capture_screen("close_detail")?;
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

    async fn open_detail(&mut self) -> Result<bool> {
        if self.detail_visible().await? {
            return Ok(true);
        }
        if let Some(goods_box) = self.goods_box {
            self.capture_screen("open_detail_cached_goods")?;
            click_box_global(goods_box)?;
            self.nap(Duration::from_secs_f64(
                self.request
                    .config
                    .multi_snipe_tuning
                    .detail_open_settle_sec
                    .max(0.05),
            ))
            .await;
            if self.detail_visible().await? {
                return Ok(true);
            }
        }
        if let Some(goods_box) = self.locate_goods(Duration::from_secs(2), None).await? {
            self.goods_box = Some(goods_box);
            click_box_global(goods_box)?;
            self.nap(Duration::from_secs_f64(
                self.request
                    .config
                    .multi_snipe_tuning
                    .detail_open_settle_sec
                    .max(0.05),
            ))
            .await;
            if self.detail_visible().await? {
                return Ok(true);
            }
        }
        Ok(false)
    }

    async fn cache_detail_controls(&mut self) -> Result<()> {
        let screen = self.capture_screen("cache_detail_controls")?;
        for key in ["btn_buy", "btn_close", "qty_minus", "qty_plus", "btn_max"] {
            if key == "btn_max" && self.request.goods.big_category.trim() != "弹药" {
                continue;
            }
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
