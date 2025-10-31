# SQLite 存储方案设计

## 背景与目标
- 价格采集频率：单次运行最多监控 10 个商品，约 2 次/3 秒上报 => 峰值约 400 条/分钟、每日 57.6 万条。
- 统计需求：最近 7 天需要分钟级的最高/最低/平均价，7 天之后需要小时级统计；购买记录需完整保留。
- 现状：通过 JSONL 追加写入，随着体量增长带来 I/O 与查询性能问题，缺乏聚合分层与自动清理。
- 目标：使用 SQLite 统一存储价格、统计、购买与配置数据，支持实时写入、查询高效、定期归档与清理，保留 7 天内的事件级数据。

## 数据库文件布局
- 默认路径：沿用现有 `output/` 目录，创建 `output/history.sqlite3`。
- PRAGMA 建议：
  ```sql
  PRAGMA journal_mode = WAL;
  PRAGMA synchronous = NORMAL;
  PRAGMA temp_store = MEMORY;
  PRAGMA busy_timeout = 2000;
  ```
- 连接管理：单实例运行，可在应用启动时复用一个 `sqlite3.Connection`，按需开启子线程连接（设置 `check_same_thread=False`）。

## 表结构概览
| 表名 | 作用 | 保留策略 |
| --- | --- | --- |
| `market_goods` | 商品市场的基础资料 | 持续保留 |
| `task_profile` | 任务配置清单 | 持续保留 |
| `task_run` | 任务运行实例记录 | 持续保留 |
| `task_purchase_summary` | 任务维度的购买统计 | 持续保留 |
| `price_event` | 原始价格事件留存，用于重算与调试 | 保留最近 7 天 |
| `price_minutely` | 分钟聚合的价格统计 | 保留最近 7 天 |
| `price_hourly` | 小时聚合的价格统计 | 长期保留（可配置） |
| `purchase_event` | 购买行为记录 | 长期保留 |
| `app_config` | 运行配置（KV/JSON） | 持续保留 |
| `config_meta` | 配置元信息与迁移版本 | 持续保留 |

## 表字段设计

### market_goods —— 商品基础信息
| 字段 | 类型 | 约束 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `item_id` | TEXT | PRIMARY KEY | | 商品唯一标识，对应业务中的货物 ID |
| `display_name` | TEXT | NOT NULL | `''` | 默认显示名称 |
| `search_name` | TEXT | | `NULL` | 搜索使用的名称，可为空 |
| `category` | TEXT | | `NULL` | 一级分类（如 `ammo`, `keys` 等） |
| `sub_category` | TEXT | | `NULL` | 二级分类或子类 |
| `enabled` | INTEGER | NOT NULL | `1` | 是否在 UI 中启用（0/1） |
| `image_path` | TEXT | | `NULL` | UI 所需的资源相对路径，方便渲染 |
| `meta_json` | TEXT | | `NULL` | 额外配置（如 ROI、限价、偏好标签） |
| `created_at` | INTEGER | NOT NULL | | 创建时间戳（秒） |
| `updated_at` | INTEGER | NOT NULL | | 最近更新时间戳（秒） |

索引：
```sql
CREATE INDEX idx_market_goods_category ON market_goods(category, sub_category);
CREATE INDEX idx_market_goods_enabled ON market_goods(enabled);
```

迁移建议：
- 将现有 `goods.json` 解析后批量写入 `market_goods`；
- 保留 `meta_json` 字段以兼容未来商品特性（如 ROI、OCR 参数等）；
- UI 侧加载商品列表时改为查询该表，支持按分类/关键字过滤。

### task_profile —— 任务配置
| 字段 | 类型 | 约束 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `task_id` | TEXT | PRIMARY KEY | | 任务唯一 ID（与业务现有 ID 对应） |
| `task_name` | TEXT | NOT NULL | `''` | 任务名称 |
| `task_type` | TEXT | NOT NULL | `''` | 任务类型，如 `auto_buy`, `multi_snipe` |
| `enabled` | INTEGER | NOT NULL | `1` | 是否启用 |
| `config_json` | TEXT | NOT NULL | `''` | 任务配置 JSON，包含目标商品、限价等 |
| `created_at` | INTEGER | NOT NULL | | 创建时间戳 |
| `updated_at` | INTEGER | NOT NULL | | 最近更新时间戳 |
| `notes` | TEXT | | `NULL` | 备注信息 |

索引：
```sql
CREATE INDEX idx_task_profile_enabled ON task_profile(enabled);
CREATE INDEX idx_task_profile_type ON task_profile(task_type);
```

迁移建议：将现有 `buy_tasks.json`、`snipe_tasks.json` 等文件合并导入 `task_profile`（可按类型区分）。

