use std::env;
use std::path::PathBuf;
use std::time::Instant;

use anyhow::{Context, Result, anyhow};
use corrmatch::{
    CompileConfigNoRot, CompiledTemplate, ImageView, MatchConfig, Matcher, Metric, RotationMode,
    Template,
};
use serde_json::json;

mod automation {
    #[path = "../../automation/capture.rs"]
    pub mod capture;
    #[path = "../../automation/vision.rs"]
    pub mod vision;
}

use automation::capture::capture_full_screen;
use automation::vision::probe_template_in_image_fast;

fn main() -> Result<()> {
    let mut args = env::args().skip(1);
    let mode = args
        .next()
        .ok_or_else(|| anyhow!("usage: template_bench <fast-screen|fast-image|corrmatch-screen|corrmatch-image> <template_path> [image_path] [rounds] [threshold]"))?;

    match mode.as_str() {
        "fast-screen" => {
            let template_path = PathBuf::from(
                args.next()
                    .ok_or_else(|| anyhow!("fast-screen mode requires <template_path>"))?,
            );
            let rounds = args
                .next()
                .as_deref()
                .unwrap_or("10")
                .parse::<usize>()
                .context("invalid rounds")?;
            let threshold = args
                .next()
                .as_deref()
                .unwrap_or("0.85")
                .parse::<f64>()
                .context("invalid threshold")?;
            bench_screen("fast", &template_path, rounds, threshold)?;
        }
        "fast-image" => {
            let template_path = PathBuf::from(
                args.next()
                    .ok_or_else(|| anyhow!("fast-image mode requires <template_path>"))?,
            );
            let image_path = PathBuf::from(
                args.next()
                    .ok_or_else(|| anyhow!("fast-image mode requires <image_path>"))?,
            );
            let rounds = args
                .next()
                .as_deref()
                .unwrap_or("30")
                .parse::<usize>()
                .context("invalid rounds")?;
            let threshold = args
                .next()
                .as_deref()
                .unwrap_or("0.85")
                .parse::<f64>()
                .context("invalid threshold")?;
            bench_image("fast", &template_path, &image_path, rounds, threshold)?;
        }
        "corrmatch-screen" => {
            let template_path = PathBuf::from(
                args.next()
                    .ok_or_else(|| anyhow!("corrmatch-screen mode requires <template_path>"))?,
            );
            let rounds = args
                .next()
                .as_deref()
                .unwrap_or("10")
                .parse::<usize>()
                .context("invalid rounds")?;
            let threshold = args
                .next()
                .as_deref()
                .unwrap_or("0.85")
                .parse::<f64>()
                .context("invalid threshold")?;
            bench_screen("corrmatch", &template_path, rounds, threshold)?;
        }
        "corrmatch-image" => {
            let template_path = PathBuf::from(
                args.next()
                    .ok_or_else(|| anyhow!("corrmatch-image mode requires <template_path>"))?,
            );
            let image_path = PathBuf::from(
                args.next()
                    .ok_or_else(|| anyhow!("corrmatch-image mode requires <image_path>"))?,
            );
            let rounds = args
                .next()
                .as_deref()
                .unwrap_or("30")
                .parse::<usize>()
                .context("invalid rounds")?;
            let threshold = args
                .next()
                .as_deref()
                .unwrap_or("0.85")
                .parse::<f64>()
                .context("invalid threshold")?;
            bench_image("corrmatch", &template_path, &image_path, rounds, threshold)?;
        }
        _ => return Err(anyhow!("unknown mode: {mode}")),
    }

    Ok(())
}

fn bench_screen(engine: &str, template_path: &PathBuf, rounds: usize, threshold: f64) -> Result<()> {
    let mut capture_ms = Vec::with_capacity(rounds);
    let mut match_ms = Vec::with_capacity(rounds);
    let mut total_ms = Vec::with_capacity(rounds);
    let mut matched = 0usize;
    let corrmatch = if engine == "corrmatch" {
        Some(prepare_corrmatch(template_path, threshold)?)
    } else {
        None
    };

    for _ in 0..rounds {
        let started = Instant::now();
        let capture_started = Instant::now();
        let captured = capture_full_screen()?;
        capture_ms.push(capture_started.elapsed().as_secs_f64() * 1000.0);

        let match_started = Instant::now();
        let result = if let Some((matcher, tpl_w, tpl_h)) = corrmatch.as_ref() {
            corrmatch_match(&captured.image, matcher, *tpl_w, *tpl_h, threshold)?
        } else {
            probe_template_in_image_fast(&captured.image, template_path, threshold, None)?
        };
        match_ms.push(match_started.elapsed().as_secs_f64() * 1000.0);
        total_ms.push(started.elapsed().as_secs_f64() * 1000.0);

        if result.matched {
            matched += 1;
        }
    }

    println!(
        "{}",
        json!({
            "engine": engine,
            "mode": "screen",
            "template": template_path,
            "rounds": rounds,
            "threshold": threshold,
            "matched_count": matched,
            "capture_ms_avg": average(&capture_ms),
            "match_ms_avg": average(&match_ms),
            "total_ms_avg": average(&total_ms),
            "total_ms_min": min(&total_ms),
            "total_ms_max": max(&total_ms),
        })
    );
    Ok(())
}

