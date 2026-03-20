use std::fs;
use std::io::Cursor;
use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{Context, Result};
use font8x8::{BASIC_FONTS, UnicodeFonts};
use image::{DynamicImage, ImageFormat, Rgba, RgbaImage};
use imageproc::drawing::{draw_filled_circle_mut, draw_hollow_rect_mut};
use imageproc::rect::Rect;
use serde::Serialize;

use crate::app::types::now_iso;
use crate::automation::capture::CapturedImage;
use crate::automation::vision::MatchBox;

const MAX_ROUND_BYTES: usize = 128 * 1024 * 1024;
const LABEL_PADDING: i32 = 4;
const CHAR_SIZE: i32 = 8;
const LINE_HEIGHT: i32 = 10;
const MAX_LABEL_WIDTH_CHARS: usize = 44;
const MAX_LABEL_LINES: usize = 6;

const COLOR_TEMPLATE_HIT: Rgba<u8> = Rgba([34, 197, 94, 255]);
const COLOR_TEMPLATE_MISS: Rgba<u8> = Rgba([239, 68, 68, 255]);
const COLOR_SEARCH_REGION: Rgba<u8> = Rgba([59, 130, 246, 255]);
const COLOR_OCR_ROI: Rgba<u8> = Rgba([245, 158, 11, 255]);
const COLOR_CLICK: Rgba<u8> = Rgba([220, 38, 38, 255]);
const COLOR_INPUT: Rgba<u8> = Rgba([14, 165, 233, 255]);
const COLOR_LABEL_BG: Rgba<u8> = Rgba([255, 255, 255, 230]);
const COLOR_LABEL_TEXT: Rgba<u8> = Rgba([17, 24, 39, 255]);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RoundStatus {
    Completed,
    Failed,
    Stopped,
}

impl RoundStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Stopped => "stopped",
        }
    }
}

#[derive(Debug, Clone)]
pub struct RoundFlushResult {
    pub round_index: u32,
    pub status: RoundStatus,
    pub step_count: usize,
    pub truncated: bool,
    pub skipped_steps: u32,
    pub round_dir: PathBuf,
}

#[derive(Debug)]
pub struct DebugRecorder {
    enabled: bool,
    mode: &'static str,
    session_id: String,
    session_dir: PathBuf,
    round_seq: u32,
    max_round_bytes: usize,
    current_round: Option<BufferedRound>,
}

#[derive(Debug)]
struct BufferedRound {
    index: u32,
    started_at: String,
    steps: Vec<BufferedStep>,
    total_bytes: usize,
    truncated: bool,
    skipped_steps: u32,
}

#[derive(Debug)]
struct BufferedStep {
    seq: u32,
    kind: String,
    stage: String,
    file_name: String,
    notes: Vec<String>,
    created_at: String,
    bytes: Vec<u8>,
}