### task_run —— 任务运行实例
| 字段 | 类型 | 约束 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `run_id` | TEXT | PRIMARY KEY | | 运行实例 ID（UUID） |
| `task_id` | TEXT | NOT NULL | | 对应 `task_profile.task_id` |
| `started_at` | INTEGER | NOT NULL | | 启动时间戳 |
| `ended_at` | INTEGER | | `NULL` | 结束时间戳 |
| `status` | TEXT | NOT NULL | `'running'` | 运行状态（`running`, `completed`, `failed`, `stopped` 等） |
| `result_json` | TEXT | | `NULL` | 运行结果摘要（如错误信息、统计汇总） |
| `config_snapshot` | TEXT | NOT NULL | `''` | 执行时的配置快照 |

索引：
```sql
CREATE INDEX idx_task_run_task ON task_run(task_id, started_at);
CREATE INDEX idx_task_run_status ON task_run(status);
```

用途：记录每次任务启动与完成时间，用于追踪历史及配合购买统计。

### task_purchase_summary —— 任务购买统计
| 字段 | 类型 | 约束 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `run_id` | TEXT | NOT NULL | | 对应 `task_run.run_id` |
| `item_id` | TEXT | NOT NULL | | 商品 ID |
| `total_qty` | INTEGER | NOT NULL | | 此次运行购买数量总和 |
| `total_amount` | INTEGER | NOT NULL | | 此次运行购买金额总和 |
| `avg_price` | INTEGER | NOT NULL | | 加权平均价（四舍五入） |
| `max_price` | INTEGER | NOT NULL | | 最高购买价 |
| `min_price` | INTEGER | NOT NULL | | 最低购买价 |
| `category` | TEXT | | `NULL` | 商品分类快照 |
| `item_name` | TEXT | | `NULL` | 商品名称快照 |

主键与索引：
```sql
PRIMARY KEY (run_id, item_id);
CREATE INDEX idx_task_purchase_item ON task_purchase_summary(item_id);
```

生成方式：
- 任务运行过程中，`purchase_event` 已记录细节，可在任务结束或定时任务中按 `task_id`/`run_id` 聚合。
- 对于未结束任务，可根据实时需要写入临时统计，再在结束时补全终值。

用途：支持 UI 展示“某次任务运行累计购买情况”以及历史任务报表。

### price_event —— 原始价格事件
| 字段 | 类型 | 约束 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | | 自增主键 |
| `item_id` | TEXT | NOT NULL | | 商品唯一标识 |
| `item_name` | TEXT | NOT NULL | `''` | 记录写入时的名称快照 |
| `category` | TEXT | | | 商品分类（可为空） |
| `price` | INTEGER | NOT NULL | | 识别到的单价（单位：游戏内货币，整数） |
| `ts_epoch` | INTEGER | NOT NULL | | Unix 秒级时间戳 |
| `iso` | TEXT | NOT NULL | | 便于 UI 显示的格式化时间 |
| `source` | TEXT | | `NULL` | 来源标记（如 `ocr`, `manual`），便于扩展 |

索引：
```sql
CREATE INDEX idx_price_event_item_ts ON price_event(item_id, ts_epoch);
CREATE INDEX idx_price_event_ts ON price_event(ts_epoch);
```

### price_minutely —— 分钟聚合
| 字段 | 类型 | 约束 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `item_id` | TEXT | NOT NULL | | 商品唯一标识 |
| `bucket_minute` | INTEGER | NOT NULL | | 分钟桶时间戳（`ts_epoch // 60`） |
| `min_price` | INTEGER | NOT NULL | | 该分钟内的最低价 |
| `max_price` | INTEGER | NOT NULL | | 该分钟内的最高价 |
| `avg_price` | INTEGER | NOT NULL | | 四舍五入后的平均价 |
| `sample_count` | INTEGER | NOT NULL | | 该分钟累计事件条数 |
| `item_name` | TEXT | | | 最新名称快照 |
| `category` | TEXT | | | 最新分类快照 |
| `updated_at` | INTEGER | NOT NULL | | 最近一次更新的秒级时间戳 |

主键与索引：
```sql
PRIMARY KEY (item_id, bucket_minute);
CREATE INDEX idx_price_minutely_bucket ON price_minutely(bucket_minute);
```

