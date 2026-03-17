use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use tauri::{AppHandle, Manager, path::BaseDirectory};

use crate::app::types::PathsSnapshot;

#[derive(Debug, Clone)]
pub struct AppPaths {
    pub root_dir: PathBuf,
    pub data_dir: PathBuf,
    pub images_dir: PathBuf,
    pub assets_dir: PathBuf,
    pub debug_dir: PathBuf,
    pub logs_dir: PathBuf,
    pub cache_dir: PathBuf,
    pub db_path: PathBuf,
    pub bundled_umi_dir: Option<PathBuf>,
    pub bundled_resources_dir: Option<PathBuf>,
}

impl AppPaths {
    pub fn resolve(app: &AppHandle) -> Result<Self> {
        let root_dir = match env::var("ARENA_BUYER_PORTABLE_ROOT") {
            Ok(path) if !path.trim().is_empty() => PathBuf::from(path),
            _ => env::current_exe()
                .context("failed to resolve current executable path")?
                .parent()
                .map(Path::to_path_buf)
                .context("executable has no parent directory")?,
        };
        let data_dir = root_dir.join("data");
        let images_dir = data_dir.join("images");
        let assets_dir = data_dir.join("assets");
        let debug_dir = data_dir.join("debug");
        let logs_dir = data_dir.join("logs");
        let cache_dir = data_dir.join("cache");
        let db_path = data_dir.join("app.db");

        for dir in [
            &data_dir,
            &images_dir,
            &assets_dir,
            &debug_dir,
            &logs_dir,
            &cache_dir,
        ] {
            fs::create_dir_all(dir)
                .with_context(|| format!("failed to create {}", dir.display()))?;
        }

        let bundled_resources_dir = first_existing_dir([
            app.path()
                .resolve("app_resources", BaseDirectory::Resource)
                .ok(),
            Some(root_dir.join("app_resources")),
            Some(root_dir.join("legacy_resources")),
            Some(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../resources/app")),
        ]);
        let bundled_umi_dir = first_existing_dir([
            app.path()
                .resolve("sidecars/Umi-OCR_Paddle_v2.1.5", BaseDirectory::Resource)
                .ok(),
            Some(root_dir.join("sidecars").join("Umi-OCR_Paddle_v2.1.5")),
            Some(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../Umi-OCR_Paddle_v2.1.5")),
        ]);

        Ok(Self {
            root_dir,
            data_dir,
            images_dir,
            assets_dir,
            debug_dir,
            logs_dir,
            cache_dir,
            db_path,
            bundled_umi_dir,
            bundled_resources_dir,
        })
    }

    pub fn resolve_data_path(&self, raw: &str) -> PathBuf {
        let raw = raw.trim();
        if raw.is_empty() {
            return self.data_dir.clone();
        }
        let path = PathBuf::from(raw);
        if path.is_absolute() {
            return path;
        }
        self.data_dir.join(path)
    }

    pub fn snapshot(&self) -> PathsSnapshot {
        PathsSnapshot {
            root_dir: self.root_dir.display().to_string(),
            data_dir: self.data_dir.display().to_string(),
            images_dir: self.images_dir.display().to_string(),
            assets_dir: self.assets_dir.display().to_string(),
            debug_dir: self.debug_dir.display().to_string(),
            logs_dir: self.logs_dir.display().to_string(),
            cache_dir: self.cache_dir.display().to_string(),
            db_path: self.db_path.display().to_string(),
            bundled_umi_dir: self
                .bundled_umi_dir
                .as_ref()
                .map(|path| path.display().to_string()),
            bundled_resources_dir: self
                .bundled_resources_dir
                .as_ref()
                .map(|path| path.display().to_string()),
        }
    }
}

fn first_existing_dir<const N: usize>(candidates: [Option<PathBuf>; N]) -> Option<PathBuf> {
    candidates.into_iter().flatten().find(|path| path.exists())
}