#[derive(Debug, Clone, Copy)]
struct LocalRect {
    x: i32,
    y: i32,
    width: i32,
    height: i32,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct RoundManifest {
    mode: String,
    session_id: String,
    round_index: u32,
    status: String,
    truncated: bool,
    skipped_steps: u32,
    total_bytes: usize,
    started_at: String,
    flushed_at: String,
    steps: Vec<StepManifest>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct StepManifest {
    seq: u32,
    kind: String,
    stage: String,
    file_name: String,
    created_at: String,
    notes: Vec<String>,
}

impl DebugRecorder {
    pub fn new(
        debug_root: &Path,
        mode: &'static str,
        session_id: impl Into<String>,
        enabled: bool,
    ) -> Self {
        let session_id = session_id.into();
        Self {
            enabled,
            mode,
            session_dir: debug_root.join(mode).join(&session_id),
            session_id,
            round_seq: 0,
            max_round_bytes: MAX_ROUND_BYTES,
            current_round: None,
        }
    }

    pub fn enabled(&self) -> bool {
        self.enabled
    }

    pub fn session_dir(&self) -> &Path {
        &self.session_dir
    }

    pub fn begin_round(&mut self) {
        if !self.enabled || self.current_round.is_some() {
            return;
        }
        self.round_seq = self.round_seq.saturating_add(1);
        self.current_round = Some(BufferedRound {
            index: self.round_seq,
            started_at: now_iso(),
            steps: Vec::new(),
            total_bytes: 0,
            truncated: false,
            skipped_steps: 0,
        });
    }

    pub fn finish_round(&mut self, status: RoundStatus) -> Result<Option<RoundFlushResult>> {
        if !self.enabled {
            self.current_round = None;
            return Ok(None);
        }
        let Some(round) = self.current_round.take() else {
            return Ok(None);
        };
        let round_dir = self
            .session_dir
            .join(format!("round-{:04}-{}", round.index, status.as_str()));
        fs::create_dir_all(&round_dir)
            .with_context(|| format!("failed to create {}", round_dir.display()))?;

        for step in &round.steps {
            let path = round_dir.join(&step.file_name);
            fs::write(&path, &step.bytes)
                .with_context(|| format!("failed to write {}", path.display()))?;
        }

        let manifest = RoundManifest {
            mode: self.mode.to_string(),
            session_id: self.session_id.clone(),
            round_index: round.index,
            status: status.as_str().to_string(),
            truncated: round.truncated,
            skipped_steps: round.skipped_steps,
            total_bytes: round.total_bytes,
            started_at: round.started_at,
            flushed_at: now_iso(),
            steps: round
                .steps
                .into_iter()
                .map(|step| StepManifest {
                    seq: step.seq,
                    kind: step.kind,
                    stage: step.stage,
                    file_name: step.file_name,
                    created_at: step.created_at,
                    notes: step.notes,
                })
                .collect(),
        };
        let manifest_path = round_dir.join("manifest.json");
        fs::write(
            &manifest_path,
            serde_json::to_vec_pretty(&manifest).context("failed to serialize round manifest")?,
        )
        .with_context(|| format!("failed to write {}", manifest_path.display()))?;

        Ok(Some(RoundFlushResult {
            round_index: manifest.round_index,
            status,
            step_count: manifest.steps.len(),
            truncated: manifest.truncated,
            skipped_steps: manifest.skipped_steps,
            round_dir,
        }))
    }

    pub fn flush_active_round_on_drop(
        &mut self,
        status: RoundStatus,
    ) -> Option<RoundFlushResult> {
        self.finish_round(status).ok().flatten()
    }

    pub fn record_template(
        &mut self,
        screen: &CapturedImage,
        stage: &str,
        slug: &str,
        threshold: f64,
        confidence: f64,
        elapsed: Duration,
        search_region: Option<MatchBox>,
        matched: Option<MatchBox>,
    ) -> Result<()> {
        if !self.enabled {
            return Ok(());
        }
        let mut image = screen.image.clone();
        let local_region = search_region.and_then(|rect| global_box_to_local(screen, rect));
        let local_match = matched.and_then(|rect| global_box_to_local(screen, rect));
        if let Some(region) = local_region {
            draw_box(&mut image, region, COLOR_SEARCH_REGION);
        }
        if let Some(hit) = local_match {
            draw_box(&mut image, hit, COLOR_TEMPLATE_HIT);
        }
        let anchor = local_match.or(local_region).unwrap_or(LocalRect {
            x: 8,
            y: 8,
            width: 1,
            height: 1,
        });
        let status = if local_match.is_some() { "hit" } else { "miss" };
        let notes = vec![
            format!("slug={slug}"),
            format!("state={status}"),
            format!("threshold={threshold:.3}"),
            format!("confidence={confidence:.3}"),
            format!("elapsed_ms={:.1}", elapsed.as_secs_f64() * 1000.0),
        ];
        draw_label(&mut image, anchor, &notes, box_color(status));
        self.push_image_step("template", stage, image, notes)
    }

    pub fn record_ocr(
        &mut self,
        screen: &CapturedImage,
        stage: &str,
        roi: MatchBox,
        texts: &[String],
        elapsed: Duration,
    ) -> Result<()> {
        if !self.enabled {
            return Ok(());
        }
        let Some(local_roi) = global_box_to_local(screen, roi) else {
            return Ok(());
        };
        let mut image = screen.image.clone();
        draw_box(&mut image, local_roi, COLOR_OCR_ROI);
        let mut notes = vec![
            format!("elapsed_ms={:.1}", elapsed.as_secs_f64() * 1000.0),
            format!("result={}", summarize_texts(texts)),
        ];
        if texts.is_empty() {
            notes.push("empty=true".to_string());
        }
        draw_label(&mut image, local_roi, &notes, COLOR_OCR_ROI);
        self.push_image_step("ocr", stage, image, notes)
    }

    pub fn record_click(
        &mut self,
        screen: &CapturedImage,
        stage: &str,
        point: (i32, i32),
        target: Option<MatchBox>,
    ) -> Result<()> {
        if !self.enabled {
            return Ok(());
        }
        let mut image = screen.image.clone();
        let local_target = target.and_then(|rect| global_box_to_local(screen, rect));
        if let Some(rect) = local_target {
            draw_box(&mut image, rect, COLOR_CLICK);
        }
        if let Some((x, y)) = global_point_to_local(screen, point) {
            draw_filled_circle_mut(&mut image, (x, y), 5, COLOR_CLICK);
        }
        let anchor = local_target.unwrap_or(LocalRect {
            x: point.0.saturating_sub(screen.x),
            y: point.1.saturating_sub(screen.y),
            width: 1,
            height: 1,
        });
        let mut notes = vec![format!("point=({}, {})", point.0, point.1)];
        if let Some(rect) = target {
            notes.push(format!("target={},{},{},{}", rect.0, rect.1, rect.2, rect.3));
        }
        draw_label(&mut image, anchor, &notes, COLOR_CLICK);
        self.push_image_step("click", stage, image, notes)
    }

    pub fn record_input(
        &mut self,
        screen: &CapturedImage,
        stage: &str,
        target: Option<MatchBox>,
        value: &str,
    ) -> Result<()> {
        if !self.enabled {
            return Ok(());
        }
        let mut image = screen.image.clone();
        let local_target = target.and_then(|rect| global_box_to_local(screen, rect));
        if let Some(rect) = local_target {
            draw_box(&mut image, rect, COLOR_INPUT);
        }
        let anchor = local_target.unwrap_or(LocalRect {
            x: 8,
            y: 8,
            width: 1,
            height: 1,
        });
        let notes = vec![format!("value={}", escape_ascii(value))];
        draw_label(&mut image, anchor, &notes, COLOR_INPUT);
        self.push_image_step("input", stage, image, notes)
    }

    fn push_image_step(
        &mut self,
        kind: &str,
        stage: &str,
        image: RgbaImage,
        notes: Vec<String>,
    ) -> Result<()> {
        let Some(round) = self.current_round.as_mut() else {
            return Ok(());
        };
        if round.truncated {
            round.skipped_steps = round.skipped_steps.saturating_add(1);
            return Ok(());
        }

        let bytes = encode_png(&image)?;
        if round.total_bytes.saturating_add(bytes.len()) > self.max_round_bytes {
            round.truncated = true;
            round.skipped_steps = round.skipped_steps.saturating_add(1);
            return Ok(());
        }

        let seq = round.steps.len() as u32 + 1;
        let file_name = format!(
            "{:04}_{}_{}.png",
            seq,
            sanitize_component(kind),
            sanitize_component(stage)
        );
        round.total_bytes += bytes.len();
        round.steps.push(BufferedStep {
            seq,
            kind: kind.to_string(),
            stage: stage.to_string(),
            file_name,
            notes,
            created_at: now_iso(),
            bytes,
        });
        Ok(())
    }

    #[cfg(test)]
    fn set_max_round_bytes_for_test(&mut self, bytes: usize) {
        self.max_round_bytes = bytes;
    }
}

fn box_color(status: &str) -> Rgba<u8> {
    if status == "hit" {
        COLOR_TEMPLATE_HIT
    } else {
        COLOR_TEMPLATE_MISS
    }
}

fn encode_png(image: &RgbaImage) -> Result<Vec<u8>> {
    let mut cursor = Cursor::new(Vec::new());
    DynamicImage::ImageRgba8(image.clone())
        .write_to(&mut cursor, ImageFormat::Png)
        .context("failed to encode debug image as png")?;
    Ok(cursor.into_inner())
}

fn draw_box(image: &mut RgbaImage, rect: LocalRect, color: Rgba<u8>) {
    if rect.width <= 0 || rect.height <= 0 {
        return;
    }
    draw_hollow_rect_mut(
        image,
        Rect::at(rect.x, rect.y).of_size(rect.width as u32, rect.height as u32),
        color,
    );
}

fn draw_label(image: &mut RgbaImage, anchor: LocalRect, notes: &[String], border: Rgba<u8>) {
    let lines = wrap_lines(notes);
    if lines.is_empty() {
        return;
    }
    let width_chars = lines.iter().map(|line| line.len()).max().unwrap_or(0) as i32;
    let label_width = width_chars * CHAR_SIZE + LABEL_PADDING * 2;
    let label_height = lines.len() as i32 * LINE_HEIGHT + LABEL_PADDING * 2;
    let (left, top) = choose_label_origin(image, anchor, label_width, label_height);
    fill_rect(image, left, top, label_width, label_height, COLOR_LABEL_BG);
    draw_rect(image, left, top, label_width, label_height, border);
    for (idx, line) in lines.iter().enumerate() {
        draw_text(
            image,
            left + LABEL_PADDING,
            top + LABEL_PADDING + idx as i32 * LINE_HEIGHT,
            line,
            COLOR_LABEL_TEXT,
        );
    }
}

fn draw_rect(image: &mut RgbaImage, left: i32, top: i32, width: i32, height: i32, color: Rgba<u8>) {
    if width <= 0 || height <= 0 {
        return;
    }
    draw_hollow_rect_mut(
        image,
        Rect::at(left, top).of_size(width as u32, height as u32),
        color,
    );
}

fn fill_rect(
    image: &mut RgbaImage,
    left: i32,
    top: i32,
    width: i32,
    height: i32,
    color: Rgba<u8>,
) {
    if width <= 0 || height <= 0 {
        return;
    }
    let right = (left + width).min(image.width() as i32);
    let bottom = (top + height).min(image.height() as i32);
    for y in top.max(0)..bottom {
        for x in left.max(0)..right {
            blend_pixel(image, x as u32, y as u32, color);
        }
    }
}

fn blend_pixel(image: &mut RgbaImage, x: u32, y: u32, color: Rgba<u8>) {
    let base = image.get_pixel_mut(x, y);
    let alpha = f32::from(color[3]) / 255.0;
    let inv = 1.0 - alpha;
    base[0] = (f32::from(color[0]) * alpha + f32::from(base[0]) * inv).round() as u8;
    base[1] = (f32::from(color[1]) * alpha + f32::from(base[1]) * inv).round() as u8;
    base[2] = (f32::from(color[2]) * alpha + f32::from(base[2]) * inv).round() as u8;
    base[3] = 255;
}

fn draw_text(image: &mut RgbaImage, left: i32, top: i32, raw: &str, color: Rgba<u8>) {
    let text = escape_ascii(raw);
    for (idx, ch) in text.chars().enumerate() {
        let x = left + idx as i32 * CHAR_SIZE;
        if let Some(glyph) = BASIC_FONTS.get(ch) {
            for (row, bits) in glyph.iter().enumerate() {
                for col in 0..8 {
                    if (bits >> col) & 1 == 1 {
                        let px = x + col;
                        let py = top + row as i32;
                        if px >= 0
                            && py >= 0
                            && (px as u32) < image.width()
                            && (py as u32) < image.height()
                        {
                            image.put_pixel(px as u32, py as u32, color);
                        }
                    }
                }
            }
        }
    }
}

fn choose_label_origin(
    image: &RgbaImage,
    anchor: LocalRect,
    label_width: i32,
    label_height: i32,
) -> (i32, i32) {
    let image_width = image.width() as i32;
    let image_height = image.height() as i32;
    let candidates = [
        (anchor.x + anchor.width + 8, anchor.y),
        (anchor.x - label_width - 8, anchor.y),
        (anchor.x, anchor.y + anchor.height + 8),
        (anchor.x, anchor.y - label_height - 8),
    ];
    for (left, top) in candidates {
        if left >= 0
            && top >= 0
            && left + label_width <= image_width
            && top + label_height <= image_height
        {
            return (left, top);
        }
    }
    let max_left = if label_width >= image_width {
        0
    } else {
        image_width - label_width
    };
    let max_top = if label_height >= image_height {
        0
    } else {
        image_height - label_height
    };
    (
        anchor.x.clamp(0, max_left),
        anchor.y.clamp(0, max_top),
    )
}

fn wrap_lines(lines: &[String]) -> Vec<String> {
    let mut wrapped = Vec::new();
    for line in lines {
        let escaped = escape_ascii(line);
        if escaped.is_empty() {
            continue;
        }
        let chars = escaped.chars().collect::<Vec<_>>();
        for chunk in chars.chunks(MAX_LABEL_WIDTH_CHARS) {
            wrapped.push(chunk.iter().collect::<String>());
            if wrapped.len() >= MAX_LABEL_LINES {
                if let Some(last) = wrapped.last_mut() {
                    *last = truncate_with_ellipsis(last);
                }
                return wrapped;
            }
        }
    }
    wrapped
}

fn truncate_with_ellipsis(value: &str) -> String {
    let mut chars = value.chars().collect::<Vec<_>>();
    if chars.len() + 3 > MAX_LABEL_WIDTH_CHARS {
        chars.truncate(MAX_LABEL_WIDTH_CHARS.saturating_sub(3));
    }
    format!("{}...", chars.into_iter().collect::<String>())
}

fn summarize_texts(texts: &[String]) -> String {
    if texts.is_empty() {
        return "<empty>".to_string();
    }
    let joined = texts
        .iter()
        .take(3)
        .map(|item| escape_ascii(item))
        .collect::<Vec<_>>()
        .join(" | ");
    if texts.len() > 3 {
        format!("{joined} | +{}", texts.len() - 3)
    } else {
        joined
    }
}

fn escape_ascii(value: &str) -> String {
    value.chars().flat_map(|ch| ch.escape_default()).collect()
}

fn sanitize_component(raw: &str) -> String {
    let compact = raw.trim().to_ascii_lowercase();
    let mut out = String::with_capacity(compact.len());
    for ch in compact.chars() {
        if ch.is_ascii_alphanumeric() {
            out.push(ch);
        } else if matches!(ch, '-' | '_') {
            out.push(ch);
        } else {
            out.push('_');
        }
    }
    let out = out.trim_matches('_').to_string();
    if out.is_empty() {
        "step".to_string()
    } else {
        out
    }
}

fn global_point_to_local(screen: &CapturedImage, point: (i32, i32)) -> Option<(i32, i32)> {
    let x = point.0 - screen.x;
    let y = point.1 - screen.y;
    if x < 0 || y < 0 || x >= screen.width || y >= screen.height {
        return None;
    }
    Some((x, y))
}

fn global_box_to_local(screen: &CapturedImage, rect: MatchBox) -> Option<LocalRect> {
    if rect.2 <= 0 || rect.3 <= 0 {
        return None;
    }
    let left = rect.0 - screen.x;
    let top = rect.1 - screen.y;
    let right = left + rect.2;
    let bottom = top + rect.3;
    let clipped_left = left.clamp(0, screen.width.saturating_sub(1));
    let clipped_top = top.clamp(0, screen.height.saturating_sub(1));
    let clipped_right = right.clamp(clipped_left + 1, screen.width);
    let clipped_bottom = bottom.clamp(clipped_top + 1, screen.height);
    Some(LocalRect {
        x: clipped_left,
        y: clipped_top,
        width: clipped_right - clipped_left,
        height: clipped_bottom - clipped_top,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_capture(width: u32, height: u32) -> CapturedImage {
        CapturedImage {
            x: 0,
            y: 0,
            width: width as i32,
            height: height as i32,
            image: RgbaImage::from_pixel(width, height, Rgba([16, 16, 16, 255])),
        }
    }

    fn temp_root() -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "arena-buyer-debug-recorder-{}",
            now_iso().replace(':', "-")
        ));
        let _ = fs::remove_dir_all(&path);
        path
    }

    #[test]
    fn draws_click_marker_in_red() {
        let mut recorder = DebugRecorder::new(&temp_root(), "single", "session-a", true);
        recorder.begin_round();
        recorder
            .record_click(&test_capture(80, 60), "click_buy", (24, 18), Some((10, 8, 30, 20)))
            .expect("click step should record");
        let round = recorder.current_round.as_ref().expect("round should exist");
        let image = image::load_from_memory(&round.steps[0].bytes)
            .expect("png should decode")
            .into_rgba8();
        let found = image
            .pixels()
            .any(|pixel| pixel[0] > 180 && pixel[1] < 120 && pixel[2] < 120);
        assert!(found, "expected a red marker somewhere in the output");
    }

    #[test]
    fn draws_template_hit_box_in_green() {
        let mut recorder = DebugRecorder::new(&temp_root(), "single", "session-b", true);
        recorder.begin_round();
        recorder
            .record_template(
                &test_capture(80, 60),
                "probe_btn_buy",
                "btn_buy",
                0.88,
                0.93,
                Duration::from_millis(12),
                Some((8, 8, 32, 24)),
                Some((12, 10, 20, 12)),
            )
            .expect("template step should record");
        let round = recorder.current_round.as_ref().expect("round should exist");
        let image = image::load_from_memory(&round.steps[0].bytes)
            .expect("png should decode")
            .into_rgba8();
        let found = image
            .pixels()
            .any(|pixel| pixel[1] > 160 && pixel[0] < 120);
        assert!(found, "expected a green template outline somewhere in the output");
    }

    #[test]
    fn draws_ocr_roi_in_amber() {
        let mut recorder = DebugRecorder::new(&temp_root(), "multi", "session-c", true);
        recorder.begin_round();
        recorder
            .record_ocr(
                &test_capture(80, 60),
                "avg_price",
                (18, 16, 24, 12),
                &["123K".to_string()],
                Duration::from_millis(20),
            )
            .expect("ocr step should record");
        let round = recorder.current_round.as_ref().expect("round should exist");
        let image = image::load_from_memory(&round.steps[0].bytes)
            .expect("png should decode")
            .into_rgba8();
        let pixel = image.get_pixel(18, 16);
        assert!(pixel[0] > 180);
        assert!(pixel[1] > 100);
    }

    #[test]
    fn flushes_round_into_status_directory() {
        let root = temp_root();
        let mut recorder = DebugRecorder::new(&root, "single", "session-d", true);
        recorder.begin_round();
        recorder
            .record_click(&test_capture(64, 64), "click_search", (16, 16), None)
            .expect("click step should record");
        let result = recorder
            .finish_round(RoundStatus::Completed)
            .expect("round should flush")
            .expect("flush result should exist");
        assert_eq!(result.round_index, 1);
        assert_eq!(result.status, RoundStatus::Completed);
        assert!(result.round_dir.ends_with("round-0001-completed"));
        assert!(result.round_dir.join("manifest.json").exists());
        assert!(result.round_dir.join("0001_click_click_search.png").exists());
        let manifest = fs::read_to_string(result.round_dir.join("manifest.json"))
            .expect("manifest should exist");
        assert!(manifest.contains("\"status\": \"completed\""));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn marks_round_as_truncated_when_limit_is_hit() {
        let root = temp_root();
        let mut recorder = DebugRecorder::new(&root, "multi", "session-e", true);
        recorder.set_max_round_bytes_for_test(32);
        recorder.begin_round();
        recorder
            .record_click(&test_capture(64, 64), "click_buy", (16, 16), None)
            .expect("record should not fail");
        let result = recorder
            .finish_round(RoundStatus::Stopped)
            .expect("flush should work")
            .expect("result should exist");
        assert!(result.truncated);
        assert_eq!(result.step_count, 0);
        assert_eq!(result.skipped_steps, 1);
        let manifest = fs::read_to_string(result.round_dir.join("manifest.json"))
            .expect("manifest should exist");
        assert!(manifest.contains("\"truncated\": true"));
        let _ = fs::remove_dir_all(root);
    }
}
