# task_runner.py 任务执行流程说明

本说明基于当前仓库中的 `task_runner.py` 实现，概述“任务运行器”的整体执行路径与关键步骤，分别以多级列表与流程图给出，便于理解与排查。

## 多级列表（执行路径）

1. 初始化（TaskRunner.**init**）

   - 读取配置：`load_config('config.json')`，解析 `tasks_data`、`goods.json`。
   - 构建依赖：`ScreenOps`（模板查找/点击/输入）、`Buyer`（单次购买流程）。
   - 运行模式与周期：`task_mode`（`round`/`time`）、`restart_every_min`（周期性软重启）。

2. 启动（TaskRunner.start）

   - 清理暂停/终止标志，启动后台线程执行 `_run()`。

3. 统一启动流程（Buyer.\_ensure_ready_v2 → run_launch_flow）

   - 快速路径：若屏幕已存在首页/市场标识，直接视为已就绪。
   - 配置校验：校验 `exe_path`、`templates.btn_launch`、`templates.home_indicator`（或 `market_indicator`）。
   - 启动进程：按 `exe_path` 与 `launch_args` 启动启动器进程。
   - 等待并点击启动：等待 `btn_launch` →（可配置延迟）点击。
   - 等待进入首页：等待 `home_indicator` 或 `market_indicator` 出现。

4. 主循环（TaskRunner.\_run）

   - 任务准备：复制并排序 `tasks`（按 `order`），初始化字段 `purchased/executed_ms/status`，并执行有效性校验：
     - `item_id` 必填，映射到 `goods.json` 条目。
     - 对应 `goods.search_name` 非空，`goods.image_path` 文件存在。
   - 分支执行：
     - 轮询模式（\_run_round_robin）
       - 按顺序挑选“启用+有效+未达标”的任务进入一个“片段（duration_min 分钟）”。
       - 片段开始：记录时间窗口与日志；建立搜索上下文：
         - 清理位置缓存 `buyer.clear_pos(goods.id)`；
         - 预热一次 `buyer.execute_once(skip_search=False)`（进入市场 → 搜索）。
       - 片段循环（直到片段结束或终止）：
         - 处理暂停/终止。
         - 重启检查：若到期执行 `_do_soft_restart()` 并标记需重建搜索上下文。
         - 若需重建搜索上下文：执行一次 `execute_once(skip_search=False)`。
         - 单次尝试：`execute_once(skip_search=True)`（不重复搜索，直接尝试购买）。
         - 成功则累加 `purchased` 并回调 `on_task_update`；若返回不可继续则提前结束片段。
       - 片段收尾：累计 `executed_ms`，状态置 `idle`、输出日志。
     - 时间窗口模式（\_run_time_window）
       - 在所有“启用+有效”的任务中，选择第一个当前时间命中其 `[time_start, time_end]` 窗口的任务执行。
       - 进入窗口时建立搜索上下文：`execute_once(skip_search=False)`。
       - 窗口循环（直到窗口结束或终止）：含暂停/终止处理、周期性软重启检查、必要时重建搜索上下文；单次尝试使用 `execute_once(skip_search=True)`；成功则更新 `purchased` 与回调。
       - 窗口退出时记录日志后继续择任务。

5. 单次购买（Buyer.execute_once）

   - 详情页恢复（优化）：若同时检测到 `btn_buy` 与 `btn_close`（仍在详情页），先点击 `btn_close` 关闭详情；随后尝试匹配 `goods.image_path` 商品图片以重建商品坐标；若无法匹配，认为搜索上下文已偏离，立即重新执行“进入市场并搜索”。
   - 搜索阶段（仅当 `skip_search=False`）：
     - 导航市场并聚焦搜索框：匹配 `btn_market` → `input_search`。
     - 输入关键词：`goods.search_name`，点击 `btn_search`。
     - 稳定性调整：搜索阶段的每一步操作之间增加约 1 秒等待时间（点击首页/市场、聚焦搜索框、输入内容、点击搜索等）。
   - 打开详情（带恢复）：
     - 优先使用缓存坐标（若 `skip_search=True` 更倾向缓存）尝试点击并验证详情是否打开；
     - 若失败，进行模板匹配（`goods.image_path`）；
     - 若仍失败，尝试“重新搜索一次”后再匹配。
   - 读取平均单价（\_read_avg_unit_price）：
     - 以 `btn_buy` 位置为锚点推导 ROI（平均单价区域）。
     - 按配置调用 OCR（优先 `umi`，可选 `tesseract`）。
     - 若使用 `umi` 且出现致命错误则抛 `FatalOcrError`，由上层终止任务。
   - 价格决策与数量：
     - 阈值计算：`limit = threshold + premium_pct%`；
     - `restock_price` 补货策略：
       - 类别为“弹药” → 尝试 `btn_max` 选择最大数量；
       - 其他 → 尝试点击数量输入并输入 `5`；
       - 否则默认数量为 `1`，均受 `max_per_order` 与剩余目标量约束。
     - 若 `unit_price > limit`（且 `limit>0`）则关闭详情并跳过。
   - 提交与结果：
     - 点击 `btn_buy` 提交；在 ~1.2s 内轮询 `buy_ok/buy_fail`。
     - 成功：先关闭成功遮罩；若是“补货价”则保留详情继续，否则关闭详情；返回本次购买数量。
     - 失败/未知：关闭详情，返回 `0`。
   - 返回值：`(purchased_in_this_attempt, should_continue_loop)`。

