use std::path::Path;
use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock};

use anyhow::{Context, Result, bail};
use corrmatch::{
    CompileConfigNoRot, CompiledTemplate, CorrMatchError, ImageView, MatchConfig, Matcher,
    Metric, RotationMode, Template,
};
use image::{GrayImage, RgbaImage, imageops};
use imageproc::template_matching::{MatchTemplateMethod, find_extremes, match_template};
use serde::{Deserialize, Serialize};

use crate::automation::capture::CaptureRegion;

pub type MatchBox = (i32, i32, i32, i32);

const CORRMATCH_MAX_LEVELS: usize = 4;
const CORRMATCH_BEAM_WIDTH: usize = 6;
const CORRMATCH_PER_ANGLE_TOPK: usize = 3;
const CORRMATCH_ROI_RADIUS: usize = 6;
const CORRMATCH_NMS_RADIUS: usize = 4;

#[derive(Clone)]
struct CachedTemplateMatcher {
    matcher: Arc<Matcher>,
    width: i32,
    height: i32,
}

static TEMPLATE_CACHE: OnceLock<Mutex<HashMap<String, CachedTemplateMatcher>>> = OnceLock::new();

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TemplateMatchResult {
    pub matched: bool,
    pub confidence: f64,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TemplateFileValidationResult {
    pub valid: bool,
    pub width: Option<u32>,
    pub height: Option<u32>,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TemplateProbeResult {
    pub matched: bool,
    pub confidence: f64,
    #[serde(rename = "box")]
    pub box_rect: Option<CaptureRegion>,
    pub message: String,
}

pub fn test_template(path: &str) -> Result<TemplateMatchResult> {
    let template = load_template_gray(Path::new(path))?;
    Ok(TemplateMatchResult {
        matched: true,
        confidence: 1.0,
        message: format!("模板可读，尺寸={}x{}", template.width(), template.height()),
    })
}

pub fn validate_template_file(path: &Path) -> Result<TemplateFileValidationResult> {
    if !path.exists() {
        return Ok(TemplateFileValidationResult {
            valid: false,
            width: None,
            height: None,
            message: format!("template file does not exist: {}", path.display()),
        });
    }
    let image =
        image::open(path).with_context(|| format!("failed to open template {}", path.display()))?;
    Ok(TemplateFileValidationResult {
        valid: true,
        width: Some(image.width()),
        height: Some(image.height()),
        message: format!("模板文件可读，尺寸={}x{}", image.width(), image.height()),
    })
}

pub fn load_template_gray(path: &Path) -> Result<GrayImage> {
    if !path.exists() {
        bail!("template file does not exist: {}", path.display());
    }
    let image =
        image::open(path).with_context(|| format!("failed to open template {}", path.display()))?;
    Ok(image.to_luma8())
}

pub fn locate_template_in_image(
    image: &RgbaImage,
    template_path: &Path,
    confidence: f64,
    region: Option<MatchBox>,
) -> Result<Option<MatchBox>> {
    Ok(probe_template_with_helper(image, template_path, confidence, region)?.box_rect.map(
        |rect| (rect.x, rect.y, rect.width, rect.height),
    ))
}

pub fn locate_gray_template_in_image(
    image: &GrayImage,
    template: &GrayImage,
    confidence: f64,
    region: Option<MatchBox>,
) -> Result<Option<MatchBox>> {
    Ok(probe_gray_template_in_image(image, template, confidence, region)?.box_rect.map(|rect| {
        (rect.x, rect.y, rect.width, rect.height)
    }))
}

pub fn probe_template_in_image(
    image: &RgbaImage,
    template_path: &Path,
    threshold: f64,
    region: Option<MatchBox>,
) -> Result<TemplateProbeResult> {
    probe_template_with_helper(image, template_path, threshold, region)
}

pub fn probe_template_in_image_fast(
    image: &RgbaImage,
    template_path: &Path,
    threshold: f64,
    region: Option<MatchBox>,
) -> Result<TemplateProbeResult> {
    probe_template_with_helper(image, template_path, threshold, region)
}

pub fn probe_gray_template_in_image(
    image: &GrayImage,
    template: &GrayImage,
    threshold: f64,
    region: Option<MatchBox>,
) -> Result<TemplateProbeResult> {
    let (x, y, width, height) = clamp_region(region, image.width() as i32, image.height() as i32)
        .unwrap_or((0, 0, image.width() as i32, image.height() as i32));
    if width < template.width() as i32 || height < template.height() as i32 {
        return Ok(TemplateProbeResult {
            matched: false,
            confidence: 0.0,
            box_rect: None,
            message: "目标区域小于模板尺寸".to_string(),
        });
    }
    let cropped =
        imageops::crop_imm(image, x as u32, y as u32, width as u32, height as u32).to_image();
    if cropped.width() < template.width() || cropped.height() < template.height() {
        return Ok(TemplateProbeResult {
            matched: false,
            confidence: 0.0,
            box_rect: None,
            message: "裁剪后的区域小于模板尺寸".to_string(),
        });
    }
    let response = match_template(
        &cropped,
        template,
        MatchTemplateMethod::CrossCorrelationNormalized,
    );
    let extremes = find_extremes(&response);
    let score = extremes.max_value as f64;
    if !score.is_finite() {
        return Ok(TemplateProbeResult {
            matched: false,
            confidence: 0.0,
            box_rect: None,
            message: "模板匹配得分无效".to_string(),
        });
    }
    let px = x + extremes.max_value_location.0 as i32;
    let py = y + extremes.max_value_location.1 as i32;
    let matched = score >= threshold;
    Ok(TemplateProbeResult {
        matched,
        confidence: score,
        box_rect: Some(CaptureRegion {
            x: px,
            y: py,
            width: template.width() as i32,
            height: template.height() as i32,
        }),
        message: if matched {
            format!("模板命中，score={score:.3}")
        } else {
            format!("模板未命中，score={score:.3}，threshold={threshold:.3}")
        },
    })
}

fn clamp_region(region: Option<MatchBox>, max_width: i32, max_height: i32) -> Option<MatchBox> {
    let (x, y, width, height) = region?;
    if width <= 0 || height <= 0 {
        return None;
    }
    let left = x.clamp(0, max_width.saturating_sub(1));
    let top = y.clamp(0, max_height.saturating_sub(1));
    let right = (x + width).clamp(left + 1, max_width);
    let bottom = (y + height).clamp(top + 1, max_height);
    Some((left, top, right - left, bottom - top))
}

fn probe_template_with_helper(
    image: &RgbaImage,
    template_path: &Path,
    threshold: f64,
    region: Option<MatchBox>,
) -> Result<TemplateProbeResult> {
    let cached = load_cached_template(template_path)?;
    let gray = rgba_to_luma8(image);
    let (offset_x, offset_y, haystack) = crop_for_helper(&gray, region)?;

    if haystack.width() < cached.width as u32 || haystack.height() < cached.height as u32 {
        return Ok(TemplateProbeResult {
            matched: false,
            confidence: 0.0,
            box_rect: None,
            message: "目标区域小于模板尺寸".to_string(),
        });
    }

    let image_view = ImageView::from_slice(
        haystack.as_raw(),
        haystack.width() as usize,
        haystack.height() as usize,
    )
    .map_err(|error| anyhow::anyhow!("corrmatch image view failed: {error}"))?;

    let matched = match cached.matcher.match_image(image_view) {
        Ok(result) => result,
        Err(CorrMatchError::NoCandidates { .. }) => {
            return Ok(TemplateProbeResult {
                matched: false,
                confidence: 0.0,
                box_rect: None,
                message: "模板未命中，corrmatch 无候选".to_string(),
            });
        }
        Err(error) => return Err(anyhow::anyhow!("corrmatch match failed: {error}")),
    };

    let score = f64::from(matched.score);
    let matched_flag = score >= threshold;
    let px = matched.x.round() as i32 + offset_x;
    let py = matched.y.round() as i32 + offset_y;

    Ok(TemplateProbeResult {
        matched: matched_flag,
        confidence: score,
        box_rect: Some(CaptureRegion {
            x: px,
            y: py,
            width: cached.width,
            height: cached.height,
        }),
        message: if matched_flag {
            format!("模板命中，score={score:.3}")
        } else {
            format!("模板未命中，score={score:.3}，threshold={threshold:.3}")
        },
    })
}

fn crop_for_helper(image: &GrayImage, region: Option<MatchBox>) -> Result<(i32, i32, GrayImage)> {
    let (x, y, width, height) = clamp_region(region, image.width() as i32, image.height() as i32)
        .unwrap_or((0, 0, image.width() as i32, image.height() as i32));
    let cropped =
        imageops::crop_imm(image, x as u32, y as u32, width as u32, height as u32).to_image();
    Ok((x, y, cropped))
}

fn load_cached_template(path: &Path) -> Result<CachedTemplateMatcher> {
    let key = path.to_string_lossy().to_string();
    let cache = TEMPLATE_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    if let Some(entry) = cache.lock().ok().and_then(|items| items.get(&key).cloned()) {
        return Ok(entry);
    }

    let template = load_template_gray(path)?;
    let width = template.width() as i32;
    let height = template.height() as i32;
    let compiled = build_corrmatch_template(&template)?;
    let matcher = Arc::new(
        Matcher::new(compiled).with_config(MatchConfig {
            metric: Metric::Zncc,
            rotation: RotationMode::Disabled,
            parallel: true,
            max_image_levels: CORRMATCH_MAX_LEVELS,
            beam_width: CORRMATCH_BEAM_WIDTH,
            per_angle_topk: CORRMATCH_PER_ANGLE_TOPK,
            roi_radius: CORRMATCH_ROI_RADIUS,
            nms_radius: CORRMATCH_NMS_RADIUS,
            min_score: f32::NEG_INFINITY,
            ..MatchConfig::default()
        }),
    );
    let entry = CachedTemplateMatcher {
        matcher,
        width,
        height,
    };

    if let Ok(mut items) = cache.lock() {
        items.insert(key, entry.clone());
    }
    Ok(entry)
}

fn build_corrmatch_template(template: &GrayImage) -> Result<corrmatch::CompiledTemplate> {
    let owned = Template::new(
        template.as_raw().clone(),
        template.width() as usize,
        template.height() as usize,
    )
    .map_err(|error| anyhow::anyhow!("corrmatch template build failed: {error}"))?;
    CompiledTemplate::compile_unrotated(&owned, CompileConfigNoRot {
        max_levels: CORRMATCH_MAX_LEVELS,
    })
    .map_err(|error| anyhow::anyhow!("corrmatch template compile failed: {error}"))
}

fn rgba_to_luma8(image: &RgbaImage) -> GrayImage {
    image::DynamicImage::ImageRgba8(image.clone()).to_luma8()
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use image::{GrayImage, Luma, Rgba, RgbaImage};

    use super::{
        load_template_gray, locate_template_in_image, probe_gray_template_in_image,
        probe_template_in_image_fast, validate_template_file,
    };

    fn sample_template() -> GrayImage {
        let mut image = GrayImage::new(2, 2);
        image.put_pixel(0, 0, Luma([0]));
        image.put_pixel(1, 0, Luma([255]));
        image.put_pixel(0, 1, Luma([255]));
        image.put_pixel(1, 1, Luma([0]));
        image
    }

    fn sample_image() -> GrayImage {
        let mut image = GrayImage::from_pixel(6, 6, Luma([32]));
        let template = sample_template();
        for y in 0..2 {
            for x in 0..2 {
                image.put_pixel(x + 3, y + 2, *template.get_pixel(x, y));
            }
        }
        image
    }

    #[test]
    fn validates_missing_template_file() {
        let result = validate_template_file(PathBuf::from("Z:/definitely/missing/template.png").as_path())
            .expect("validation should not fail");
        assert!(!result.valid);
        assert!(result.message.contains("does not exist"));
    }

    #[test]
    fn probes_gray_template_hit() {
        let result = probe_gray_template_in_image(&sample_image(), &sample_template(), 0.9, None)
            .expect("probe should succeed");
        assert!(result.matched);
        assert!(result.confidence >= 0.9);
        let rect = result.box_rect.expect("match box should exist");
        assert_eq!((rect.x, rect.y, rect.width, rect.height), (3, 2, 2, 2));
    }

    #[test]
    fn probes_gray_template_miss_with_high_threshold() {
        let result =
            probe_gray_template_in_image(&sample_image(), &sample_template(), 1.1, None)
                .expect("probe should succeed");
        assert!(!result.matched);
        assert!(result.message.contains("threshold"));
    }

    #[test]
    fn locates_template_inside_region() {
        let mut rgba = RgbaImage::from_pixel(6, 6, Rgba([32, 32, 32, 255]));
        let template = sample_template();
        for y in 0..2 {
            for x in 0..2 {
                let value = template.get_pixel(x, y).0[0];
                rgba.put_pixel(x + 3, y + 2, Rgba([value, value, value, 255]));
            }
        }
        let temp_dir = std::env::temp_dir();
        let path = temp_dir.join(format!("arena-template-{}.png", std::process::id()));
        image::DynamicImage::ImageLuma8(template.clone())
            .save(&path)
            .expect("template save should succeed");

        let located = locate_template_in_image(&rgba, &path, 0.9, Some((2, 1, 3, 3)))
            .expect("locate should succeed");
        assert_eq!(located, Some((3, 2, 2, 2)));

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn loads_template_gray_from_png() {
        let temp_dir = std::env::temp_dir();
        let path = temp_dir.join(format!("arena-load-template-{}.png", std::process::id()));
        image::DynamicImage::ImageLuma8(sample_template())
            .save(&path)
            .expect("template save should succeed");
        let loaded = load_template_gray(&path).expect("template should load");
        assert_eq!(loaded.width(), 2);
        assert_eq!(loaded.height(), 2);
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn fast_probe_finds_template_inside_rgba_image() {
        let mut rgba = RgbaImage::from_pixel(6, 6, Rgba([32, 32, 32, 255]));
        let template = sample_template();
        for y in 0..2 {
            for x in 0..2 {
                let value = template.get_pixel(x, y).0[0];
                rgba.put_pixel(x + 3, y + 2, Rgba([value, value, value, 255]));
            }
        }
        let temp_dir = std::env::temp_dir();
        let path = temp_dir.join(format!("arena-fast-template-{}.png", std::process::id()));
        image::DynamicImage::ImageLuma8(template.clone())
            .save(&path)
            .expect("template save should succeed");

        let result = probe_template_in_image_fast(&rgba, &path, 0.9, None)
            .expect("fast probe should succeed");
        let rect = result.box_rect.expect("match box should exist");
        assert_eq!((rect.x, rect.y, rect.width, rect.height), (3, 2, 2, 2));

        let _ = std::fs::remove_file(&path);
    }
}
