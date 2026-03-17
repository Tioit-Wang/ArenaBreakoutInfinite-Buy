use std::path::PathBuf;

use anyhow::{Context, Result};
use rusqlite::{Connection, OptionalExtension, params};
use serde::de::DeserializeOwned;
use serde_json::json;

use crate::app::types::{
    AppConfig, AutomationRunState, GoodsRecord, HistorySummary, ImportReport, MultiTaskRecord,
    PriceHistoryRecord, PurchaseHistoryRecord, RuntimeLogEntry, SingleTaskRecord, TemplateConfig,
    now_iso,
};
use crate::storage::migrations::MIGRATIONS;

#[derive(Clone)]
pub struct Repository {
    db_path: PathBuf,
}

impl Repository {
    pub fn new(db_path: PathBuf) -> Self {
        Self { db_path }
    }

    pub fn init(&self) -> Result<()> {
        let conn = self.connect()?;
        for migration in MIGRATIONS {
            conn.execute_batch(migration)?;
        }
        Ok(())
    }

    pub fn get_config(&self) -> Result<Option<AppConfig>> {
        let conn = self.connect()?;
        conn.query_row(
            "SELECT value_json FROM app_config WHERE key = 'settings'",
            [],
            |row| row.get::<_, String>(0),
        )
        .optional()?
        .map(|raw| serde_json::from_str::<AppConfig>(&raw).context("invalid app config json"))
        .transpose()
    }

    pub fn save_config(&self, config: &AppConfig) -> Result<AppConfig> {
        let conn = self.connect()?;
        let updated_at = now_iso();
        let raw = serde_json::to_string(config)?;
        conn.execute(
            r#"
            INSERT INTO app_config (key, value_json, updated_at)
            VALUES ('settings', ?1, ?2)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
            "#,
            params![raw, updated_at],
        )?;
        Ok(config.clone())
    }

    pub fn list_templates(&self) -> Result<Vec<TemplateConfig>> {
        self.list_payloads("SELECT payload_json FROM templates ORDER BY slug ASC", [])
    }

