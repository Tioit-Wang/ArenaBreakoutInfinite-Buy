use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use anyhow::{Context, Result};
use tauri::AppHandle;
use walkdir::WalkDir;

use crate::app::types::{AppConfig, GoodsRecord, TemplateConfig, now_iso};
use crate::config::defaults::{default_config, default_templates};
use crate::config::paths::AppPaths;
use crate::storage::repository::Repository;

#[derive(Clone)]
pub struct ConfigService {
    paths: Arc<AppPaths>,
    repo: Arc<Repository>,
}

const CATEGORY_SLUGS: &[(&str, &str)] = &[
    ("装备", "equipment"),
    ("武器配件", "weapon_parts"),
    ("武器枪机", "firearms"),
    ("弹药", "ammo"),
    ("医疗用品", "medical"),
    ("战术道具", "tactical"),
    ("钥匙", "keys"),
    ("杂物", "misc"),
    ("饮食", "food"),
];

impl ConfigService {
    pub fn new(paths: Arc<AppPaths>, repo: Arc<Repository>) -> Self {
        Self { paths, repo }
    }

    pub fn ensure_seeded(&self, app: &AppHandle) -> Result<()> {
        self.copy_bundled_images()?;
        self.copy_bundled_assets()?;

        if self.repo.get_config()?.is_none() {
            self.repo.save_config(&default_config())?;
        }

        if self.repo.list_templates()?.is_empty() {
            for template in default_templates() {
                self.repo.upsert_template(&template)?;
            }
        }

        if self.repo.list_goods()?.is_empty() {
            for goods in self.load_default_goods(app)? {
                self.repo.save_goods(&goods)?;
            }
        }

        self.migrate_resource_paths()?;
        self.repair_template_images()?;

        Ok(())
    }

    pub fn get(&self) -> Result<AppConfig> {
        Ok(self.repo.get_config()?.unwrap_or_else(default_config))
    }

    pub fn save(&self, config: &AppConfig) -> Result<AppConfig> {
        self.repo.save_config(config)
    }

    pub fn list_templates(&self) -> Result<Vec<TemplateConfig>> {
        self.repo.list_templates()
    }

    pub fn upsert_template(&self, template: &TemplateConfig) -> Result<TemplateConfig> {
        self.repo.upsert_template(template)
    }

    fn migrate_resource_paths(&self) -> Result<()> {
        for mut template in self.repo.list_templates()? {
            let normalized = self.normalize_template_path(&template.path);
            if normalized != template.path {
                template.path = normalized;
                template.updated_at = now_iso();
                self.repo.upsert_template(&template)?;
            }
        }

        for mut goods in self.repo.list_goods()? {
            let normalized = goods.image_path.replace('\\', "/");
            if normalized != goods.image_path {
                goods.image_path = normalized;
                goods.updated_at = now_iso();
                self.repo.save_goods(&goods)?;
            }
        }

        Ok(())
    }

    fn copy_bundled_images(&self) -> Result<()> {
        let Some(base) = &self.paths.bundled_resources_dir else {
            return Ok(());
        };
        let source = base.join("images");
        if !source.exists() {
            return Ok(());
        }
        for entry in WalkDir::new(&source).into_iter().filter_map(Result::ok) {
            if !entry.file_type().is_file() {
                continue;
            }
            let src = entry.path();
            let rel = src.strip_prefix(&source).unwrap_or(src);
            let Some(mapped_rel) = self.map_bundled_image_relative(rel) else {
                continue;
            };
            let dest = self.paths.images_dir.join(mapped_rel);
            if dest.exists() {
                continue;
            }
            if let Some(parent) = dest.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::copy(src, &dest).with_context(|| {
                format!(
                    "failed to copy bundled image {} to {}",
                    src.display(),
                    dest.display()
                )
            })?;
        }
        Ok(())
    }

    fn map_bundled_image_relative(&self, rel: &Path) -> Option<PathBuf> {
        let rel = PathBuf::from(rel);
        let file_name = rel.file_name()?.to_string_lossy();
        if file_name.eq_ignore_ascii_case("__init__.py") {
            return None;
        }
        let normalized = rel.to_string_lossy().replace('\\', "/");
        if normalized.starts_with("goods/") || normalized == "goods" {
            return Some(rel);
        }
        if normalized.starts_with("templates/") || normalized == "templates" {
            return Some(rel);
        }
        Some(PathBuf::from("templates").join(rel))
    }

    fn copy_bundled_assets(&self) -> Result<()> {
        let Some(base) = &self.paths.bundled_resources_dir else {
            return Ok(());
        };
        let source = base.join("assets");
        if !source.exists() {
            return Ok(());
        }
        for entry in WalkDir::new(&source).into_iter().filter_map(Result::ok) {
            if !entry.file_type().is_file() {
                continue;
            }
            let src = entry.path();
            let rel = src.strip_prefix(&source).unwrap_or(src);
            let dest = self.paths.assets_dir.join(rel);
            if dest.exists() {
                continue;
            }
            if let Some(parent) = dest.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::copy(src, &dest).with_context(|| {
                format!(
                    "failed to copy bundled asset {} to {}",
                    src.display(),
                    dest.display()
                )
            })?;
        }
        Ok(())
    }

