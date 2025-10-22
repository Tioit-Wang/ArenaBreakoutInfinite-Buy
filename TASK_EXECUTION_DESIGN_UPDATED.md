% 购买任务执行逻辑设计稿（更新版）

本设计稿根据“轮流执行按任务自定时长循环、时间区间按当前时间选择、统一购买逻辑以模板与字段为准”的要求重写：
- 进入任何任务前，必须先“确保游戏就绪”。若启动流程仍失败（启动超时或始终未识别到“市场按钮”），立即终止全部任务。
- 轮流执行：每个任务有自己的执行时长（分钟），按照任务顺序循环周而复始地执行各自时长的片段（而非给每个任务统一的固定时片）。
- 时间区间执行：读取任务的时间窗口，按当前时间判断应该执行哪个任务。
- 统一的购买执行逻辑：严格基于“现有模板、坐标点、ROI 区域与配置字段”描述；避免抽象词，明确“匹配模板并点击/检测/读取”。
- 新增/明确任务控制：开始、暂停、终止（轮流执行下“终止”清空已执行时长；暂停仅冻结）。

---

## 1. 模板、坐标与区域（来自 config.json）

- 模板 templates（键 → 路径，置信度在 config 中）：
  - `btn_launch` → `images/btn_launch.png`（启动按钮）
  - `btn_home` → `images/btn_home.png`（主页/返回主页，非必须）
  - `btn_market` → `images/btn_market.png`（市场按钮）
  - `input_search` → `images/input_search.png`（市场搜索输入框）
  - `btn_search` → `images/btn_search.png`（搜索按钮）
  - `btn_buy` → `images/btn_buy.png`（购买/确认按钮）
  - `buy_ok` → `images/buy_ok.png`（购买成功提示）
  - `buy_fail` → `images/buy_fail.png`（购买失败/售罄/余额不足提示）
  - `btn_close` → `images/btn_close.png`（关闭详情/对话框）
  - `btn_refresh` → `images/btn_refresh.png`（列表刷新，按需）
  - `btn_back` → `images/btn_back.png`（返回，按需）
  - `btn_max` → `images/btn_max.png`（Max 数量按钮）
- 坐标 points：
  - `first_item`：点击搜索结果第一项的坐标（x,y）
  - `quantity_input`：数量输入框坐标（x,y），用于无法通过按钮设置数量时直接输入
- 区域 rects：
  - `price_region`：价格 OCR 区域矩形（x1,y1,x2,y2）
- ROI 与 OCR 相关：
  - `price_roi.top_template` / `price_roi.bottom_template`（上/下边界模板）
  - `currency_area.template`（货币区域模板，可用于定位/校验）
  - `umi_ocr.base_url`（Umi-OCR 服务地址）、`umi_ocr.timeout_sec`（超时）

---

## 2. 参数说明（任务 / 商品 / 配置）

- 任务（buy_tasks.json.tasks 内每条记录，按本设计参与调度）
  - `id`：任务唯一标识
  - `order`：排序顺序（数值越小优先级越高）
  - `enabled`：是否启用
  - `item_name`：物品名（用于搜索）
  - `price_threshold`：预设购买价格阈值（基础价）
  - `price_premium_pct`：可接受溢价百分比（>=0）
  - `restock_price`：补货价阈值（<=该价时走 Max 购买）
  - `target_total`：目标累计购买量（<=0 表示无限制，仅受时间约束）
  - `purchased`：已累计购买量（运行时更新）
  - `max_per_order`：单次下单上限（按钮/库存/规则限制）
  - `max_button_qty`：Max 按钮最大可设数量（如 120）
  - `default_buy_qty`：常规模式下默认尝试的购买数量
  - 轮流：`duration_min`（该任务每次轮到时的执行时长，单位分钟）
  - 时间区间：`time_start`、`time_end`（HH:MM；支持跨天，空值视为“总是匹配”）

- 商品（config.json.purchase_items 列表项，供 UI/调试或非任务模式使用）
  - 字段与任务相近：`item_name`、`price_threshold`、`price_premium_pct`、`restock_price`、
    `target_total`、`purchased`、`max_per_order`、`default_buy_qty`、`max_button_qty`、`id` 等

