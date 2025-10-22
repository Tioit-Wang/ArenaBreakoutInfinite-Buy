# 购买任务执行逻辑设计稿（最新）

本设计稿整合当前全部规范，明确：
- 进入任何任务前，必须先“确保游戏就绪”。若启动流程仍失败（超时或始终未识别到“市场按钮”），立即终止全部任务。
- 轮流执行：每个任务各自设置执行时长，按任务顺序周而复始地执行各自的时长片段（示例：a=10min, b=20min → 第35分钟执行a，第45分钟执行b）。
- 时间区间执行：读取任务时间窗口，按当前时间选择应该执行的任务。
- 统一购买执行：严格基于“现有模板、物品市场(goods.json)、坐标/ROI/价格识别配置”等字段，全部以“匹配模板并点击/检测/读取”的表述。
- 控制：支持开始/暂停/终止（轮流执行下“终止”清空本任务已执行时长；暂停仅冻结）。
- 软重启=重启游戏（而非重启本软件）。轮流执行与时间区间模式中均按计划定期重启游戏。

---

## 1. 模板 / 物品 / 坐标 / 区域（来自配置）

- 模板 templates（键 → 路径，置信度见 config.json）：
  - `btn_launch` → `images/btn_launch.png`（启动按钮）
  - `btn_home` → `images/btn_home.png`（可选：回到主页）
  - `btn_market` → `images/btn_market.png`（市场按钮）
  - `input_search` → `images/input_search.png`（市场搜索输入框）
  - `btn_search` → `images/btn_search.png`（搜索按钮）
  - `btn_buy` → `images/btn_buy.png`（购买/确认按钮）
  - `buy_ok` → `images/buy_ok.png`（购买成功提示）
  - `buy_fail` → `images/buy_fail.png`（购买失败/售罄/余额不足提示）
  - `btn_close` → `images/btn_close.png`（关闭详情/对话框）
  - `btn_refresh` → `images/btn_refresh.png`（列表刷新，可选）
  - `btn_back` → `images/btn_back.png`（返回，可选）
  - `btn_max` → `images/btn_max.png`（Max 数量按钮）
- 物品市场 goods.json：
  - 条目字段（至少）：`id`、`name`、`search_name`、`image_path`、`big_category`、`sub_category`、`image_path`。
  - 任务与物品关联：任务应包含 `item_id`，用于解析：
    - 搜索关键字使用 goods.`search_name`；若为空→goods.`name`→任务.`item_name`。
    - 详情进入模板使用 goods.`image_path`（不再依赖坐标）。
- 坐标 points（仍可用于数量直输等）：
  - `quantity_input`：数量输入框坐标（x,y）。
- 区域 rects / ROI 与 OCR：
  - `currency_area.template`：货币图标模板（右侧 `price_width` 为价格区域，高度等于模板高度；支持 `ocr_engine` 与 `scale`）。
  - `price_roi.*`、`rects.price_region`：历史 ROI 配置（本设计以 currency_area 为主，作为可选回退）。
  - Umi-OCR：`umi_ocr.base_url`、`umi_ocr.timeout_sec`。

---

## 2. 参数说明（任务 / 商品 / 配置）

- 任务（buy_tasks.json.tasks）：
  - `id`：任务唯一标识；`order`：排序（越小越先）；`enabled`：启用
  - `item_id`：关联 goods.json 的 `id`（用于解析搜索关键字与详情模板）
  - `item_name`：物品名（用于展示/回退）
  - `price_threshold`：预设购买价阈值（基础价）
  - `price_premium_pct`：溢价百分比（>=0）
  - `restock_price`：补货价阈值（<=该价走 Max 购买）
  - `target_total`：目标累计数量（<=0 表示无限制，仅受时间/片段约束）
  - `purchased`：累计购买量（运行时更新）
  - `max_per_order`、`max_button_qty`、`default_buy_qty`
  - 轮流：`duration_min`（每次轮到时的执行时长，分钟）
  - 时间区间：`time_start`、`time_end`（HH:MM；支持跨天；空值=总是匹配）