    pub fn upsert_template(&self, template: &TemplateConfig) -> Result<TemplateConfig> {
        let conn = self.connect()?;
        conn.execute(
            r#"
            INSERT INTO templates (id, slug, name, kind, path, confidence, payload_json, created_at, updated_at)
            VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)
            ON CONFLICT(id) DO UPDATE SET
              slug = excluded.slug,
              name = excluded.name,
              kind = excluded.kind,
              path = excluded.path,
              confidence = excluded.confidence,
              payload_json = excluded.payload_json,
              updated_at = excluded.updated_at
            "#,
            params![
                template.id,
                template.slug,
                template.name,
                template.kind,
                template.path,
                template.confidence,
                serde_json::to_string(template)?,
                template.created_at,
                template.updated_at,
            ],
        )?;
        Ok(template.clone())
    }

    pub fn list_goods(&self) -> Result<Vec<GoodsRecord>> {
        self.list_payloads(
            "SELECT payload_json FROM goods ORDER BY name COLLATE NOCASE ASC",
            [],
        )
    }

    pub fn save_goods(&self, goods: &GoodsRecord) -> Result<GoodsRecord> {
        let conn = self.connect()?;
        conn.execute(
            r#"
            INSERT INTO goods (id, name, search_name, big_category, sub_category, favorite, image_path, payload_json, created_at, updated_at)
            VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)
            ON CONFLICT(id) DO UPDATE SET
              name = excluded.name,
              search_name = excluded.search_name,
              big_category = excluded.big_category,
              sub_category = excluded.sub_category,
              favorite = excluded.favorite,
              image_path = excluded.image_path,
              payload_json = excluded.payload_json,
              updated_at = excluded.updated_at
            "#,
            params![
                goods.id,
                goods.name,
                goods.search_name,
                goods.big_category,
                goods.sub_category,
                if goods.favorite { 1 } else { 0 },
                goods.image_path,
                serde_json::to_string(goods)?,
                goods.created_at,
                goods.updated_at,
            ],
        )?;
        Ok(goods.clone())
    }

    pub fn delete_goods(&self, id: &str) -> Result<()> {
        self.connect()?
            .execute("DELETE FROM goods WHERE id = ?1", params![id])?;
        Ok(())
    }

    pub fn list_single_tasks(&self) -> Result<Vec<SingleTaskRecord>> {
        self.list_payloads(
            "SELECT payload_json FROM single_tasks ORDER BY order_index ASC, updated_at ASC",
            [],
        )
    }

    pub fn save_single_task(&self, task: &SingleTaskRecord) -> Result<SingleTaskRecord> {
        let conn = self.connect()?;
        conn.execute(
            r#"
            INSERT INTO single_tasks (id, item_id, item_name, enabled, order_index, payload_json, created_at, updated_at)
            VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)
            ON CONFLICT(id) DO UPDATE SET
              item_id = excluded.item_id,
              item_name = excluded.item_name,
              enabled = excluded.enabled,
              order_index = excluded.order_index,
              payload_json = excluded.payload_json,
              updated_at = excluded.updated_at
            "#,
            params![
                task.id,
                task.item_id,
                task.item_name,
                if task.enabled { 1 } else { 0 },
                task.order_index,
                serde_json::to_string(task)?,
                task.created_at,
                task.updated_at,
            ],
        )?;
        Ok(task.clone())
    }

    pub fn reorder_single_tasks(&self, task_ids: &[String]) -> Result<Vec<SingleTaskRecord>> {
        let conn = self.connect()?;
        for (idx, task_id) in task_ids.iter().enumerate() {
            conn.execute(
                "UPDATE single_tasks SET order_index = ?1, updated_at = ?2 WHERE id = ?3",
                params![idx as i64, now_iso(), task_id],
            )?;
        }
        self.list_single_tasks()
    }

    pub fn delete_single_task(&self, id: &str) -> Result<()> {
        self.connect()?
            .execute("DELETE FROM single_tasks WHERE id = ?1", params![id])?;
        Ok(())
    }

    pub fn list_multi_tasks(&self) -> Result<Vec<MultiTaskRecord>> {
        self.list_payloads(
            "SELECT payload_json FROM multi_tasks ORDER BY order_index ASC, updated_at ASC",
            [],
        )
    }

    pub fn save_multi_task(&self, task: &MultiTaskRecord) -> Result<MultiTaskRecord> {
        let conn = self.connect()?;
        conn.execute(
            r#"
            INSERT INTO multi_tasks (id, item_id, name, enabled, order_index, payload_json, created_at, updated_at)
            VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)
            ON CONFLICT(id) DO UPDATE SET
              item_id = excluded.item_id,
              name = excluded.name,
              enabled = excluded.enabled,
              order_index = excluded.order_index,
              payload_json = excluded.payload_json,
              updated_at = excluded.updated_at
            "#,
            params![
                task.id,
                task.item_id,
                task.name,
                if task.enabled { 1 } else { 0 },
                task.order_index,
                serde_json::to_string(task)?,
                task.created_at,
                task.updated_at,
            ],
        )?;
        Ok(task.clone())
    }

    pub fn reorder_multi_tasks(&self, task_ids: &[String]) -> Result<Vec<MultiTaskRecord>> {
        let conn = self.connect()?;
        for (idx, task_id) in task_ids.iter().enumerate() {
            conn.execute(
                "UPDATE multi_tasks SET order_index = ?1, updated_at = ?2 WHERE id = ?3",
                params![idx as i64, now_iso(), task_id],
            )?;
        }
        self.list_multi_tasks()
    }

    pub fn delete_multi_task(&self, id: &str) -> Result<()> {
        self.connect()?
            .execute("DELETE FROM multi_tasks WHERE id = ?1", params![id])?;
        Ok(())
    }

    pub fn insert_price_history(&self, record: &PriceHistoryRecord) -> Result<()> {
        self.connect()?.execute(
            r#"
            INSERT OR REPLACE INTO price_history (id, item_id, item_name, category, price, observed_at, payload_json)
            VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
            "#,
            params![
                record.id,
                record.item_id,
                record.item_name,
                record.category,
                record.price,
                record.observed_at,
                serde_json::to_string(record)?,
            ],
        )?;
        Ok(())
    }

    pub fn insert_purchase_history(&self, record: &PurchaseHistoryRecord) -> Result<()> {
        self.connect()?.execute(
            r#"
            INSERT OR REPLACE INTO purchase_history (id, item_id, item_name, category, price, qty, amount, task_id, task_name, used_max, purchased_at, payload_json)
            VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)
            "#,
            params![
                record.id,
                record.item_id,
                record.item_name,
                record.category,
                record.price,
                record.qty,
                record.amount,
                record.task_id,
                record.task_name,
                record.used_max.map(|value| if value { 1 } else { 0 }),
                record.purchased_at,
                serde_json::to_string(record)?,
            ],
        )?;
        Ok(())
    }

    pub fn query_price_history(
        &self,
        item_id: Option<&str>,
        limit: u32,
    ) -> Result<Vec<PriceHistoryRecord>> {
        let conn = self.connect()?;
        let mut out = Vec::new();
        if let Some(item_id) = item_id {
            let mut statement = conn.prepare(
                "SELECT payload_json FROM price_history WHERE item_id = ?1 ORDER BY observed_at DESC LIMIT ?2",
            )?;
            let rows = statement.query_map(params![item_id, limit as i64], |row| {
                row.get::<_, String>(0)
            })?;
            for row in rows {
                let raw = row?;
                out.push(serde_json::from_str(&raw)?);
            }
        } else {
            let mut statement = conn.prepare(
                "SELECT payload_json FROM price_history ORDER BY observed_at DESC LIMIT ?1",
            )?;
            let rows = statement.query_map(params![limit as i64], |row| row.get::<_, String>(0))?;
            for row in rows {
                let raw = row?;
                out.push(serde_json::from_str(&raw)?);
            }
        }
        Ok(out)
    }

    pub fn query_purchase_history(
        &self,
        item_id: Option<&str>,
        limit: u32,
    ) -> Result<Vec<PurchaseHistoryRecord>> {
        let conn = self.connect()?;
        let mut out = Vec::new();
        if let Some(item_id) = item_id {
            let mut statement = conn.prepare(
                "SELECT payload_json FROM purchase_history WHERE item_id = ?1 ORDER BY purchased_at DESC LIMIT ?2",
            )?;
            let rows = statement.query_map(params![item_id, limit as i64], |row| {
                row.get::<_, String>(0)
            })?;
            for row in rows {
                let raw = row?;
                out.push(serde_json::from_str(&raw)?);
            }
        } else {
            let mut statement = conn.prepare(
                "SELECT payload_json FROM purchase_history ORDER BY purchased_at DESC LIMIT ?1",
            )?;
            let rows = statement.query_map(params![limit as i64], |row| row.get::<_, String>(0))?;
            for row in rows {
                let raw = row?;
                out.push(serde_json::from_str(&raw)?);
            }
        }
        Ok(out)
    }

    pub fn summarize_history(&self, item_id: Option<&str>) -> Result<HistorySummary> {
        let prices = self.query_price_history(item_id, 1_000)?;
        let purchases = self.query_purchase_history(item_id, 1_000)?;
        let mut summary = HistorySummary::default();

        for record in prices.iter() {
            summary.price_count += 1;
            summary.price_avg += record.price;
            if summary.price_min == 0 || record.price < summary.price_min {
                summary.price_min = record.price;
            }
            if record.price > summary.price_max {
                summary.price_max = record.price;
            }
        }
        if summary.price_count > 0 {
            summary.latest_price = prices.first().map(|record| record.price).unwrap_or(0);
            summary.price_avg /= summary.price_count;
        }

        for record in purchases {
            summary.purchase_count += 1;
            summary.purchase_qty += record.qty;
            summary.purchase_amount += record.amount;
            summary.purchase_avg += record.price;
        }
        if summary.purchase_count > 0 {
            summary.purchase_avg /= summary.purchase_count;
        }

        Ok(summary)
    }

    pub fn upsert_runtime_session(&self, state: &AutomationRunState) -> Result<()> {
        let Some(session_id) = &state.session_id else {
            return Ok(());
        };
        let now = now_iso();
        self.connect()?.execute(
            r#"
            INSERT INTO runtime_sessions (id, mode, state, payload_json, started_at, ended_at, updated_at)
            VALUES (?1, ?2, ?3, ?4, ?5, NULL, ?6)
            ON CONFLICT(id) DO UPDATE SET
              mode = excluded.mode,
              state = excluded.state,
              payload_json = excluded.payload_json,
              ended_at = CASE WHEN excluded.state IN ('stopped', 'failed', 'completed') THEN excluded.updated_at ELSE runtime_sessions.ended_at END,
              updated_at = excluded.updated_at
            "#,
            params![
                session_id,
                state.mode.clone().unwrap_or_else(|| "unknown".to_string()),
                state.state,
                serde_json::to_string(state)?,
                state.started_at,
                now,
            ],
        )?;
        Ok(())
    }

    pub fn append_runtime_log(&self, entry: &RuntimeLogEntry) -> Result<i64> {
        let conn = self.connect()?;
        conn.execute(
            r#"
            INSERT INTO runtime_logs (session_id, level, scope, message, created_at, payload_json)
            VALUES (?1, ?2, ?3, ?4, ?5, ?6)
            "#,
            params![
                entry.session_id,
                entry.level,
                entry.scope,
                entry.message,
                entry.created_at,
                serde_json::to_string(entry)?,
            ],
        )?;
        Ok(conn.last_insert_rowid())
    }

    pub fn recent_runtime_logs(&self, limit: u32) -> Result<Vec<RuntimeLogEntry>> {
        let conn = self.connect()?;
        let mut statement =
            conn.prepare("SELECT payload_json FROM runtime_logs ORDER BY id DESC LIMIT ?1")?;
        let rows = statement.query_map(params![limit as i64], |row| row.get::<_, String>(0))?;
        let mut out = Vec::new();
        for row in rows {
            out.push(serde_json::from_str(&row?)?);
        }
        Ok(out)
    }

    pub fn record_import(&self, report: &ImportReport) -> Result<ImportReport> {
        let conn = self.connect()?;
        let summary_json = json!({
            "goodsImported": report.goods_imported,
            "singleTasksImported": report.single_tasks_imported,
            "multiTasksImported": report.multi_tasks_imported,
            "priceRowsImported": report.price_rows_imported,
            "purchaseRowsImported": report.purchase_rows_imported,
            "finishedAt": report.finished_at,
        });
        let now = now_iso();
        conn.execute(
            r#"
            INSERT INTO imports (id, source_root, status, summary_json, created_at, updated_at)
            VALUES (?1, ?2, ?3, ?4, ?5, ?6)
            ON CONFLICT(id) DO UPDATE SET
              status = excluded.status,
              summary_json = excluded.summary_json,
              updated_at = excluded.updated_at
            "#,
            params![
                report.id,
                report.source_root,
                report.status,
                serde_json::to_string(&summary_json)?,
                now,
                now,
            ],
        )?;
        Ok(report.clone())
    }

    fn connect(&self) -> Result<Connection> {
        Connection::open(&self.db_path)
            .with_context(|| format!("failed to open sqlite db {}", self.db_path.display()))
    }

    fn list_payloads<T, P>(&self, sql: &str, params: P) -> Result<Vec<T>>
    where
        T: DeserializeOwned,
        P: rusqlite::Params,
    {
        let conn = self.connect()?;
        let mut statement = conn.prepare(sql)?;
        let rows = statement.query_map(params, |row| row.get::<_, String>(0))?;
        let mut out = Vec::new();
        for row in rows {
            out.push(serde_json::from_str(&row?)?);
        }
        Ok(out)
    }
}