- 配置（config.json）
  - `game.exe_path`、`launch_args`、`startup_timeout_sec`：启动器与启动超时（默认 120s）
  - `templates`：见第 1 节
  - `points`、`rects`、`price_roi`、`currency_area`、`umi_ocr`：见第 1 节
  - 顶层运行：`step_delays.default`（细步延时，默认 0.01s）、`restart_every_min`（软重启间隔，可选）

---

## 3. 启动与就绪检测（进入任务前）

- 先匹配模板 `btn_market`：
  - 若找到 → 游戏就绪（进入任务）
- 否则执行启动流程（任一步失败视为启动失败）：
  - 如配置了 `game.exe_path` → 启动进程（工作目录=可执行文件所在目录；传入 `launch_args`）
  - 在 `startup_timeout_sec` 内循环：
    - 尝试匹配 `btn_launch` → 若找到则点击一次
    - 尝试匹配 `btn_market` → 若找到则判定就绪
  - 超时仍未识别到 `btn_market` → 终止全部任务并记录错误

日志粘贴示例：
- 【08:00:00】【全局】【-】：开始启动，超时点 08:02:00
- 【08:01:05】【全局】【-】：已点击启动按钮
- 【08:01:12】【全局】【-】：检测到市场按钮，进入任务
- 【08:02:00】【全局】【-】：启动失败（超时），终止全部任务

---

## 4. 统一的购买执行逻辑（严格基于模板与字段）

以下步骤在两种模式中完全一致，差异仅在“何时进入该逻辑、驻留多久与何时切换”。

1) 进入市场与检索
- 匹配模板 `btn_market` 并点击（必要时先匹配 `btn_home` 返回首页）。
- 匹配模板 `input_search` 并点击，向搜索框输入 `item_name`（任务字段）。
- 匹配模板 `btn_search` 并点击，等待列表刷新（50–150ms）。

2) 进入详情
- 点击坐标 `points.first_item` 打开第一个商品详情（或在没有坐标时备用其他模板方案）。

3) 读取价格（OCR）
- 如配置了 `rects.price_region`：截取该区域进行 OCR；
- 或使用 `price_roi.top_template` + `price_roi.bottom_template` 定位 ROI；
- 可选匹配 `currency_area.template` 校验货币区域位置；
- OCR 使用 `umi_ocr`（base_url/timeout 参照配置），允许 K 缩写（1.2K → 1200）。

4) 价格阈值判定（仅“预设价+溢价”，无平均模式）
- 计算 `allowed_max = floor(price_threshold * (1 + max(0, price_premium_pct)/100))`。
- 若 `unit_price <= allowed_max` → 进入下单流程；否则关闭详情（匹配 `btn_close`）并返回步骤 1。

5) 数量决策
- 计算当前任务剩余需求 `remain = max(0, target_total - purchased)`；
- 若 `restock_price > 0` 且 `unit_price <= restock_price` → 补货分支：
  - 匹配 `btn_max` 并点击设为最大；下单数量 `q = min(max_button_qty, max_per_order, remain)`；
- 否则常规分支：
  - 以 `default_buy_qty` 为起点，`q = min(default_buy_qty, max_per_order, max_button_qty, remain)`；
  - 如需直接输入数量，点击 `points.quantity_input`，输入期望数量。
- 若 `q < 1` → 关闭详情并返回步骤 1。

6) 触发购买与结果判定
- 匹配 `btn_buy` 并点击提交订单。
- 在 300–600ms 内轮询结果：
  - 匹配 `buy_ok` → 成功：`purchased += q`，移动光标到界面空白处后，匹配 `btn_close` 关闭详情；
  - 匹配 `buy_fail` → 失败：记录失败原因，匹配 `btn_close` 关闭详情；
  - 都未匹配到 → 视为未知，宽限一次重查；仍无 → 记录“结果未知”并关闭详情。

7) 节奏建议
- 细步延时遵循 `step_delays.default`（默认 0.01s）；
- ROI/模板检测步等待建议 10–50ms/步；
- `target_total <= 0` 视为“无限目标”，仅受调度驻留时间约束。

日志粘贴示例：
- 【08:12:11】【5.56 M995】【24/120】：匹配 btn_market → input_search → btn_search 成功
- 【08:12:12】【5.56 M995】【24/120】：OCR 单价=195，阈值≤200(+0%)，进入下单
- 【08:12:13】【5.56 M995】【36/120】：购买成功(+12)，已关闭详情
- 【08:12:15】【5.56 M995】【36/120】：购买失败(售罄)，已关闭详情

