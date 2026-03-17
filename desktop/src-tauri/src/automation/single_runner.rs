use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
};
use std::time::{Duration, Instant};

use anyhow::{Context, Result, anyhow, bail};
use image::{GrayImage, RgbaImage, imageops::FilterType};
use tokio::time::sleep;
use uuid::Uuid;
use xcap::Window;

use crate::app::types::{
    AppConfig, AutomationEvent, GoodsRecord, PriceHistoryRecord, PurchaseHistoryRecord,
    SingleTaskRecord, TemplateConfig, now_iso,
};
use crate::automation::common::{parse_price_text, price_with_premium};
use crate::automation::input::{click_point, type_text};
use crate::automation::ocr::recognize_text;
use crate::automation::vision::{MatchBox, locate_template_in_image};
use crate::config::paths::AppPaths;
use crate::storage::repository::Repository;

const STEP_1: &str = "步骤1-全局启动与准备";
const STEP_3: &str = "步骤3-障碍清理与初始化检查";
const STEP_4: &str = "步骤4-搜索与列表定位";
const STEP_5: &str = "步骤5-预缓存（预热）";
const STEP_6: &str = "步骤6-价格读取与阈值判定";
const STEP_8: &str = "步骤8-会话内循环与退出条件";

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

#[derive(Debug, Clone)]
struct WindowHint {
    title: String,
    app_name: String,
}

#[derive(Debug, Clone)]
struct CapturedWindow {
    hint: WindowHint,
    x: i32,
    y: i32,
    width: i32,
    height: i32,
    image: RgbaImage,
}