- 商品（config.json.purchase_items，用于 UI/调试或非任务模式）：与任务字段相似。
- 全局配置（config.json）：
  - `game.exe_path`、`launch_args`、`startup_timeout_sec`（默认 120s）
  - `templates`、`points`、`rects`、`price_roi`、`currency_area`、`umi_ocr`
  - `step_delays.default`（细步延时，默认 0.01s）、`restart_every_min`（定期重启游戏的周期，分钟；<=0 表示关闭）

---

## 3. 启动与就绪检测（进入任务前）

- 优先匹配 `btn_market`：存在→就绪。
- 否则执行启动流程（任一步失败视为启动失败）：
  - 若配置 `game.exe_path`：启动进程（工作目录=可执行文件所在目录，传 `launch_args`）。
  - 在 `startup_timeout_sec` 内循环：
    - 匹配 `btn_launch` → 若找到则点击一次；
    - 匹配 `btn_market` → 若找到则判定就绪并进入任务；
  - 超时仍未识别到 `btn_market` → 终止全部任务并记录错误。

日志示例：
- 【08:00:00】【全局】【-】：开始启动，超时点 08:02:00
- 【08:01:05】【全局】【-】：已点击启动按钮
- 【08:01:12】【全局】【-】：检测到市场按钮，进入任务
- 【08:02:00】【全局】【-】：启动失败（超时），终止全部任务

---

## 4. 统一购买执行逻辑（基于模板/字段）

1) 进入市场与检索：
- 匹配 `btn_market` 并点击（必要时先匹配 `btn_home` 返回首页）。
- 匹配 `input_search` 并点击，输入搜索关键字：
  - 通过任务 `item_id` 在 goods.json 查得 `search_name`；若为空→goods.`name`→任务.`item_name`。
- 匹配 `btn_search` 并点击，等待列表刷新（50–150ms）。

2) 进入详情（物品模板匹配）：
- 通过 `item_id` 取 goods.`image_path` 作为“物品模板”；
- 匹配该模板并点击中心进入详情（不再使用 `points.first_item` 坐标）；
- 未匹配到：记录错误并可先匹配 `btn_refresh` 刷新后重试。

3) 读取价格（“货币价格区域”逻辑）：
- 全屏匹配 `currency_area.template`，按分数去重取最多两个候选；
- 候选按 Y 升序排序：顶部视为“单价(平均价)”，底部视为“合计价格”；
- 对每个候选，在其右侧裁切 ROI：宽度=`currency_area.price_width`，高度=模板高度；
- OCR 引擎：优先 `currency_area.ocr_engine`，否则回退 `avg_price_area.ocr_engine`（默认 umi）；
- 对 ROI OCR 并解析数值（支持 K/M 缩写）；以“顶部 ROI”结果为单价（若仅有一个 ROI，则以其为准）；
- 未匹配或 OCR 为空：记录失败并关闭详情，返回检索流程。

4) 价格阈值（仅“预设价 + 溢价”）：
- `allowed_max = floor(price_threshold * (1 + max(0, price_premium_pct)/100))`；
- `unit_price <= allowed_max` → 进入下单；否则关闭详情返回检索。

5) 数量与补货：
- `remain = max(0, target_total - purchased)`；
- 补货：若 `restock_price > 0` 且 `unit_price <= restock_price` →
  - 匹配 `btn_max` 设为最大；`q = min(max_button_qty, max_per_order, remain)`；
- 常规：`q = min(default_buy_qty, max_per_order, max_button_qty, remain)`；必要时点击 `points.quantity_input` 直输数量；
- `q < 1` → 关闭详情并返回检索。

