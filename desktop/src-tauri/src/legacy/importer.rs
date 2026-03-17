use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use anyhow::Result;
use uuid::Uuid;

use crate::app::types::{
    GoodsRecord, ImportReport, LegacyCandidate, MultiTaskRecord, PriceHistoryRecord,
    PurchaseHistoryRecord, SingleTaskRecord, iso_to_epoch, now_iso,
};
use crate::config::paths::AppPaths;
use crate::storage::repository::Repository;

#[derive(Clone)]
pub struct LegacyImporter {
    paths: Arc<AppPaths>,
    repo: Arc<Repository>,
}

impl LegacyImporter {
    pub fn new(paths: Arc<AppPaths>, repo: Arc<Repository>) -> Self {
        Self { paths, repo }
    }

    pub fn scan(&self) -> Result<Vec<LegacyCandidate>> {
        let mut candidates = Vec::new();
        for root in self.candidate_roots()? {
            let mut files = Vec::new();
            for name in [
                "config.json",
                "goods.json",
                "buy_tasks.json",
                "snipe_tasks.json",
            ] {
                if root.join(name).exists() {
                    files.push(name.to_string());
                }
            }
            let output_dir = root.join("output");
            if output_dir.exists() {
                files.push("output".to_string());
            }
            if files.is_empty() {
                continue;
            }
            candidates.push(LegacyCandidate {
                root: root.display().to_string(),
                display_name: root
                    .file_name()
                    .map(|value| value.to_string_lossy().to_string())
                    .unwrap_or_else(|| root.display().to_string()),
                files,
                output_dir: output_dir
                    .exists()
                    .then(|| output_dir.display().to_string()),
            });
        }
        Ok(candidates)
    }

    pub fn import(&self, source_root: &str) -> Result<ImportReport> {
        let source_root = PathBuf::from(source_root);
        let mut report = ImportReport {
            id: format!("import-{}", Uuid::new_v4()),
            source_root: source_root.display().to_string(),
            status: "completed".to_string(),
            goods_imported: 0,
            single_tasks_imported: self.import_single_tasks(&source_root.join("buy_tasks.json"))?,
            multi_tasks_imported: self.import_multi_tasks(&source_root.join("snipe_tasks.json"))?,
            price_rows_imported: 0,
            purchase_rows_imported: 0,
            finished_at: now_iso(),
        };

        let config_path = source_root.join("config.json");
        if config_path.exists() {
            let raw = fs::read_to_string(&config_path)?;
            let value: serde_json::Value = serde_json::from_str(&raw)?;
            if let Some(templates) = value.get("templates").and_then(|node| node.as_object()) {
                for (slug, item) in templates {
                    let path = item
                        .get("path")
                        .and_then(|node| node.as_str())
                        .unwrap_or_default();
                    let confidence = item
                        .get("confidence")
                        .and_then(|node| node.as_f64())
                        .unwrap_or(0.85);
                    let record = crate::app::types::TemplateConfig {
                        id: format!("tpl-{slug}"),
                        slug: slug.clone(),
                        name: slug.clone(),
                        kind: "imported".to_string(),
                        path: path.replace('\\', "/"),
                        confidence,
                        notes: Some("imported from legacy config".to_string()),
                        created_at: now_iso(),
                        updated_at: now_iso(),
                    };
                    self.repo.upsert_template(&record)?;
                }
            }
        }

        let goods_path = source_root.join("goods.json");
        if goods_path.exists() {
            let raw = fs::read_to_string(&goods_path)?;
            let items: Vec<serde_json::Value> = serde_json::from_str(&raw).unwrap_or_default();
            for item in items {
                let record = GoodsRecord {
                    id: item
                        .get("id")
                        .and_then(|node| node.as_str())
                        .map(str::to_string)
                        .unwrap_or_else(|| Uuid::new_v4().to_string()),
                    name: item
                        .get("name")
                        .and_then(|node| node.as_str())
                        .unwrap_or_default()
                        .to_string(),
                    search_name: item
                        .get("search_name")
                        .and_then(|node| node.as_str())
                        .unwrap_or_default()
                        .to_string(),
                    big_category: item
                        .get("big_category")
                        .and_then(|node| node.as_str())
                        .unwrap_or_default()
                        .to_string(),
                    sub_category: item
                        .get("sub_category")
                        .and_then(|node| node.as_str())
                        .unwrap_or_default()
                        .to_string(),
                    exchangeable: item
                        .get("exchangeable")
                        .and_then(|node| node.as_bool())
                        .unwrap_or(false),
                    craftable: item
                        .get("craftable")
                        .and_then(|node| node.as_bool())
                        .unwrap_or(false),
                    favorite: item
                        .get("favorite")
                        .and_then(|node| node.as_bool())
                        .unwrap_or(false),
                    image_path: item
                        .get("image_path")
                        .and_then(|node| node.as_str())
                        .unwrap_or("images/goods/_default.png")
                        .replace('\\', "/"),
                    price: item.get("price").and_then(|node| node.as_i64()),
                    created_at: now_iso(),
                    updated_at: now_iso(),
                };
                self.repo.save_goods(&record)?;
                report.goods_imported += 1;
            }
        }

        let output_dir = source_root.join("output");
        if output_dir.exists() {
            report.price_rows_imported =
                self.import_price_history(&output_dir.join("price_history.jsonl"))?;
            report.purchase_rows_imported =
                self.import_purchase_history(&output_dir.join("purchase_history.jsonl"))?;
        }
        self.repo.record_import(&report)?;
        Ok(report)
    }