6. 控制与重启

   - `pause/resume/stop`：通过事件控制主循环；暂停时清理位置缓存以确保恢复后重新匹配更安全。
   - 周期性软重启：`_should_restart_now` + `_do_soft_restart`，尝试通过模板按钮退出并回到市场，再重建搜索上下文，并重置下一次重启时间。

7. 关键模板键（部分）
   - 启动/就绪：`btn_launch`、`home_indicator`、`market_indicator`。
   - 市场/搜索：`btn_home`、`btn_market`、`input_search`、`btn_search`。
   - 详情/购买：`btn_buy`、`btn_close`、`btn_max`、`buy_ok`、`buy_fail`。

## 流程图（Mermaid）

### 任务调度主流程

```mermaid
flowchart TD
  A[TaskRunner.start] --> B{Ensure Ready\n(run_launch_flow)}
  B -- 失败 --> E[终止]
  B -- 成功 --> C[加载/校验任务]
  C --> D{模式?}
  D -- round --> R0[轮询模式]
  D -- time  --> T0[时间窗口模式]

  subgraph 轮询模式
    R0 --> R1[选择 启用+有效+未达标 任务]
    R1 -->|无任务| R1
    R1 -->|有任务| R2[片段开始: duration_min 分钟]
    R2 --> R3[建立搜索上下文\nexecute_once(skip_search=false)]
    R3 --> R4{片段循环}
    R4 --> RS{到达重启点?}
    RS -- 是 --> RS1[软重启并重建搜索上下文]
    RS1 --> R5
    RS -- 否 --> R5{已建立搜索上下文?}
    R5 -- 否 --> R3
    R5 -- 是 --> R6[单次尝试\nexecute_once(skip_search=true)]
    R6 --> R7[累加 purchased & 通知 UI]
    R7 --> R8{should_continue?}
    R8 -- 否 --> R9[片段收尾: 更新 executed_ms/status]
    R8 -- 是 --> R4
    R9 --> R1
  end

  subgraph 时间窗口模式
    T0 --> T1[选择当前命中的任务]
    T1 -->|无命中| T1
    T1 -->|有命中| T2[进入窗口: 建立搜索上下文]
    T2 --> T3{窗口循环}
    T3 --> TS{到达重启点?}
    TS -- 是 --> TS1[软重启并重建搜索上下文]
    TS1 --> T4
    TS -- 否 --> T4{已建立搜索上下文?}
    T4 -- 否 --> T2
    T4 -- 是 --> T5[单次尝试\nexecute_once(skip_search=true)]
    T5 --> T6[累加 purchased & 通知 UI]
    T6 --> TW{仍在窗口内?}
    TW -- 否 --> T7[退出窗口日志]
    TW -- 是 --> T3
    T7 --> T1
  end
```

### 单次购买（Buyer.execute_once）

```mermaid
flowchart TD
  A[进入 execute_once] --> B{仍在详情页?\n(btn_buy+btn_close)}
  B -- 是 --> C[关闭详情]
  C --> C1{匹配商品图片?\n(goods.image_path)}
  C1 -- 是 --> C2[刷新坐标缓存]
  C1 -- 否 --> C3[进入市场并搜索\nbtn_market → input_search → btn_search]
  C2 --> D[打开详情（缓存优先→模板匹配）]
  C3 --> D
  B -- 否 --> D1{skip_search?}
  D1 -- 否 --> E[进入市场并搜索\nbtn_market → input_search → btn_search]
  D1 -- 是 --> F[跳过搜索]
  E --> G[打开详情（缓存优先→模板匹配→必要时重搜一次）]
  F --> G
  D --> G
  G -->|失败| H[记录未匹配并返回 (0, True)]
  G -->|成功| I[读取平均单价\n以 btn_buy 为锚点 + OCR]
  I -->|异常(Umi致命)| X[抛 FatalOcrError]
  I -->|失败| J[关闭详情, 返回 (0, True)]
  I -->|成功| K{阈值判定\nunit_price ≤ limit?}
  K -- 否 --> L[关闭详情, 返回 (0, True)]
  K -- 是 --> M[数量选择\n(补货价/弹药/默认)]
  M --> N[点击 btn_buy 提交]
  N --> O{轮询 buy_ok / buy_fail ≤1.2s}
  O -- 成功 --> P[关闭成功遮罩; 补货价保留详情/否则关闭]
  O -- 失败/未知 --> Q[关闭详情]
  P --> R[返回 (q, True)]
  Q --> S[返回 (0, True)]
```

—— 完 ——