6) 下单与结果：
- 匹配 `btn_buy` 点击提交；300–600ms 内轮询：
  - 匹配 `buy_ok` → 成功：`purchased += q`，移开光标到空白处后，匹配 `btn_close` 关闭详情；
  - 匹配 `buy_fail` → 失败：记录原因并关闭详情；
  - 均未匹配 → 视为未知，宽限一次复查；仍无 → 记录“结果未知”并关闭详情。

7) 节奏：
- 细步延时遵循 `step_delays.default`（默认 0.01s）；模板/OCR 步建议 10–50ms 等待。

日志示例：
- 【08:12:11】【5.56 M995】【24/120】：匹配 btn_market → input_search(goods.search_name) → btn_search 成功
- 【08:12:12】【5.56 M995】【24/120】：匹配 goods.image_path 模板并进入详情
- 【08:12:13】【5.56 M995】【24/120】：Currency 区域OCR 单价=195，阈值≤200(+0%)，进入下单
- 【08:12:14】【5.56 M995】【36/120】：购买成功(+12)，已关闭详情

---

## 5. 软重启（重启游戏）的统一规则

- 定义：`restart_every_min > 0` 时，按该周期触发“重启游戏”。
- 触发检查点：在购买循环的安全间隙（完成一次详情关闭后）进行检查；若到期，则立即按如下流程执行：
  1) 记录日志：到达重启周期；
  2) 结束当前未提交的操作（确保已关闭详情窗口）；
  3) 退出游戏（可选顺序：匹配 `btn_back`/系统菜单关闭；必要时使用进程终止）；
  4) 执行“启动与就绪检测”（第 3 节），直至识别到 `btn_market`；
  5) 恢复原任务：
     - 轮流执行：恢复当前任务片段（见下述时间处理）；
     - 时间区间：若窗口仍有效则继续；若窗口已越界则按时间调度切换到当前有效任务。
- 计时处理：
  - 轮流执行：重启期间“暂停片段计时”（`executed_ms` 不累计），重启完成后继续计时；
  - 时间区间：窗口时间不暂停（自然流逝）。

---

## 6. 轮流执行（每任务自定时长，顺序循环）

- 准备：过滤 `enabled==true`，按 `order` 升序；初始化运行态：`status`、`executed_ms`（暂停保留；终止清零）、`purchased`；
- 执行：
  - 为当前任务设 `start_ts = now`，`end_ts = now + duration_min*60s`；
  - 运行“统一购买执行逻辑”；在每次详情关闭后检查是否到达重启周期→若到期则走“软重启（重启游戏）”；
  - 满足任一条件退出片段：`now >= end_ts`、收到“暂停/终止”、达标或需要切换；
  - `executed_ms += 片段实际毫秒`（不包含重启耗时）；
  - 片段结束后切到下一个启用任务；队尾回到队首，周而复始；
  - 示例：a=10、b=20： [0,10) a；[10,30) b；[30,40) a；[40,60) b； → 35min 执行 a，45min 执行 b（忽略重启开销）。
- 控制：
  - 暂停：`status=paused`，立即停止循环；恢复继续累计；
  - 终止：`status=terminated`，清零 `executed_ms`；`purchased` 不变；再次开始从完整 `duration_min` 计时；
  - 达标：`target_total > 0` 且已达成 → 后续轮次跳过。

---

## 7. 时间区间执行（按当前时间选择 + 定期重启游戏）

- 窗口解析：`time_start`、`time_end`（HH:MM；空值=总是匹配；`end < start`=跨天）。
- 选择：以当前时间选择第一个“窗口包含当前时刻”的任务（按 `order` 升序；重叠取优先）。
- 驻留：
  - 进入所选任务后计算“窗口结束时间戳”，记录日志；
  - 驻留执行“统一购买执行逻辑”；在每次详情关闭后检查重启周期→若到期则立即执行“软重启（重启游戏）”；
  - 若窗口结束或出现更高优先的重叠窗口，则停止当前任务并重新选择。
- 控制：暂停冻结但窗口仍流逝；终止=本窗口禁用（待下个窗口再进入）。