fn bench_image(engine: &str, template_path: &PathBuf, image_path: &PathBuf, rounds: usize, threshold: f64) -> Result<()> {
    let image = image::open(image_path)
        .with_context(|| format!("failed to open {}", image_path.display()))?
        .to_rgba8();
    let mut match_ms = Vec::with_capacity(rounds);
    let mut matched = 0usize;
    let corrmatch = if engine == "corrmatch" {
        Some(prepare_corrmatch(template_path, threshold)?)
    } else {
        None
    };

    for _ in 0..rounds {
        let started = Instant::now();
        let result = if let Some((matcher, tpl_w, tpl_h)) = corrmatch.as_ref() {
            corrmatch_match(&image, matcher, *tpl_w, *tpl_h, threshold)?
        } else {
            probe_template_in_image_fast(&image, template_path, threshold, None)?
        };
        match_ms.push(started.elapsed().as_secs_f64() * 1000.0);
        if result.matched {
            matched += 1;
        }
    }

    println!(
        "{}",
        json!({
            "engine": engine,
            "mode": "image",
            "template": template_path,
            "image": image_path,
            "rounds": rounds,
            "threshold": threshold,
            "matched_count": matched,
            "match_ms_avg": average(&match_ms),
            "match_ms_min": min(&match_ms),
            "match_ms_max": max(&match_ms),
        })
    );
    Ok(())
}

fn prepare_corrmatch(template_path: &PathBuf, _threshold: f64) -> Result<(Matcher, i32, i32)> {
    let template = image::open(template_path)
        .with_context(|| format!("failed to open {}", template_path.display()))?
        .to_luma8();
    let template_width = template.width() as i32;
    let template_height = template.height() as i32;
    let owned = Template::new(
        template.into_raw(),
        template_width as usize,
        template_height as usize,
    )
    .map_err(|error| anyhow!("corrmatch template build failed: {error}"))?;
    let compiled = CompiledTemplate::compile_unrotated(
        &owned,
        CompileConfigNoRot { max_levels: 4 },
    )
        .map_err(|error| anyhow!("corrmatch template compile failed: {error}"))?;
    let matcher = Matcher::new(compiled).with_config(MatchConfig {
        metric: Metric::Zncc,
        rotation: RotationMode::Disabled,
        parallel: true,
        max_image_levels: 4,
        beam_width: 6,
        per_angle_topk: 3,
        roi_radius: 6,
        nms_radius: 4,
        min_score: f32::NEG_INFINITY,
        ..MatchConfig::default()
    });
    Ok((matcher, template_width, template_height))
}

fn corrmatch_match(
    image: &image::RgbaImage,
    matcher: &Matcher,
    template_width: i32,
    template_height: i32,
    threshold: f64,
) -> Result<automation::vision::TemplateProbeResult> {
    let gray = image::DynamicImage::ImageRgba8(image.clone()).to_luma8();
    let view = ImageView::from_slice(
        gray.as_raw(),
        gray.width() as usize,
        gray.height() as usize,
    )
    .map_err(|error| anyhow!("corrmatch image view failed: {error}"))?;
    let matched = matcher
        .match_image(view)
        .map_err(|error| anyhow!("corrmatch match failed: {error}"))?;
    let hit = f64::from(matched.score) >= threshold;
    Ok(automation::vision::TemplateProbeResult {
        matched: hit,
        confidence: f64::from(matched.score),
        box_rect: if hit {
            Some(automation::capture::CaptureRegion {
                x: matched.x.round() as i32,
                y: matched.y.round() as i32,
                width: template_width,
                height: template_height,
            })
        } else {
            None
        },
        message: if hit {
            format!("corrmatch matched, score={:.3}", matched.score)
        } else {
            format!("corrmatch missed, score={:.3}, threshold={threshold:.3}", matched.score)
        },
    })
}

fn average(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values.iter().sum::<f64>() / values.len() as f64
}

fn min(values: &[f64]) -> f64 {
    values
        .iter()
        .copied()
        .reduce(f64::min)
        .unwrap_or(0.0)
}

fn max(values: &[f64]) -> f64 {
    values
        .iter()
        .copied()
        .reduce(f64::max)
        .unwrap_or(0.0)
}
