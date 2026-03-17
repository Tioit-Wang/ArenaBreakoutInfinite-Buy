use anyhow::Result;
use image::{GrayImage, RgbaImage, imageops::FilterType};

use crate::automation::capture::CaptureRegion;
use crate::automation::vision::MatchBox;
use crate::app::types::AppConfig;

pub const CARD_WIDTH: i32 = 165;
pub const CARD_HEIGHT: i32 = 212;
pub const CARD_TOP_HEIGHT: i32 = 20;
pub const CARD_BOTTOM_HEIGHT: i32 = 30;
pub const CARD_MARGIN_LR: i32 = 30;
pub const CARD_MARGIN_TB: i32 = 20;
pub const CARD_INNER_WIDTH: i32 = CARD_WIDTH - (CARD_MARGIN_LR * 2);
pub const CARD_INNER_HEIGHT: i32 =
    CARD_HEIGHT - CARD_TOP_HEIGHT - CARD_BOTTOM_HEIGHT - (CARD_MARGIN_TB * 2);

pub fn parse_price_text(text: &str) -> Option<i64> {
    if text.trim().is_empty() {
        return None;
    }
    let source = text.trim().to_uppercase().replace(',', "").replace(' ', "");
    let mut numeric = String::new();
    let mut suffix = None;
    for ch in source.chars() {
        if ch.is_ascii_digit() || ch == '.' {
            numeric.push(ch);
        } else if matches!(ch, 'K' | 'M') {
            suffix = Some(ch);
            break;
        }
    }
    if numeric.is_empty() {
        let digits: String = source.chars().filter(|ch| ch.is_ascii_digit()).collect();
        return digits.parse::<i64>().ok();
    }
    let mut value = numeric.parse::<f64>().ok()?;
    match suffix {
        Some('K') => value *= 1_000.0,
        Some('M') => value *= 1_000_000.0,
        _ => {}
    }
    Some(value.round() as i64)
}

pub fn price_with_premium(price: i64, premium_pct: f64) -> i64 {
    let factor = 1.0 + (premium_pct / 100.0);
    ((price as f64) * factor).round() as i64
}

pub fn infer_card_from_goods_match(mid: MatchBox) -> MatchBox {
    let (x, y, width, height) = mid;
    let card_width = ((width as f64) * (CARD_WIDTH as f64 / CARD_INNER_WIDTH as f64)).round() as i32;
    let card_height =
        ((height as f64) * (CARD_HEIGHT as f64 / CARD_INNER_HEIGHT as f64)).round() as i32;
    let left_offset =
        ((width as f64) * (CARD_MARGIN_LR as f64 / CARD_INNER_WIDTH as f64)).round() as i32;
    let top_offset = ((height as f64)
        * ((CARD_TOP_HEIGHT + CARD_MARGIN_TB) as f64 / CARD_INNER_HEIGHT as f64))
        .round() as i32;
    (
        x - left_offset,
        y - top_offset,
        card_width.max(width),
        card_height.max(height),
    )
}

pub fn top_roi_from_card(card: MatchBox) -> MatchBox {
    (card.0, card.1, card.2, CARD_TOP_HEIGHT.min(card.3).max(1))
}

pub fn bottom_roi_from_card(card: MatchBox) -> MatchBox {
    let height = CARD_BOTTOM_HEIGHT.min(card.3).max(1);
    (card.0, card.1 + card.3 - height, card.2, height)
}

pub fn goods_inner_region_from_outer(outer: &CaptureRegion) -> CaptureRegion {
    CaptureRegion {
        // 保持与 Python 版一致：从卡片左上角按固定像素偏移裁中间商品图，
        // 不随外框实际宽高按比例缩放。
        x: outer.x + CARD_MARGIN_LR,
        y: outer.y + CARD_TOP_HEIGHT + CARD_MARGIN_TB,
        width: CARD_INNER_WIDTH,
        height: CARD_INNER_HEIGHT,
    }
}