---

## 8. 任务管理与日志

- 列表：启用、物品名、目标/累计、模式字段（duration 或 time 窗口）、状态（idle/running/paused/terminated）、操作（开始/暂停/终止）。
- 日志格式：统一为“【时间】【物品】【累计/目标】：message”。
- 常见粘贴点：
  - 轮流：进入任务片段时粘贴“开始/结束/时长”；
  - 时间区间：切入任务时粘贴“窗口结束时间”；
  - 启动/重启：粘贴开始/完成/超时；
  - 购买：单价/阈值/数量/累计/结果；
  - 失败：模板缺失/OCR失败/导航失败等。

---

## 9. 流程图（Mermaid）

### 9.1 启动/就绪
```mermaid
flowchart TD
  A[开始] --> B{匹配 btn_market ?}
  B -- 是 --> C[游戏就绪]
  B -- 否 --> D[启动 exe_path(含 args)]
  D --> E[超时内循环]
  E -->|匹配 btn_launch 并点击| E
  E -->|匹配到 btn_market| C
  E -->|超时未见市场| Z[终止全部任务]
```

### 9.2 统一购买 + 周期重启
```mermaid
flowchart TD
  M[匹配 btn_market] --> Q[匹配 input_search 输入 goods.search_name]
  Q --> S[匹配 btn_search]
  S --> O[匹配 goods.image_path 并点击进入详情]
  O --> R[匹配 currency_area.template, 取右侧 ROI 并 OCR]
  R --> J{unit_price 合法?}
  J -- 否 --> X[匹配 btn_close, 重试]
  J -- 是 --> T{<= restock_price?}
  T -- 是 --> L[匹配 btn_max; q=min(max_button_qty,max_per_order,remain)]
  T -- 否 --> N[q=min(default,max_button_qty,max_per_order,remain)]
  L --> P[匹配 btn_buy 提交]
  N --> P
  P --> U{buy_ok / buy_fail / 未知}
  U -- buy_ok --> C1[累计 purchased+=q, 关闭详情]
  U -- buy_fail --> C2[记录失败, 关闭详情]
  U -- 未知 --> C3[宽限一次后关闭]
  C1 --> K{到达重启周期?}
  C2 --> K
  C3 --> K
  K -- 是 --> G[退出游戏→执行启动与就绪→恢复任务]
  K -- 否 --> M
```

### 9.3 轮流执行（自定时长）
```mermaid
flowchart TD
  S[按 order 排序/过滤] --> A[任务 i]
  A --> B[就绪检查失败?]
  B -- 是 --> Z[终止全部]
  B -- 否 --> C[记录开始/结束(基于 duration_min)]
  C --> D[统一购买(含周期重启检查)]
  D --> E{片段结束 / 暂停 / 终止 / 达标}
  E -- 是 --> F[下一个启用任务]
  F --> A
  E -- 否 --> D
```

### 9.4 时间区间执行（含周期重启）
```mermaid
flowchart TD
  S[解析所有任务时间窗] --> A{当前时刻匹配?}
  A -- 无 --> W[空闲轮询(1-3s)] --> S
  A -- 有 --> B[就绪检查失败?]
  B -- 是 --> Z[终止全部]
  B -- 否 --> C[记录窗口结束时间]
  C --> D[统一购买(含周期重启检查)]
  D --> E{窗口结束 / 更高优先切换}
  E -- 是 --> S
  E -- 否 --> D
```

---

本稿为“最新”整合版：
- 详情进入使用 goods.image_path 模板，不再依赖坐标；
- 搜索关键字使用 goods.search_name（由任务 item_id 解析）；
- 价格读取采用 currency_area（货币价格区域）逻辑；
- 软重启=重启游戏，并在两种模式中按周期执行；
- 轮流=每任务自定时长、顺序循环；时间区间=按当前时间选择驻留任务；
- 日志格式统一，含启动/重启/切换/购买等关键时间粘贴。