UPSERT 逻辑（示例）：
```sql
INSERT INTO price_minutely (
    item_id, bucket_minute, min_price, max_price, avg_price, sample_count,
    item_name, category, updated_at
) VALUES (
    :item_id, :bucket_minute, :price, :price, :price, 1,
    :item_name, :category, :ts_epoch
) ON CONFLICT(item_id, bucket_minute) DO UPDATE SET
    min_price = MIN(price_minutely.min_price, excluded.min_price),
    max_price = MAX(price_minutely.max_price, excluded.max_price),
    avg_price = ROUND(
        (price_minutely.avg_price * price_minutely.sample_count + excluded.avg_price)
        / (price_minutely.sample_count + excluded.sample_count)
    ),
    sample_count = price_minutely.sample_count + excluded.sample_count,
    item_name = excluded.item_name,
    category = COALESCE(excluded.category, price_minutely.category),
    updated_at = excluded.updated_at;
```

### price_hourly —— 小时聚合
| 字段 | 类型 | 约束 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `item_id` | TEXT | NOT NULL | | 商品唯一标识 |
| `bucket_hour` | INTEGER | NOT NULL | | 小时桶时间戳（`ts_epoch // 3600`） |
| `min_price` | INTEGER | NOT NULL | | 该小时内最低价 |
| `max_price` | INTEGER | NOT NULL | | 该小时内最高价 |
| `avg_price` | INTEGER | NOT NULL | | 四舍五入后的平均价 |
| `sample_count` | INTEGER | NOT NULL | | 该小时累积分钟数 * 采样数量 |
| `item_name` | TEXT | | | 最新名称快照 |
| `category` | TEXT | | | 最新分类快照 |
| `aggregated_at` | INTEGER | NOT NULL | | 归档时间戳 |

主键与索引：
```sql
PRIMARY KEY (item_id, bucket_hour);
CREATE INDEX idx_price_hourly_bucket ON price_hourly(bucket_hour);
```

归档 SQL（示例）：
```sql
INSERT INTO price_hourly (
    item_id, bucket_hour, min_price, max_price, avg_price,
    sample_count, item_name, category, aggregated_at
)
SELECT
    item_id,
    bucket_minute / 60 AS bucket_hour,
    MIN(min_price),
    MAX(max_price),
    ROUND(SUM(avg_price * sample_count) / SUM(sample_count)),
    SUM(sample_count),
    MAX(item_name),
    MAX(category),
    :now_epoch
FROM price_minutely
WHERE bucket_minute < :minute_threshold
GROUP BY item_id, bucket_hour
ON CONFLICT(item_id, bucket_hour) DO UPDATE SET
    min_price = MIN(price_hourly.min_price, excluded.min_price),
    max_price = MAX(price_hourly.max_price, excluded.max_price),
    avg_price = ROUND(
        (price_hourly.avg_price * price_hourly.sample_count + excluded.avg_price * excluded.sample_count)
        / (price_hourly.sample_count + excluded.sample_count)
    ),
    sample_count = price_hourly.sample_count + excluded.sample_count,
    item_name = excluded.item_name,
    category = COALESCE(excluded.category, price_hourly.category),
    aggregated_at = excluded.aggregated_at;
```

### purchase_event —— 购买记录
| 字段 | 类型 | 约束 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | | 自增主键 |
| `item_id` | TEXT | NOT NULL | | 商品唯一标识 |
| `item_name` | TEXT | NOT NULL | `''` | 名称快照 |
| `category` | TEXT | | | 分类信息 |
| `price` | INTEGER | NOT NULL | | 单件价格 |
| `qty` | INTEGER | NOT NULL | | 数量 |
| `amount` | INTEGER | NOT NULL | | 总价（`price * qty`） |
| `ts_epoch` | INTEGER | NOT NULL | | Unix 秒 |
| `iso` | TEXT | NOT NULL | | 格式化时间 |
| `task_id` | TEXT | | | 任务标识 |
| `task_name` | TEXT | | | 任务名称 |
| `used_max` | INTEGER | | `NULL` | 是否使用最大购买按钮，0/1 |
| `meta_json` | TEXT | | `NULL` | 预留扩展（如失败原因） |

索引：
```sql
CREATE INDEX idx_purchase_item_ts ON purchase_event(item_id, ts_epoch);
CREATE INDEX idx_purchase_ts ON purchase_event(ts_epoch);
```

### app_config —— 运行配置
| 字段 | 类型 | 约束 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `section` | TEXT | NOT NULL | | 配置分组（如 `paths`, `umi_ocr`） |
| `key` | TEXT | NOT NULL | | 配置键 |
| `value_json` | TEXT | NOT NULL | | JSON 字符串，统一存储复杂结构 |
| `updated_at` | INTEGER | NOT NULL | | 最近更新时间 |
| `version` | INTEGER | NOT NULL | `1` | 乐观锁版本号 |

主键：
```sql
PRIMARY KEY(section, key);
```

