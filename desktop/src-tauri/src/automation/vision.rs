use std::path::Path;

use anyhow::{Context, Result, bail};
use image::{GrayImage, RgbaImage, imageops};
use imageproc::template_matching::{MatchTemplateMethod, find_extremes, match_template};
use serde::{Deserialize, Serialize};

pub type MatchBox = (i32, i32, i32, i32);

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TemplateMatchResult {
    pub matched: bool,
    pub confidence: f64,
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
    let template = load_template_gray(template_path)?;
    locate_gray_template_in_image(
        &image::DynamicImage::ImageRgba8(image.clone()).to_luma8(),
        &template,
        confidence,
        region,
    )
}

pub fn locate_gray_template_in_image(
    image: &GrayImage,
    template: &GrayImage,
    confidence: f64,
    region: Option<MatchBox>,
) -> Result<Option<MatchBox>> {
    let (x, y, width, height) = clamp_region(region, image.width() as i32, image.height() as i32)
        .unwrap_or((0, 0, image.width() as i32, image.height() as i32));
    if width < template.width() as i32 || height < template.height() as i32 {
        return Ok(None);
    }
    let cropped =
        imageops::crop_imm(image, x as u32, y as u32, width as u32, height as u32).to_image();
    if cropped.width() < template.width() || cropped.height() < template.height() {
        return Ok(None);
    }
    let response = match_template(
        &cropped,
        template,
        MatchTemplateMethod::CrossCorrelationNormalized,
    );
    let extremes = find_extremes(&response);
    let score = extremes.max_value as f64;
    if !score.is_finite() || score < confidence {
        return Ok(None);
    }
    let px = x + extremes.max_value_location.0 as i32;
    let py = y + extremes.max_value_location.1 as i32;
    Ok(Some((
        px,
        py,
        template.width() as i32,
        template.height() as i32,
    )))
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