    fn load_default_goods(&self, _app: &AppHandle) -> Result<Vec<GoodsRecord>> {
        let Some(base) = &self.paths.bundled_resources_dir else {
            return Ok(Vec::new());
        };
        let goods_path = base.join("defaults").join("goods.json");
        if !goods_path.exists() {
            return Ok(Vec::new());
        }
        let raw = fs::read_to_string(&goods_path)
            .with_context(|| format!("failed to read {}", goods_path.display()))?;
        let value: serde_json::Value = serde_json::from_str(&raw)?;
        let items = value.as_array().cloned().unwrap_or_default();
        let now = now_iso();
        Ok(items
            .into_iter()
            .filter_map(|item| {
                let id = item.get("id")?.as_str()?.to_string();
                let image_path = item
                    .get("image_path")
                    .and_then(|v| v.as_str())
                    .unwrap_or("images/goods/_default.png")
                    .replace('\\', "/");
                Some(GoodsRecord {
                    id,
                    name: item
                        .get("name")
                        .and_then(|v| v.as_str())
                        .unwrap_or_default()
                        .to_string(),
                    search_name: item
                        .get("search_name")
                        .and_then(|v| v.as_str())
                        .unwrap_or_default()
                        .to_string(),
                    big_category: item
                        .get("big_category")
                        .and_then(|v| v.as_str())
                        .unwrap_or_default()
                        .to_string(),
                    sub_category: item
                        .get("sub_category")
                        .and_then(|v| v.as_str())
                        .unwrap_or_default()
                        .to_string(),
                    exchangeable: item
                        .get("exchangeable")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false),
                    craftable: item
                        .get("craftable")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false),
                    favorite: item
                        .get("favorite")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false),
                    image_path,
                    price: item.get("price").and_then(|v| v.as_i64()),
                    created_at: now.clone(),
                    updated_at: now.clone(),
                })
            })
            .collect())
    }

    fn normalize_template_path(&self, path: &str) -> String {
        let normalized = path.replace('\\', "/");
        if normalized.starts_with("images/templates/") || normalized.starts_with("images/goods/") {
            return normalized;
        }
        if let Some(file_name) = normalized.strip_prefix("images/") {
            if !file_name.contains('/') {
                return format!("images/templates/{file_name}");
            }
        }
        normalized
    }

    fn repair_template_images(&self) -> Result<()> {
        let template_dir = self.paths.images_dir.join("templates");
        fs::create_dir_all(&template_dir)
            .with_context(|| format!("failed to create {}", template_dir.display()))?;

        for template in self.repo.list_templates()? {
            let target = self.resolve_template_absolute_path(&template.path);
            if target.exists() {
                continue;
            }

            let Some(file_name) = target.file_name() else {
                continue;
            };
            let legacy_flat = self.paths.images_dir.join(file_name);
            if legacy_flat.exists() {
                if let Some(parent) = target.parent() {
                    fs::create_dir_all(parent)?;
                }
                fs::copy(&legacy_flat, &target).with_context(|| {
                    format!(
                        "failed to repair legacy template {} to {}",
                        legacy_flat.display(),
                        target.display()
                    )
                })?;
                continue;
            }

            if let Some(source) = self.find_bundled_template_source(file_name) {
                if let Some(parent) = target.parent() {
                    fs::create_dir_all(parent)?;
                }
                fs::copy(&source, &target).with_context(|| {
                    format!(
                        "failed to restore bundled template {} to {}",
                        source.display(),
                        target.display()
                    )
                })?;
            }
        }

        Ok(())
    }

    fn find_bundled_template_source(&self, file_name: &std::ffi::OsStr) -> Option<PathBuf> {
        let base = self.paths.bundled_resources_dir.as_ref()?;
        let nested = base.join("images").join("templates").join(file_name);
        if nested.exists() {
            return Some(nested);
        }
        let legacy_flat = base.join("images").join(file_name);
        if legacy_flat.exists() {
            return Some(legacy_flat);
        }
        None
    }

    pub fn template_relative_path(&self, slug: &str) -> String {
        format!("images/templates/{slug}.png")
    }

    pub fn goods_relative_path(&self, big_category: &str, file_name: &str) -> String {
        let slug = CATEGORY_SLUGS
            .iter()
            .find(|(name, _)| *name == big_category)
            .map(|(_, slug)| *slug)
            .unwrap_or("misc");
        format!("images/goods/{slug}/{file_name}")
    }

    pub fn relative_to_absolute_data_path(&self, relative_path: &str) -> PathBuf {
        self.paths.resolve_data_path(relative_path)
    }

    pub fn resolve_template_absolute_path(&self, raw_path: &str) -> PathBuf {
        let normalized = self.normalize_template_path(raw_path);
        let preferred = self.relative_to_absolute_data_path(&normalized);
        if preferred.exists() {
            return preferred;
        }
        let legacy = self.relative_to_absolute_data_path(raw_path);
        if legacy.exists() {
            return legacy;
        }
        preferred
    }
}