    fn candidate_roots(&self) -> Result<Vec<PathBuf>> {
        let current_dir = std::env::current_dir()?;
        let mut candidates = vec![self.paths.data_dir.clone(), current_dir.clone()];
        let mut cursor = Some(current_dir.as_path());
        for _ in 0..4 {
            let Some(path) = cursor else {
                break;
            };
            candidates.push(path.join("data"));
            cursor = path.parent();
        }
        candidates.sort();
        candidates.dedup();
        Ok(candidates
            .into_iter()
            .filter(|path| path.exists())
            .collect())
    }

    fn import_single_tasks(&self, path: &Path) -> Result<usize> {
        if !path.exists() {
            return Ok(0);
        }
        let raw = fs::read_to_string(path)?;
        let value: serde_json::Value = serde_json::from_str(&raw)?;
        let tasks = value
            .get("tasks")
            .and_then(|node| node.as_array())
            .cloned()
            .unwrap_or_default();
        let mut imported = 0;
        for (index, task) in tasks.into_iter().enumerate() {
            let record = SingleTaskRecord {
                id: task
                    .get("id")
                    .and_then(|node| node.as_str())
                    .map(str::to_string)
                    .unwrap_or_else(|| Uuid::new_v4().to_string()),
                item_id: task
                    .get("item_id")
                    .and_then(|node| node.as_str())
                    .unwrap_or_default()
                    .to_string(),
                item_name: task
                    .get("item_name")
                    .and_then(|node| node.as_str())
                    .unwrap_or_default()
                    .to_string(),
                enabled: task
                    .get("enabled")
                    .and_then(|node| node.as_bool())
                    .unwrap_or(true),
                price_threshold: task
                    .get("price_threshold")
                    .and_then(|node| node.as_i64())
                    .unwrap_or(0),
                price_premium_pct: task
                    .get("price_premium_pct")
                    .and_then(|node| node.as_f64())
                    .unwrap_or(0.0),
                restock_price: task
                    .get("restock_price")
                    .and_then(|node| node.as_i64())
                    .unwrap_or(0),
                restock_premium_pct: task
                    .get("restock_premium_pct")
                    .and_then(|node| node.as_f64())
                    .unwrap_or(0.0),
                target_total: task
                    .get("target_total")
                    .and_then(|node| node.as_i64())
                    .unwrap_or(0),
                purchased: task
                    .get("purchased")
                    .and_then(|node| node.as_i64())
                    .unwrap_or(0),
                duration_min: task
                    .get("duration_min")
                    .and_then(|node| node.as_i64())
                    .unwrap_or(10),
                time_start: task
                    .get("time_start")
                    .and_then(|node| node.as_str())
                    .map(str::to_string),
                time_end: task
                    .get("time_end")
                    .and_then(|node| node.as_str())
                    .map(str::to_string),
                order_index: task
                    .get("order")
                    .and_then(|node| node.as_i64())
                    .unwrap_or(index as i64),
                created_at: now_iso(),
                updated_at: now_iso(),
            };
            self.repo.save_single_task(&record)?;
            imported += 1;
        }
        Ok(imported)
    }