pub fn crop_gray(image: &RgbaImage, rect: MatchBox) -> Result<GrayImage> {
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

pub fn resize(image: GrayImage, scale: f64) -> GrayImage {
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

pub fn threshold(mut image: GrayImage) -> GrayImage {
    for pixel in image.pixels_mut() {
        pixel.0[0] = if pixel.0[0] > 128 { 255 } else { 0 };
    }
    image
}

pub fn top_half(image: GrayImage) -> GrayImage {
    image::imageops::crop_imm(&image, 0, 0, image.width(), (image.height() / 2).max(1)).to_image()
}

pub fn avg_price_roi(
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

pub fn infer_qty_from_max(max_box: MatchBox) -> MatchBox {
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

pub fn parse_digits(text: &str) -> Option<i64> {
    let digits: String = text.chars().filter(|ch| ch.is_ascii_digit()).collect();
    digits.parse::<i64>().ok()
}

#[cfg(test)]
mod tests {
    use image::{GrayImage, Luma, Rgba, RgbaImage};

    use super::{
        avg_price_roi, bottom_roi_from_card, crop_gray, goods_inner_region_from_outer,
        infer_card_from_goods_match, infer_qty_from_max, parse_digits, parse_price_text,
        price_with_premium, resize, threshold, top_half, top_roi_from_card,
    };
    use crate::automation::capture::CaptureRegion;
    use crate::app::types::AppConfig;

    #[test]
    fn parses_price_suffixes() {
        assert_eq!(parse_price_text("12.5k"), Some(12_500));
        assert_eq!(parse_price_text("1.2M"), Some(1_200_000));
        assert_eq!(parse_price_text("12,345"), Some(12_345));
    }

    #[test]
    fn computes_premium_threshold() {
        assert_eq!(price_with_premium(10_000, 2.0), 10_200);
    }

    #[test]
    fn infers_card_from_goods_match() {
        assert_eq!(infer_card_from_goods_match((100, 120, 105, 122)), (70, 80, 165, 212));
    }

    #[test]
    fn maps_goods_inner_region_from_outer_using_python_fixed_offsets() {
        let outer = CaptureRegion {
            x: 10,
            y: 20,
            width: 206,
            height: 265,
        };
        let inner = goods_inner_region_from_outer(&outer);
        assert_eq!(inner.x, 40);
        assert_eq!(inner.y, 60);
        assert_eq!(inner.width, 105);
        assert_eq!(inner.height, 122);
    }

    #[test]
    fn computes_top_and_bottom_rois() {
        let card = (10, 20, 165, 212);
        assert_eq!(top_roi_from_card(card), (10, 20, 165, 20));
        assert_eq!(bottom_roi_from_card(card), (10, 202, 165, 30));
    }

    #[test]
    fn adjusts_avg_price_roi_for_exchangeables() {
        let config = AppConfig::default();
        let normal = avg_price_roi((100, 200, 80, 40), false, &config, 500, 500);
        let exchangeable = avg_price_roi((100, 200, 80, 40), true, &config, 500, 500);
        assert!(exchangeable.1 < normal.1);
        assert_eq!(normal.2, 80);
    }

    #[test]
    fn infers_quantity_box_from_max_button() {
        let inferred = infer_qty_from_max((300, 400, 40, 30));
        assert!(inferred.0 < 300);
        assert!(inferred.2 >= 80);
        assert!(inferred.3 >= 28);
    }

    #[test]
    fn parses_digits_only_strings() {
        assert_eq!(parse_digits("x12y34"), Some(1234));
        assert_eq!(parse_digits("none"), None);
    }

    #[test]
    fn crops_gray_and_resizes_and_thresholds() {
        let mut image = RgbaImage::new(4, 4);
        for y in 0..4 {
            for x in 0..4 {
                let value = if x < 2 { 40 } else { 220 };
                image.put_pixel(x, y, Rgba([value, value, value, 255]));
            }
        }
        let cropped = crop_gray(&image, (1, 1, 2, 2)).expect("crop should succeed");
        assert_eq!(cropped.width(), 2);
        assert_eq!(cropped.height(), 2);

        let resized = resize(cropped.clone(), 2.0);
        assert_eq!(resized.width(), 4);
        assert_eq!(resized.height(), 4);

        let thresholded = threshold(cropped);
        let values = thresholded.pixels().map(|px| px.0[0]).collect::<Vec<_>>();
        assert!(values.iter().all(|value| *value == 0 || *value == 255));
    }

    #[test]
    fn slices_top_half() {
        let mut image = GrayImage::new(4, 6);
        for y in 0..6 {
            for x in 0..4 {
                image.put_pixel(x, y, Luma([y as u8]));
            }
        }
        let half = top_half(image);
        assert_eq!(half.width(), 4);
        assert_eq!(half.height(), 3);
        assert_eq!(half.get_pixel(0, 2).0[0], 2);
    }
}
