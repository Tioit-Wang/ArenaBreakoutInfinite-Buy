use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use anyhow::Result;
use tokio::time::sleep;

use crate::automation::capture::CapturedImage;
use crate::automation::debug_recorder::{DebugRecorder, RoundStatus};
use crate::automation::input::{click_point, type_text};
use crate::automation::vision::{MatchBox, probe_template_in_image_fast};
use crate::config::paths::AppPaths;

#[derive(Debug, Clone)]
pub struct TemplateProbeEntry {
    pub path: PathBuf,
    pub confidence: f64,
}

pub trait SessionDebugSupport {
    fn debug_recorder(&mut self) -> &mut DebugRecorder;
    fn debug_recorder_ref(&self) -> &DebugRecorder;
    fn debug_mode_label(&self) -> &'static str;
    fn emit_debug_log(&self, level: &str, message: String);
    fn capture_screen_for_debug(&mut self, stage: &str) -> Result<CapturedImage>;
    fn template_probe_entry(&self, slug: &str) -> Result<TemplateProbeEntry>;

    fn debug_enabled(&self) -> bool {
        self.debug_recorder_ref().enabled()
    }

    fn announce_debug_session(&self) {
        if !self.debug_enabled() {
            return;
        }
        self.emit_debug_log(
            "info",
            format!(
                "已启用{}调试模式，目录={}",
                self.debug_mode_label(),
                self.debug_recorder_ref().session_dir().display()
            ),
        );
    }

    fn finish_debug_round(&mut self, status: RoundStatus) {
        let flush = self.debug_recorder().finish_round(status);
        match flush {
            Ok(Some(flush)) => self.emit_debug_log(
                "info",
                format!(
                    "调试轮次已写入，round={} status={} steps={} truncated={} skipped={} dir={}",
                    flush.round_index,
                    flush.status.as_str(),
                    flush.step_count,
                    flush.truncated,
                    flush.skipped_steps,
                    flush.round_dir.display()
                ),
            ),
            Ok(None) => {}
            Err(error) => self.emit_debug_log("error", format!("调试轮次写入失败：{error}")),
        }
    }

    fn click_box_step(&mut self, stage: &str, rect: MatchBox) -> Result<()> {
        self.click_point_step(stage, center_of_box(rect), Some(rect))
    }

    fn click_point_step(
        &mut self,
        stage: &str,
        point: (i32, i32),
        target: Option<MatchBox>,
    ) -> Result<()> {
        if self.debug_enabled() {
            let screen = self.capture_screen_for_debug(stage)?;
            self.debug_recorder()
                .record_click(&screen, stage, point, target)?;
        }
        click_point(point.0, point.1)
    }

    fn type_text_step(&mut self, stage: &str, target: Option<MatchBox>, value: &str) -> Result<()> {
        if self.debug_enabled() {
            let screen = self.capture_screen_for_debug(stage)?;
            self.debug_recorder()
                .record_input(&screen, stage, target, value)?;
        }
        type_text(value)
    }

    fn record_ocr_step(
        &mut self,
        stage: &str,
        screen: &CapturedImage,
        roi: MatchBox,
        texts: &[String],
        elapsed: Duration,
    ) -> Result<()> {
        self.debug_recorder()
            .record_ocr(screen, stage, roi, texts, elapsed)
    }

    async fn wait_any_template(
        &mut self,
        slug: &str,
        timeout: Duration,
    ) -> Result<Option<MatchBox>> {
        let deadline = Instant::now() + timeout;
        let stage = format!("wait_any_template_{slug}");
        let template = self.template_probe_entry(slug)?;
        loop {
            let screen = self.capture_screen_for_debug(&stage)?;
            if let Some(box_rect) = self.locate_template_in_capture(
                &screen,
                &stage,
                slug,
                &template.path,
                template.confidence,
                None,
            )? {
                return Ok(Some(box_rect));
            }
            if timeout.is_zero() || Instant::now() >= deadline {
                return Ok(None);
            }
            sleep(Duration::from_millis(120)).await;
        }
    }