---

## 5. 轮流执行（每任务自定时长，顺序循环）

- 任务准备：
  - 过滤 `enabled==true`，按 `order` 升序排序；
  - 为每个任务维护运行态：`status`（idle/running/paused/terminated），`executed_ms`（本轮片段累计毫秒，暂停保留；“终止”清零），`purchased`（累计）。

- 执行与循环：
  - 从第一个任务开始，顺序执行任务片段：
    - 片段开始：记录 `start_ts = now`，`end_ts = now + duration_min*60s`；
    - 进入“统一购买执行逻辑”，直至 `now >= end_ts`、收到“暂停/终止”、或达到目标量；
    - `executed_ms += 片段实际运行毫秒`；
    - 片段结束后无论是否达标，立即切换到下一个启用任务；
  - 到达队尾后回到队首，继续下一轮；
  - 示例：任务 a=10min，b=20min；从 t=0 开始：
    - [0,10) 执行 a；[10,30) 执行 b；[30,40) 执行 a；[40,60) 执行 b；
    - 因此 t=35min 时执行 a，t=45min 时执行 b（满足“周而复始”）。

- 控制：
  - 暂停：将当前任务 `status=paused`，立即停止其执行循环；再次“开始”从未用尽的时段继续累计；
  - 终止：`status=terminated` 并清零该任务的 `executed_ms`；`purchased` 不变；再次“开始”从完整的 `duration_min` 重新计时；
  - 达成目标后：若 `target_total > 0` 且已达成 → 该任务在后续轮次中跳过（除非用户手动重置）。

- 软重启（可选）：
  - 每满 `restart_every_min` 分钟对后台执行器做一次软重启，避免状态漂移；不改变当前片段计时与任务状态。

- 日志粘贴：
  - 【08:12:04】【5.56 M995】【24/120】：进入任务（轮流）→ 开始:08:12:04 结束:08:22:04 时长:10m
  - 【08:22:04】【5.56 M995】【36/120】：任务片段结束（时间用尽）

---

## 6. 时间区间执行（按当前时间选择）

- 窗口解析：
  - 每个任务具有 `time_start`、`time_end`（HH:MM）；空值视为“总是匹配”；`end < start` 表示跨天窗口；
  - 将所有任务的窗口映射到“今天/明天”的绝对时间，得到当前有效窗口集合。

- 选择策略：
  - 以当前时间为基准，选择“当前时刻落在其窗口内”的第一个任务；如有多条重叠，按 `order` 最小优先；
  - 无匹配窗口 → 空闲轮询（建议 1–3 秒）并持续重算；

- 驻留与切换：
  - 进入所选任务后，计算该窗口的“结束时间戳”；记录日志“窗口结束 HH:MM:SS”；
  - 执行“统一购买执行逻辑”，直至窗口结束或有更高优先窗口需要切换；
  - 窗口结束/切换时停止当前任务，回到“选择策略”。

- 控制：
  - 暂停：冻结当前任务执行，窗口仍流逝；恢复需在窗口有效期内；
  - 终止：本窗口禁用（直到下个窗口才允许自动进入）；`purchased` 不变；

- 日志粘贴：
  - 【21:00:00】【R37.F】【0/—】：进入任务（时间区间）→ 窗口结束:23:30:00
  - 【23:30:00】【R37.F】【240/—】：窗口到期，停止任务并重新选择

---

## 7. 任务管理与日志

- 列表展示：启用、物品名、目标/累计、模式字段（duration 或时间窗）、状态（idle/running/paused/terminated）、操作按钮（开始/暂停/终止）。
- 日志：满足“【时间】【物品】【累计/目标】：message”。
- 操作规则：
  - 开始：
    - 轮流：切入对应任务片段（若当前片段为其他任务，则在片段结束或用户强制切换时进入）；
    - 时间区间：需处于有效窗口内，否则提示并等待窗口；
  - 暂停：冻结当前任务；
  - 终止：
    - 轮流：清零 `executed_ms`；
    - 时间区间：本窗口禁用；

---

## 8. 时序与建议值