    fn import_multi_tasks(&self, path: &Path) -> Result<usize> {
        if !path.exists() {
            return Ok(0);
        }
        let raw = fs::read_to_string(path)?;
        let value: serde_json::Value = serde_json::from_str(&raw)?;
        let tasks = value
            .get("items")
            .and_then(|node| node.as_array())
            .cloned()
            .unwrap_or_default();
        let mut imported = 0;
        for (index, task) in tasks.into_iter().enumerate() {
            let record = MultiTaskRecord {
                id: task
                    .get("id")
                    .and_then(|node| node.as_str())
                    .map(str::to_string)
                    .unwrap_or_else(|| Uuid::new_v4().to_string()),
                item_id: task
                    .get("item_id")
                    .and_then(|node| node.as_str())
                    .unwrap_or_default()
                    .to_string(),
                name: task
                    .get("name")
                    .and_then(|node| node.as_str())
                    .unwrap_or_default()
                    .to_string(),
                enabled: task
                    .get("enabled")
                    .and_then(|node| node.as_bool())
                    .unwrap_or(true),
                price: task
                    .get("price")
                    .or_else(|| task.get("price_threshold"))
                    .and_then(|node| node.as_i64())
                    .unwrap_or(0),
                premium_pct: task
                    .get("premium_pct")
                    .and_then(|node| node.as_f64())
                    .unwrap_or(0.0),
                purchase_mode: task
                    .get("purchase_mode")
                    .or_else(|| task.get("mode"))
                    .and_then(|node| node.as_str())
                    .unwrap_or("normal")
                    .to_string(),
                target_total: task
                    .get("target_total")
                    .or_else(|| task.get("buy_qty"))
                    .and_then(|node| node.as_i64())
                    .unwrap_or(0),
                purchased: task
                    .get("purchased")
                    .and_then(|node| node.as_i64())
                    .unwrap_or(0),
                order_index: index as i64,
                image_path: task
                    .get("image_path")
                    .or_else(|| task.get("template"))
                    .and_then(|node| node.as_str())
                    .unwrap_or("images/goods/_default.png")
                    .replace('\\', "/"),
                big_category: task
                    .get("big_category")
                    .and_then(|node| node.as_str())
                    .unwrap_or_default()
                    .to_string(),
                created_at: now_iso(),
                updated_at: now_iso(),
            };
            self.repo.save_multi_task(&record)?;
            imported += 1;
        }
        Ok(imported)
    }

    fn import_price_history(&self, path: &Path) -> Result<usize> {
        if !path.exists() {
            return Ok(0);
        }
        let mut imported = 0;
        for line in fs::read_to_string(path)?.lines() {
            if line.trim().is_empty() {
                continue;
            }
            let value: serde_json::Value = match serde_json::from_str(line) {
                Ok(value) => value,
                Err(_) => continue,
            };
            let record = PriceHistoryRecord {
                id: format!("price-{}", Uuid::new_v4()),
                item_id: value
                    .get("item_id")
                    .and_then(|node| node.as_str())
                    .unwrap_or_default()
                    .to_string(),
                item_name: value
                    .get("item_name")
                    .and_then(|node| node.as_str())
                    .unwrap_or_default()
                    .to_string(),
                category: value
                    .get("category")
                    .and_then(|node| node.as_str())
                    .map(str::to_string),
                price: value
                    .get("price")
                    .and_then(|node| node.as_i64())
                    .unwrap_or(0),
                observed_at: value
                    .get("iso")
                    .and_then(|node| node.as_str())
                    .map(str::to_string)
                    .unwrap_or_else(now_iso),
                observed_at_epoch: 0,
            };
            let mut record = record;
            record.observed_at_epoch = iso_to_epoch(&record.observed_at);
            self.repo.insert_price_history(&record)?;
            imported += 1;
        }
        Ok(imported)
    }

    fn import_purchase_history(&self, path: &Path) -> Result<usize> {
        if !path.exists() {
            return Ok(0);
        }
        let mut imported = 0;
        for line in fs::read_to_string(path)?.lines() {
            if line.trim().is_empty() {
                continue;
            }
            let value: serde_json::Value = match serde_json::from_str(line) {
                Ok(value) => value,
                Err(_) => continue,
            };
            let price = value
                .get("price")
                .and_then(|node| node.as_i64())
                .unwrap_or(0);
            let qty = value.get("qty").and_then(|node| node.as_i64()).unwrap_or(0);
            let record = PurchaseHistoryRecord {
                id: format!("purchase-{}", Uuid::new_v4()),
                item_id: value
                    .get("item_id")
                    .and_then(|node| node.as_str())
                    .unwrap_or_default()
                    .to_string(),
                item_name: value
                    .get("item_name")
                    .and_then(|node| node.as_str())
                    .unwrap_or_default()
                    .to_string(),
                category: value
                    .get("category")
                    .and_then(|node| node.as_str())
                    .map(str::to_string),
                price,
                qty,
                amount: value
                    .get("amount")
                    .and_then(|node| node.as_i64())
                    .unwrap_or(price * qty),
                task_id: value
                    .get("task_id")
                    .and_then(|node| node.as_str())
                    .map(str::to_string),
                task_name: value
                    .get("task_name")
                    .and_then(|node| node.as_str())
                    .map(str::to_string),
                used_max: value.get("used_max").and_then(|node| node.as_bool()),
                purchased_at: value
                    .get("iso")
                    .and_then(|node| node.as_str())
                    .map(str::to_string)
                    .unwrap_or_else(now_iso),
            };
            self.repo.insert_purchase_history(&record)?;
            imported += 1;
        }
        Ok(imported)
    }
}