struct SingleSession {
    request: SingleRunRequest,
    emitter: SharedEmitter,
    session_id: String,
    templates: HashMap<String, TemplateEntry>,
    active_window: Option<WindowHint>,
    goods_box: Option<MatchBox>,
    detail: HashMap<String, MatchBox>,
    qty_mid: Option<MatchBox>,
    avg_ocr_streak: u32,
    ocr_miss_streak: u32,
    last_avg_ok: Instant,
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
            active_window: None,
            goods_box: None,
            detail: HashMap::new(),
            qty_mid: None,
            avg_ocr_streak: 0,
            ocr_miss_streak: 0,
            last_avg_ok: Instant::now(),
        }
    }

    async fn run(&mut self) -> Result<()> {
        self.validate()?;
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
        if self.find_scene_window().await?.is_some() {
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
        click_box(&launch_button.0, launch_button.1)?;
        self.log(
            "info",
            STEP_1,
            "已点击启动按钮，等待首页/市场标识",
            Some(0.1),
        );
        let scene = self
            .wait_any_scene(Duration::from_secs(game.startup_timeout_sec.max(1)))
            .await?
            .ok_or_else(|| anyhow!("home/market indicator not found"))?;
        self.active_window = Some(scene.hint);
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
        if let Some((window, box_rect)) = self
            .locate_active("btn_close", Duration::from_millis(80), None)
            .await?
        {
            if self
                .locate_active("btn_buy", Duration::from_millis(50), None)
                .await?
                .is_some()
            {
                click_box(&window, box_rect)?;
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
        let (window, input_box) = self
            .locate_active("input_search", Duration::from_secs(2), None)
            .await?
            .ok_or_else(|| anyhow!("search input not found"))?;
        click_box(&window, input_box)?;
        self.nap(Duration::from_millis(30)).await;
        type_text(&self.request.goods.search_name)?;
        self.nap(Duration::from_millis(30)).await;
        let (window, search_box) = self
            .locate_active("btn_search", Duration::from_secs(1), None)
            .await?
            .ok_or_else(|| anyhow!("search button not found"))?;
        click_box(&window, search_box)?;
        self.nap(Duration::from_millis(40)).await;
        let (_, goods_box) = self
            .locate_goods(Duration::from_secs_f64(2.5), None)
            .await?
            .ok_or_else(|| anyhow!("goods template not found"))?;
        self.goods_box = Some(goods_box);
        self.log("info", STEP_4, "已建立搜索上下文并缓存商品卡片", Some(0.32));
        Ok(())
    }

    async fn precache(&mut self) -> Result<()> {
        for idx in 0..3 {
            if self.open_detail().await?.is_some() {
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
        if self.open_detail().await?.is_none() {
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
            click_box(&self.capture_active()?, buy_box)?;
            if !self.wait_buy_ok().await? {
                let _ = self.close_detail().await?;
                return Ok(0);
            }
            self.record_purchase(task, unit_price, qty, used_max)?;
            self.dismiss_overlay().await?;
            return Ok(qty);
        }

        click_box(&self.capture_active()?, buy_box)?;
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
            let center = to_global(&self.capture_active()?, buy_box);
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
                click_box(&self.capture_active()?, max_box)?;
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
        click_box(&self.capture_active()?, qty_box)?;
        self.nap(Duration::from_millis(30)).await;
        type_text(&qty.to_string())?;
        self.nap(Duration::from_millis(30)).await;
        Ok(true)
    }

    async fn read_qty(&mut self) -> Result<Option<i64>> {
        let Some(roi) = self.qty_roi() else {
            return Ok(None);
        };
        let gray = crop_gray(&self.capture_active()?.image, roi)?;
        let image = threshold(resize(gray, 2.0));
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
        let captured = self.capture_active()?;
        let roi = avg_roi(
            buy_box,
            self.request.goods.exchangeable,
            &self.request.config,
            captured.width,
            captured.height,
        );
        let gray = crop_gray(&captured.image, roi)?;
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
        if let Some(buy_box) = self.detail.get("btn_buy").copied() {
            click_box(&self.capture_active()?, buy_box)?;
        } else if let Some((window, ok_box)) = self
            .locate_active("buy_ok", Duration::from_millis(120), None)
            .await?
        {
            click_box(&window, ok_box)?;
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
            click_box(&self.capture_active()?, close_box)?;
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
        if let Some((window, box_rect)) = self
            .locate_active("btn_penalty_confirm", Duration::from_secs(2), None)
            .await?
        {
            click_box(&window, box_rect)?;
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
        let (window, box_rect) = self
            .locate_active(slug, Duration::from_secs(2), None)
            .await?
            .ok_or_else(|| anyhow!("template {slug} not found"))?;
        click_box(&window, box_rect)?;
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

    async fn open_detail(&mut self) -> Result<Option<(CapturedWindow, MatchBox)>> {
        if self.detail_visible().await? {
            let current = self.capture_active()?;
            let buy_box = self.detail.get("btn_buy").copied().unwrap_or((0, 0, 0, 0));
            return Ok(Some((current, buy_box)));
        }
        if let Some(goods_box) = self.goods_box {
            let window = self.capture_active()?;
            click_box(&window, goods_box)?;
            self.nap(Duration::from_secs_f64(
                self.request
                    .config
                    .multi_snipe_tuning
                    .detail_open_settle_sec
                    .max(0.05),
            ))
            .await;
            if self.detail_visible().await? {
                return Ok(Some((self.capture_active()?, goods_box)));
            }
        }
        let matched = self.locate_goods(Duration::from_secs(2), None).await?;
        if let Some((window, goods_box)) = matched {
            self.goods_box = Some(goods_box);
            click_box(&window, goods_box)?;
            self.nap(Duration::from_secs_f64(
                self.request
                    .config
                    .multi_snipe_tuning
                    .detail_open_settle_sec
                    .max(0.05),
            ))
            .await;
            if self.detail_visible().await? {
                return Ok(Some((self.capture_active()?, goods_box)));
            }
        }
        Ok(None)
    }

    async fn cache_detail_controls(&mut self) -> Result<()> {
        let window = self.capture_active()?;
        for key in ["btn_buy", "btn_close", "qty_minus", "qty_plus", "btn_max"] {
            if key == "btn_max" && self.request.goods.big_category.trim() != "弹药" {
                continue;
            }
            if let Some(box_rect) = find_in_window(&window, self.template(key)?, None)? {
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
        if self
            .locate_active("home_indicator", Duration::from_millis(80), None)
            .await?
            .is_some()
        {
            return Ok("home");
        }
        if self
            .locate_active("market_indicator", Duration::from_millis(80), None)
            .await?
            .is_some()
        {
            return Ok("market");
        }
        if self.detail_visible().await? {
            return Ok("detail");
        }
        Ok("unknown")
    }

    async fn wait_any_scene(&mut self, timeout: Duration) -> Result<Option<CapturedWindow>> {
        let deadline = Instant::now() + timeout;
        while Instant::now() < deadline {
            if let Some(found) = self.find_scene_window().await? {
                return Ok(Some(found));
            }
            self.nap(Duration::from_millis(180)).await;
        }
        Ok(None)
    }

    async fn find_scene_window(&mut self) -> Result<Option<CapturedWindow>> {
        for slug in ["home_indicator", "market_indicator"] {
            if let Some((window, _)) = self
                .wait_any_template(slug, Duration::from_millis(0))
                .await?
            {
                return Ok(Some(window));
            }
        }
        Ok(None)
    }

    async fn wait_any_template(
        &mut self,
        slug: &str,
        timeout: Duration,
    ) -> Result<Option<(CapturedWindow, MatchBox)>> {
        let deadline = Instant::now() + timeout;
        loop {
            for window in enum_windows()? {
                let captured = capture_window(window)?;
                if let Some(box_rect) = find_in_window(&captured, self.template(slug)?, None)? {
                    self.active_window = Some(captured.hint.clone());
                    return Ok(Some((captured, box_rect)));
                }
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
    ) -> Result<Option<(CapturedWindow, MatchBox)>> {
        let deadline = Instant::now() + timeout;
        loop {
            if let Some(window) = self.capture_active_opt()? {
                if let Some(box_rect) = find_in_window(&window, self.template(slug)?, region)? {
                    return Ok(Some((window, box_rect)));
                }
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
    ) -> Result<Option<(CapturedWindow, MatchBox)>> {
        let deadline = Instant::now() + timeout;
        let goods_path = resolve_path(&self.request.paths, &self.request.goods.image_path);
        loop {
            if let Some(window) = self.capture_active_opt()? {
                if let Some(box_rect) =
                    locate_template_in_image(&window.image, &goods_path, 0.80, region)?
                {
                    return Ok(Some((window, box_rect)));
                }
            }
            if Instant::now() >= deadline {
                return Ok(None);
            }
            self.nap(Duration::from_millis(60)).await;
        }
    }

    fn capture_active(&mut self) -> Result<CapturedWindow> {
        self.capture_active_opt()?
            .ok_or_else(|| anyhow!("active window unavailable"))
    }

    fn capture_active_opt(&mut self) -> Result<Option<CapturedWindow>> {
        let Some(hint) = self.active_window.clone() else {
            return Ok(None);
        };
        for window in enum_windows()? {
            let title = window.title().unwrap_or_default();
            let app_name = window.app_name().unwrap_or_default();
            if title == hint.title && app_name == hint.app_name {
                return Ok(Some(capture_window(window)?));
            }
        }
        Ok(None)
    }

    fn template(&self, slug: &str) -> Result<&TemplateEntry> {
        self.templates
            .get(slug)
            .ok_or_else(|| anyhow!("template missing: {slug}"))
    }

    fn record_price(&self, price: i64) -> Result<()> {
        self.request.repo.insert_price_history(&PriceHistoryRecord {
            id: format!("price-{}", Uuid::new_v4()),
            item_id: self.request.goods.id.clone(),
            item_name: self.label(),
            category: optional_str(&self.request.goods.big_category),
            price,
            observed_at: now_iso(),
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

fn enum_windows() -> Result<Vec<Window>> {
    Ok(Window::all()
        .context("failed to enumerate windows")?
        .into_iter()
        .filter(|window| !window.is_minimized().unwrap_or(false))
        .filter(|window| window.width().unwrap_or_default() >= 200)
        .filter(|window| window.height().unwrap_or_default() >= 120)
        .collect())
}

fn capture_window(window: Window) -> Result<CapturedWindow> {
    Ok(CapturedWindow {
        hint: WindowHint {
            title: window.title().unwrap_or_default(),
            app_name: window.app_name().unwrap_or_default(),
        },
        x: window.x().unwrap_or_default(),
        y: window.y().unwrap_or_default(),
        width: window.width().unwrap_or_default() as i32,
        height: window.height().unwrap_or_default() as i32,
        image: window.capture_image().context("failed to capture window")?,
    })
}

fn find_in_window(
    window: &CapturedWindow,
    template: &TemplateEntry,
    region: Option<MatchBox>,
) -> Result<Option<MatchBox>> {
    locate_template_in_image(&window.image, &template.path, template.confidence, region)
}

fn click_box(window: &CapturedWindow, local: MatchBox) -> Result<()> {
    let (x, y) = to_global(window, local);
    click_point(x, y)
}

fn to_global(window: &CapturedWindow, local: MatchBox) -> (i32, i32) {
    (
        window.x + local.0 + local.2 / 2,
        window.y + local.1 + local.3 / 2,
    )
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

fn crop_gray(image: &RgbaImage, rect: MatchBox) -> Result<GrayImage> {
    let cropped = image::imageops::crop_imm(
        image,
        rect.0.max(0) as u32,
        rect.1.max(0) as u32,
        rect.2.max(1) as u32,
        rect.3.max(1) as u32,
    )
    .to_image();
    Ok(image::DynamicImage::ImageRgba8(cropped).to_luma8())
}

fn resize(image: GrayImage, scale: f64) -> GrayImage {
    let scale = scale.clamp(0.6, 2.5);
    if (scale - 1.0).abs() < f64::EPSILON {
        return image;
    }
    image::imageops::resize(
        &image,
        ((image.width() as f64) * scale).round().max(1.0) as u32,
        ((image.height() as f64) * scale).round().max(1.0) as u32,
        FilterType::Triangle,
    )
}

fn threshold(mut image: GrayImage) -> GrayImage {
    for pixel in image.pixels_mut() {
        pixel.0[0] = if pixel.0[0] > 128 { 255 } else { 0 };
    }
    image
}

fn top_half(image: GrayImage) -> GrayImage {
    image::imageops::crop_imm(&image, 0, 0, image.width(), (image.height() / 2).max(1)).to_image()
}

fn avg_roi(
    buy_box: MatchBox,
    exchangeable: bool,
    config: &AppConfig,
    max_width: i32,
    max_height: i32,
) -> MatchBox {
    let mut dist = config.avg_price_area.distance_from_buy_top.max(1) as i32;
    if exchangeable {
        dist += 30;
    }
    let height = config.avg_price_area.height.max(10) as i32;
    let y_bottom = (buy_box.1 - dist).clamp(1, max_height);
    let y_top = (y_bottom - height).clamp(0, max_height.saturating_sub(1));
    (
        buy_box.0.clamp(0, max_width.saturating_sub(1)),
        y_top,
        buy_box.2.max(1).min(max_width.saturating_sub(buy_box.0)),
        (y_bottom - y_top).max(1),
    )
}

fn infer_qty_from_max(max_box: MatchBox) -> MatchBox {
    let width = (max_box.2 * 24 / 10).clamp(80, 140);
    let gap = (max_box.2 * 35 / 100).clamp(8, 20);
    let height = (max_box.3 + 12).clamp(28, 44);
    (
        max_box.0 - gap - width,
        max_box.1 - ((height - max_box.3) / 2).max(4),
        width,
        height,
    )
}

fn parse_digits(text: &str) -> Option<i64> {
    let digits: String = text.chars().filter(|ch| ch.is_ascii_digit()).collect();
    digits.parse::<i64>().ok()
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