- 启动超时：`startup_timeout_sec = 120`（可调）
- 步进延时：`step_delays.default = 0.01s`
- 识别与结果判定：ROI/模板检测 10–50ms/步；购买结果 300–600ms
- 空闲轮询：时间区间无窗口时 1–3s；启动退避时 1–5s
- 软重启：`restart_every_min = 60min`（建议）

---

## 9. 流程图（Mermaid）

### 9.1 启动/就绪流程
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

### 9.2 轮流执行（自定时长，顺序循环）
```mermaid
flowchart TD
  S[按 order 排序/过滤] --> A[任务 i]
  A --> B[就绪检查失败?]
  B -- 是 --> Z[终止全部]
  B -- 否 --> C[记录开始/结束(基于 duration_min)]
  C --> D[统一购买执行]
  D --> E{到达片段结束 / 暂停 / 终止 / 达标}
  E -- 是 --> F[下一个启用任务]
  F --> A
  E -- 否 --> D
```

### 9.3 时间区间执行（按当前时间选择）
```mermaid
flowchart TD
  S[解析所有任务时间窗] --> A{当前时刻匹配?}
  A -- 无 --> W[空闲轮询(1-3s)] --> S
  A -- 有 --> B[就绪检查失败?]
  B -- 是 --> Z[终止全部]
  B -- 否 --> C[记录窗口结束时间]
  C --> D[统一购买执行]
  D --> E{窗口结束 / 更高优先切换}
  E -- 是 --> S
  E -- 否 --> D
```

### 9.4 统一购买执行（基于模板/ROI）
```mermaid
flowchart TD
  M[匹配 btn_market 点击] --> Q[匹配 input_search 点击+输入]
  Q --> S[匹配 btn_search 点击]
  S --> O[点击 points.first_item]
  O --> R[OCR: rects.price_region 或 price_roi]
  R --> J{unit_price 合法?}
  J -- 否 --> X[匹配 btn_close, 重试]
  J -- 是 --> T{<= restock_price?}
  T -- 是 --> L[匹配 btn_max; q=min(max_button_qty,max_per_order,remain)]
  T -- 否 --> N[q=min(default,max_button_qty,max_per_order,remain)]
  L --> P[匹配 btn_buy 点击]
  N --> P
  P --> U{buy_ok / buy_fail}
  U -- buy_ok --> C1[累计 purchased+=q, 关闭详情]
  U -- buy_fail --> C2[记录失败, 关闭详情]
  U -- 未知 --> C3[宽限一次后关闭]
```

---

以上为修订版执行逻辑：
- 轮流执行明确为“每任务自定时长、按顺序循环”并给出 35/45 分钟示例；
- 时间区间执行基于当前时间选择有效任务；
- 统一购买逻辑使用现有模板/坐标/ROI 字段进行逐步描述；
- 任务/商品/配置的关键参数说明完整列出；
- 日志规范继续使用“【时间】【物品】【累计/目标】：message”。

---

## 附：物品市场（goods.json）关联与搜索规则

- 数据源：`goods.json`，每个条目包含至少：
  - `id`：物品唯一标识
  - `name`：物品名称（用于展示）
  - `search_name`：用于市场搜索框的关键字（推荐小写、可为英文/拼写简写）
  - 其它：`big_category`、`sub_category`、`image_path`、`exchangeable`、`craftable` 等

- 任务与物品关联：
  - 任务应包含 `item_id`（关联 goods.json 的 `id`）。
  - 搜索关键字取自：通过 `item_id` 在 goods.json 中找到的 `search_name`。
  - 若未找到或 `search_name` 为空，按以下回退顺序选择搜索关键字：
    1) goods.json 中该物品的 `name`
    2) 任务中的 `item_name`
  - 日志展示名称建议优先使用 goods 的 `name`；若缺失再回退到 `item_name`。

- 对统一购买执行逻辑（第 4 节）的补充说明：
  - 第 1 步“进入市场与检索”中，输入到 `input_search` 的文本为“通过 `item_id` 解析得到的 goods.search_name（或其回退值）”。
  - 两种调度模式下均采用相同的物品解析与搜索规则。

- 异常与校验：
  - 若任务缺少 `item_id` 且无法通过回退获得搜索关键字，应视为“配置错误”，直接记录错误日志并跳过该任务。
  - 建议在任务保存时做静态校验：检查 `item_id` 是否存在于 goods.json，并预览其 `search_name`。
