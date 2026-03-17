use std::collections::BTreeMap;
use std::path::PathBuf;

use anyhow::{Context, Result, anyhow};
use chrono::{DateTime, Utc};
use rusqlite::{Connection, OptionalExtension, params};
use serde::de::DeserializeOwned;
use serde_json::json;

use crate::app::types::{
    AppConfig, AutomationRunState, GoodsRecord, HistorySummary, ImportReport,
    ItemPriceTrendPoint, ItemPriceTrendResponse, MultiTaskRecord, PriceHistoryRecord,
    PurchaseHistoryRecord, RuntimeLogEntry, SingleTaskRecord, TemplateConfig, iso_to_epoch,
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
        let mut conn = self.connect()?;
        for migration in MIGRATIONS {
            conn.execute_batch(migration)?;
        }
        Self::ensure_price_history_epoch_schema(&mut conn)?;
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
            INSERT OR REPLACE INTO price_history (id, item_id, item_name, category, price, observed_at, observed_at_epoch, payload_json)
            VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)
            "#,
            params![
                record.id,
                record.item_id,
                record.item_name,
                record.category,
                record.price,
                record.observed_at,
                record.observed_at_epoch,
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
                "SELECT payload_json FROM price_history WHERE item_id = ?1 ORDER BY observed_at_epoch DESC, observed_at DESC LIMIT ?2",
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
                "SELECT payload_json FROM price_history ORDER BY observed_at_epoch DESC, observed_at DESC LIMIT ?1",
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

    pub fn query_item_price_trend(
        &self,
        item_id: &str,
        from: &str,
        to: &str,
        timezone_offset_min: i32,
    ) -> Result<ItemPriceTrendResponse> {
        let from_epoch = parse_required_epoch(from, "from")?;
        let to_epoch = parse_required_epoch(to, "to")?;
        if from_epoch > to_epoch {
            return Err(anyhow!("from must be earlier than or equal to to"));
        }

        let conn = self.connect()?;
        let mut item_name = conn
            .query_row(
                "SELECT name FROM goods WHERE id = ?1",
                params![item_id],
                |row| row.get::<_, String>(0),
            )
            .optional()?
            .unwrap_or_default();

        let mut statement = conn.prepare(
            r#"
            SELECT item_name, price, observed_at_epoch
            FROM price_history
            WHERE item_id = ?1 AND observed_at_epoch >= ?2 AND observed_at_epoch <= ?3
            ORDER BY observed_at_epoch ASC, observed_at ASC
            "#,
        )?;
        let rows = statement.query_map(params![item_id, from_epoch, to_epoch], |row| {
            Ok(PriceTrendRow {
                item_name: row.get::<_, String>(0)?,
                price: row.get::<_, i64>(1)?,
                observed_at_epoch: row.get::<_, i64>(2)?,
            })
        })?;

        let mut buckets: BTreeMap<String, DailyPriceAggregate> = BTreeMap::new();
        let mut range_sum = 0_i64;
        let mut range_count = 0_i64;
        let mut range_min: Option<i64> = None;
        let mut range_max: Option<i64> = None;
        let mut latest_epoch = i64::MIN;
        let mut latest_price = None;

        for row in rows {
            let row = row?;
            if item_name.trim().is_empty() && !row.item_name.trim().is_empty() {
                item_name = row.item_name.clone();
            }
            let day = local_day_key(row.observed_at_epoch, timezone_offset_min)?;
            let entry = buckets.entry(day).or_default();
            entry.sample_count += 1;
            entry.sum_price += row.price;
            entry.min_price = match entry.min_price {
                Some(current) => Some(current.min(row.price)),
                None => Some(row.price),
            };
            entry.max_price = match entry.max_price {
                Some(current) => Some(current.max(row.price)),
                None => Some(row.price),
            };
            if row.observed_at_epoch >= entry.latest_epoch {
                entry.latest_epoch = row.observed_at_epoch;
                entry.latest_price = Some(row.price);
            }

            range_count += 1;
            range_sum += row.price;
            range_min = match range_min {
                Some(current) => Some(current.min(row.price)),
                None => Some(row.price),
            };
            range_max = match range_max {
                Some(current) => Some(current.max(row.price)),
                None => Some(row.price),
            };
            if row.observed_at_epoch >= latest_epoch {
                latest_epoch = row.observed_at_epoch;
                latest_price = Some(row.price);
            }
        }

        let points = buckets
            .into_iter()
            .map(|(day, bucket)| ItemPriceTrendPoint {
                day,
                min_price: bucket.min_price.unwrap_or_default(),
                max_price: bucket.max_price.unwrap_or_default(),
                avg_price: if bucket.sample_count > 0 {
                    bucket.sum_price / bucket.sample_count
                } else {
                    0
                },
                latest_price: bucket.latest_price.unwrap_or_default(),
                sample_count: bucket.sample_count,
            })
            .collect();

        Ok(ItemPriceTrendResponse {
            item_id: item_id.to_string(),
            item_name,
            from: from.to_string(),
            to: to.to_string(),
            points,
            latest_price,
            range_min_price: range_min,
            range_max_price: range_max,
            range_avg_price: if range_count > 0 {
                Some(range_sum / range_count)
            } else {
                None
            },
        })
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

    fn ensure_price_history_epoch_schema(conn: &mut Connection) -> Result<()> {
        if !Self::has_column(conn, "price_history", "observed_at_epoch")? {
            conn.execute(
                "ALTER TABLE price_history ADD COLUMN observed_at_epoch INTEGER NOT NULL DEFAULT 0",
                [],
            )?;
        }

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_price_history_item_observed_at_epoch ON price_history (item_id, observed_at_epoch DESC)",
            [],
        )?;

        let pending_rows = {
            let mut statement =
                conn.prepare("SELECT id, observed_at FROM price_history WHERE observed_at_epoch = 0")?;
            let rows = statement.query_map([], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })?;
            let mut out = Vec::new();
            for row in rows {
                out.push(row?);
            }
            out
        };

        if pending_rows.is_empty() {
            return Ok(());
        }

        let tx = conn.transaction()?;
        {
            let mut update = tx.prepare(
                "UPDATE price_history SET observed_at_epoch = ?1 WHERE id = ?2",
            )?;
            for (id, observed_at) in pending_rows {
                update.execute(params![iso_to_epoch(&observed_at), id])?;
            }
        }
        tx.commit()?;
        Ok(())
    }

    fn has_column(conn: &Connection, table: &str, column: &str) -> Result<bool> {
        let mut statement = conn.prepare(&format!("PRAGMA table_info({table})"))?;
        let rows = statement.query_map([], |row| row.get::<_, String>(1))?;
        for row in rows {
            if row? == column {
                return Ok(true);
            }
        }
        Ok(false)
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

#[derive(Debug)]
struct PriceTrendRow {
    item_name: String,
    price: i64,
    observed_at_epoch: i64,
}

#[derive(Debug, Default)]
struct DailyPriceAggregate {
    min_price: Option<i64>,
    max_price: Option<i64>,
    sum_price: i64,
    sample_count: i64,
    latest_epoch: i64,
    latest_price: Option<i64>,
}

fn parse_required_epoch(raw: &str, label: &str) -> Result<i64> {
    DateTime::parse_from_rfc3339(raw)
        .map(|value| value.timestamp())
        .map_err(|error| anyhow!("invalid {label} datetime: {error}"))
}

fn local_day_key(epoch: i64, timezone_offset_min: i32) -> Result<String> {
    let local_epoch = epoch - i64::from(timezone_offset_min) * 60;
    let timestamp = DateTime::<Utc>::from_timestamp(local_epoch, 0)
        .ok_or_else(|| anyhow!("invalid epoch for local day bucketing: {epoch}"))?;
    Ok(timestamp.format("%Y-%m-%d").to_string())
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::PathBuf;

    use super::*;

    fn temp_db_path(label: &str) -> PathBuf {
        let unique = format!(
            "arena-buyer-history-{label}-{}.sqlite3",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("system clock before unix epoch")
                .as_nanos()
        );
        std::env::temp_dir().join(unique)
    }

    fn cleanup_db(path: &PathBuf) {
        let _ = fs::remove_file(path);
    }

    fn sample_goods(item_id: &str, name: &str) -> GoodsRecord {
        GoodsRecord {
            id: item_id.to_string(),
            name: name.to_string(),
            search_name: name.to_string(),
            big_category: "物资".to_string(),
            sub_category: "测试".to_string(),
            exchangeable: false,
            craftable: false,
            favorite: false,
            image_path: "images/goods/_default.png".to_string(),
            price: None,
            created_at: now_iso(),
            updated_at: now_iso(),
        }
    }

    #[test]
    fn init_backfills_observed_at_epoch_for_existing_rows() -> Result<()> {
        let path = temp_db_path("migration");
        let conn = Connection::open(&path)?;
        conn.execute_batch(
            r#"
            CREATE TABLE price_history (
              id TEXT PRIMARY KEY,
              item_id TEXT NOT NULL,
              item_name TEXT NOT NULL,
              category TEXT,
              price INTEGER NOT NULL,
              observed_at TEXT NOT NULL,
              payload_json TEXT NOT NULL
            );
            "#,
        )?;
        let observed_at = "2026-03-10T12:34:56Z";
        let payload = serde_json::json!({
            "id": "price-legacy-1",
            "itemId": "item-1",
            "itemName": "测试物品",
            "category": "物资",
            "price": 123,
            "observedAt": observed_at,
        });
        conn.execute(
            r#"
            INSERT INTO price_history (id, item_id, item_name, category, price, observed_at, payload_json)
            VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
            "#,
            params![
                "price-legacy-1",
                "item-1",
                "测试物品",
                "物资",
                123_i64,
                observed_at,
                payload.to_string(),
            ],
        )?;
        drop(conn);

        let repo = Repository::new(path.clone());
        repo.init()?;

        let observed_at_epoch: i64 = repo.connect()?.query_row(
            "SELECT observed_at_epoch FROM price_history WHERE id = ?1",
            params!["price-legacy-1"],
            |row| row.get(0),
        )?;
        assert_eq!(observed_at_epoch, iso_to_epoch(observed_at));

        let trend = repo.query_item_price_trend(
            "item-1",
            "2026-03-10T00:00:00Z",
            "2026-03-10T23:59:59Z",
            0,
        )?;
        assert_eq!(trend.latest_price, Some(123));
        assert_eq!(trend.points.len(), 1);

        cleanup_db(&path);
        Ok(())
    }

    #[test]
    fn query_item_price_trend_groups_by_local_day_and_ignores_other_items() -> Result<()> {
        let path = temp_db_path("trend");
        let repo = Repository::new(path.clone());
        repo.init()?;
        repo.save_goods(&sample_goods("item-1", "医疗包"))?;

        let item_a_rows = [
            ("price-a-1", 100_i64, "2026-03-10T23:30:00Z"),
            ("price-a-2", 150_i64, "2026-03-11T00:30:00Z"),
            ("price-a-3", 90_i64, "2026-03-11T16:30:00Z"),
        ];
        for (id, price, observed_at) in item_a_rows {
            repo.insert_price_history(&PriceHistoryRecord {
                id: id.to_string(),
                item_id: "item-1".to_string(),
                item_name: "医疗包".to_string(),
                category: Some("物资".to_string()),
                price,
                observed_at: observed_at.to_string(),
                observed_at_epoch: iso_to_epoch(observed_at),
            })?;
        }
        repo.insert_price_history(&PriceHistoryRecord {
            id: "price-b-1".to_string(),
            item_id: "item-2".to_string(),
            item_name: "弹药".to_string(),
            category: Some("战斗".to_string()),
            price: 999,
            observed_at: "2026-03-11T08:00:00Z".to_string(),
            observed_at_epoch: iso_to_epoch("2026-03-11T08:00:00Z"),
        })?;

        let response = repo.query_item_price_trend(
            "item-1",
            "2026-03-10T00:00:00Z",
            "2026-03-12T23:59:59Z",
            -480,
        )?;

        assert_eq!(response.item_name, "医疗包");
        assert_eq!(response.latest_price, Some(90));
        assert_eq!(response.range_min_price, Some(90));
        assert_eq!(response.range_max_price, Some(150));
        assert_eq!(response.range_avg_price, Some(113));
        assert_eq!(response.points.len(), 2);
        assert_eq!(
            response.points[0],
            ItemPriceTrendPoint {
                day: "2026-03-11".to_string(),
                min_price: 100,
                max_price: 150,
                avg_price: 125,
                latest_price: 150,
                sample_count: 2,
            }
        );
        assert_eq!(
            response.points[1],
            ItemPriceTrendPoint {
                day: "2026-03-12".to_string(),
                min_price: 90,
                max_price: 90,
                avg_price: 90,
                latest_price: 90,
                sample_count: 1,
            }
        );

        cleanup_db(&path);
        Ok(())
    }
}