### config_meta —— 元信息
| 字段 | 类型 | 约束 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY CHECK (id = 1) | 1 | 唯一行 |
| `schema_version` | INTEGER | NOT NULL | | 数据库迁移版本 |
| `created_at` | INTEGER | NOT NULL | | 创建时间 |
| `updated_at` | INTEGER | NOT NULL | | 最近迁移时间 |
| `notes` | TEXT | | | 备注或迁移历史 |

## 定时任务与数据流程
1. **实时写入路径**
   - OCR 捕获价格后，同步执行两步事务：插入 `price_event`；对 `price_minutely` 进行 UPSERT。
   - `_MIN_AGG` 之类的内存缓存可移除，直接依赖数据库聚合。
2. **分钟刷新任务**（每分钟触发一次）
   - 清理 `price_minutely` 中当分钟以外但条数为 0 的异常记录（防止中断留下的空桶）。
   - 可选：写入监控指标，例如每分钟入库数量到 `app_config` 或日志。
3. **小时归档任务**（每 30 分钟或每小时执行）
   - 将 `bucket_minute < now - 7 天` 的数据汇总到 `price_hourly`（见前文 SQL）。
   - 成功后删除这些分钟记录：`DELETE FROM price_minutely WHERE bucket_minute < :threshold;`
4. **原始事件清理任务**（每日执行）
   - 删除 7 天前的 `price_event`：`DELETE FROM price_event WHERE ts_epoch < :cut;`
   - 删除已无引用的 `purchase_event` 旧记录（若需要 retention，可保留全量或引入配置）。
5. **VACUUM / 统计更新**
   - 每月或手动执行 `VACUUM` 与 `ANALYZE`，可在应用设置面板提供按钮。

任务执行方式建议：
- 在主应用启动时创建后台线程 `HistoryMaintenanceThread`，循环睡眠+执行；
- 使用同一 SQLite 连接或连接池，确保所有任务在 WAL 模式下运行；
- 关键 SQL 均需包裹事务，失败时写入日志并在下次循环重试。

## 查询策略
- 最近 7 天视图：直接从 `price_minutely` 读取，按 `bucket_minute` 排序并格式化。
- 7 天以外：优先查询 `price_hourly`；若请求跨越 7 天边界，拼接两段结果。
- 购买明细：`purchase_event` 支持按商品、时间范围、任务过滤，UI 使用倒序分页加载。
- 统计 API 可使用视图封装：
  ```sql
  CREATE VIEW v_price_minutely AS
  SELECT *, bucket_minute * 60 AS ts_epoch FROM price_minutely;
  ```

## 配置读写流程
- 替换现有 `config.json` 读取逻辑，优先查询 `app_config`；首次运行若表为空，导入 JSON。
- `value_json` 统一存储 JSON 字符串，应用层负责解析为 `dict`。
- 使用 `version` 字段实现乐观锁，更新时：`UPDATE ... SET value_json=?, updated_at=?, version=version+1 WHERE section=? AND key=? AND version=?;`
- `config_meta.schema_version` 用于 Alembic 风格的迁移控制，后续升级时增加 migration 脚本。

## 迁移与回滚建议
1. 启动迁移脚本，将 `history_paths` 目录下的 JSONL 按时间顺序导入对应表；导入完成后重命名旧文件防止重复写入。
2. 启动后对比 UI 与旧数据一致性，确认 7 天窗口内的分钟统计与小时统计均正确。
3. 若需回滚，可保留原 JSONL 文件，并提供导出脚本从 SQLite 重新生成 JSONL。

## 后续工作清单
- [ ] 新增 `sqlite_history.py`（示例命名）封装插入、聚合、查询 API。
- [ ] 在 `TaskRunner` 初始化时打开 SQLite 数据库，并传递给历史写入模块。
- [ ] 实现后台维护线程，完成分钟清理、小时归档与事件清理。
- [ ] 替换 UI 层对 `history_store` 的依赖，使其读写 SQLite。
- [ ] 将现有配置加载与保存逻辑迁移到 `app_config`。
- [ ] 将 `goods.json` 数据导入 `market_goods`，并调整 UI/业务读取逻辑。
- [ ] 将 `buy_tasks.json`、`snipe_tasks.json` 等导入 `task_profile`，并在任务运行时写入 `task_run`/`task_purchase_summary`。
- [ ] 编写迁移脚本，将 `price_history*.jsonl`、`purchase_history.jsonl` 导入新库。
- [ ] 添加基础自检（数据库文件不存在时自动初始化，并写入 `config_meta`）。

---
本设计文档描述了 SQLite 化后的数据模型、定时任务与运维流程，可作为后续实现与评审的依据。若需求变更（例如保留周期或统计粒度调整），仅需更新相关定时任务和表结构即可。