    async fn locate_active(
        &mut self,
        slug: &str,
        timeout: Duration,
        region: Option<MatchBox>,
    ) -> Result<Option<MatchBox>> {
        let deadline = Instant::now() + timeout;
        let stage = format!("locate_active_{slug}");
        let template = self.template_probe_entry(slug)?;
        loop {
            let screen = self.capture_screen_for_debug(&stage)?;
            if let Some(box_rect) = self.locate_template_in_capture(
                &screen,
                &stage,
                slug,
                &template.path,
                template.confidence,
                region,
            )? {
                return Ok(Some(box_rect));
            }
            if timeout.is_zero() || Instant::now() >= deadline {
                return Ok(None);
            }
            sleep(Duration::from_millis(60)).await;
        }
    }

    fn detect_scene_in_capture(&mut self, screen: &CapturedImage) -> Result<&'static str> {
        let home = self.template_probe_entry("home_indicator")?;
        if self
            .locate_template_in_capture(
                screen,
                "detect_scene_home_indicator",
                "home_indicator",
                &home.path,
                home.confidence,
                None,
            )?
            .is_some()
        {
            return Ok("home");
        }

        let market = self.template_probe_entry("market_indicator")?;
        if self
            .locate_template_in_capture(
                screen,
                "detect_scene_market_indicator",
                "market_indicator",
                &market.path,
                market.confidence,
                None,
            )?
            .is_some()
        {
            return Ok("market");
        }

        let buy = self.template_probe_entry("btn_buy")?;
        let close = self.template_probe_entry("btn_close")?;
        if self
            .locate_template_in_capture(
                screen,
                "detect_scene_btn_buy",
                "btn_buy",
                &buy.path,
                buy.confidence,
                None,
            )?
            .is_some()
            && self
                .locate_template_in_capture(
                    screen,
                    "detect_scene_btn_close",
                    "btn_close",
                    &close.path,
                    close.confidence,
                    None,
                )?
                .is_some()
        {
            return Ok("detail");
        }

        Ok("unknown")
    }

    fn locate_template_in_capture(
        &mut self,
        screen: &CapturedImage,
        stage: &str,
        slug: &str,
        template_path: &Path,
        confidence: f64,
        region: Option<MatchBox>,
    ) -> Result<Option<MatchBox>> {
        let local_region = region.and_then(|rect| global_box_to_local(screen, rect));
        let started = Instant::now();
        let probe =
            probe_template_in_image_fast(&screen.image, template_path, confidence, local_region)?;
        let matched = if probe.matched {
            probe.box_rect
                .map(|rect| local_box_to_global(screen, (rect.x, rect.y, rect.width, rect.height)))
        } else {
            None
        };
        self.debug_recorder().record_template(
            screen,
            stage,
            slug,
            confidence,
            probe.confidence,
            started.elapsed(),
            region,
            matched,
        )?;
        Ok(matched)
    }
}

pub fn resolve_path(paths: &AppPaths, raw: &str) -> PathBuf {
    let path = PathBuf::from(raw);
    if path.is_absolute() {
        path
    } else {
        paths.resolve_data_path(raw)
    }
}

pub fn split_args(raw: &str) -> Vec<String> {
    raw.split_whitespace().map(str::to_string).collect()
}

pub fn center_of_box(rect: MatchBox) -> (i32, i32) {
    (rect.0 + rect.2 / 2, rect.1 + rect.3 / 2)
}

pub fn global_box_to_local(screen: &CapturedImage, rect: MatchBox) -> Option<MatchBox> {
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

pub fn local_box_to_global(screen: &CapturedImage, rect: MatchBox) -> MatchBox {
    (rect.0 + screen.x, rect.1 + screen.y, rect.2, rect.3)
}

pub fn optional_str(value: &str) -> Option<String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}
