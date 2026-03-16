# AGENTS.md

## 背景

本项目是一个面向 Windows 的桌面自动化购买工具，围绕《Arena Breakout: Infinite》的市场购买流程构建，主要能力包括：

- 通过 `tkinter` 提供桌面 GUI。
- 通过 `pyautogui`、`pyscreeze`、OpenCV 做模板匹配、点击、输入与截图。
- 通过 Umi-OCR 的 HTTP 接口识别价格和文本。
- 通过 JSON/JSONL 持久化配置、商品数据、任务数据与历史价格/购买记录。
- 通过 PyInstaller 打包为 `ArenaBuyer` Windows 分发目录。

当前仓库的名称、脚本名、包名并不完全一致，协作时必须分清：

- 仓库名：`ArenaBreakoutInfinite-Buy`
- PyInstaller 产物名：`ArenaBuyer`
- `pyproject.toml` 中的项目名与脚本名：`wg1`
- 实际主包名：`super_buyer`
- 控制台入口：`wg1 = super_buyer.__main__:main`

代码事实比文档优先。当前有部分文档仍保留旧名称 `wg1` 或旧路径约定，因此推荐按以下优先级理解项目：

1. `src/super_buyer/**` 与 `src/history_store.py`
2. `pyproject.toml`
3. `ArenaBuyer.spec` / `tools/build_win.bat`
4. `README.md`
5. `docs/**`

运行时数据目录不是仓库根目录，而是 `data/`：

- 开发态：`<当前工作目录>/data`
- 打包态：`<exe 所在目录>/data`

`App._resolve_data_root()` 会把配置、默认模板播种、商品数据、任务数据都统一落到这个目录，因此不要假设根目录的 `config.json`、`goods.json`、`buy_tasks.json` 才是运行真相。

## 目录

建议先按下面的层级理解仓库：

### 根目录

- `pyproject.toml`
  - 依赖、入口脚本、`setuptools` 打包配置、资源打包声明。
- `ArenaBuyer.spec`
  - PyInstaller 目录分发配置，负责收集 `resources/images`、`resources/assets`、`resources/defaults`。
- `README.md`
  - 项目说明与运行说明，但存在旧路径名，阅读时要与源码交叉验证。
- `note.md`
  - 功能变更说明，当前记录了“定时重启游戏”相关流程。
- `tools/`
  - 构建与辅助脚本。
  - `build_win.bat` 会把 `data/images` 与 `data/config.json` 同步进包内默认资源后再打包。
- `docs/`
  - 设计文档与历史方案说明。
- `tests/`
  - 目前自动化测试主要覆盖历史记录模块。
- `data/`
  - 本地运行期数据目录，属于运行产物，不应当把其中的用户数据直接当作源码的一部分修改。
- `build/`、`dist/`、`__pycache__/`、`.mypy_cache/`、`.ruff_cache/`
  - 构建或缓存目录，默认不作为功能修改目标。

### `src/`

- `src/super_buyer/__main__.py`
  - 应用入口，最终调用 `super_buyer.ui.app.run_app()`。
- `src/super_buyer/ui/`
  - GUI 主体与标签页。
  - `app.py` 是总装入口和状态管理中心。
  - `tabs/` 是主要业务界面。
  - `widgets/` 是可复用 Tk 组件。
  - `goods_market.py` 是物品市场相关 UI 与交互。
- `src/super_buyer/core/`
  - 自动化流程核心逻辑。
  - `task_runner.py` 是主执行器之一。
  - `single_purchase_runner_v2.py` 是按设计文档重构的单商品购买流程。
  - `multi_snipe.py` 是多商品/多目标抢购逻辑。
  - `launcher.py`、`logging.py`、`models.py`、`exceptions.py` 提供基础能力。
- `src/super_buyer/services/`
  - 偏底层服务。
  - `screen_ops.py` 处理屏幕操作。
  - `ocr.py` 封装 Umi-OCR HTTP 调用。
  - `history.py` 负责历史数据写入。
  - `font_loader.py`、`compat.py` 负责兼容和字体支持。
- `src/super_buyer/config/`
  - 默认配置、加载、迁移逻辑。
  - `loader.py` 中的 `ConfigPaths` 是路径约定核心。
- `src/super_buyer/resources/`
  - 包内静态资源。
  - `images/` 是模板图像。
  - `assets/` 是字体等资源。
  - `defaults/` 是默认 `config.json` / `goods.json`。
- `src/history_store.py`
  - 顶层模块，不在 `super_buyer` 包内。
  - 负责 UI 侧历史查询、聚合补齐、清理逻辑。
  - `tests/test_history_store.py` 当前直接覆盖这里的行为。

### `docs/`

- `单商品购买流程设计指导方案.md`
  - `single_purchase_runner_v2.py` 的直接设计背景。
- `单商品购买流程_方案A.md`
  - 旧方案参考。
- `sqlite_storage_design.md`
  - 未来从 JSONL 迁移到 SQLite 的设计草案，当前尚未完全落地。
- `RESOURCES.md`
  - 资源目录说明，但其中仍有旧的 `wg1` 路径表述，不能盲信。

## 约束

### 运行环境约束

- 目标平台以 Windows 为主；屏幕坐标、模板匹配、PyInstaller 打包都按 Windows 使用场景设计。
- Python 版本要求 `>=3.13`。
- 依赖由 `uv` 管理，优先使用 `uv sync`、`uv run ...`。
- OCR 目前统一走 Umi-OCR HTTP，默认地址在 README 中说明为 `http://127.0.0.1:1224/api/ocr`。

### 数据与路径约束

- 运行期配置、任务、截图、输出都应优先落在 `data/` 下。
- 历史价格与购买记录当前以 JSONL 存放，写入逻辑在 `src/super_buyer/services/history.py`，读取查询逻辑在 `src/history_store.py`。
- 测试历史模块时会使用环境变量 `ARENA_BUYER_OUTPUT_DIR` 重定向输出目录。
- 包内资源会在首次启动时播种到 `data/images` 或相关数据目录，修改默认资源时要同时考虑开发态与打包态行为。

### 代码行为约束

- Tk 组件更新必须在主线程进行；`ui/app.py` 已大量通过 `after()` 回到主线程，新增 UI 更新逻辑时必须保持这一约束。
- 自动化逻辑高度依赖时序、截图模板、坐标和 OCR 收敛，不要随意删除等待、重试、缓存或异常吞掉逻辑，除非同步验证整个流程。
- 很多模块为了容错广泛使用 `try/except Exception`；改动时要区分“历史兼容性防御代码”和“真正的坏味道”，避免一刀切清理。
- `single_purchase_runner_v2.py` 与 `task_runner.py` 可能并存；修改购买流程前要先确认当前 UI 实际调用的是哪条路径。

### 打包与资源约束

- 新增包内资源时，通常要同步检查：
  - `pyproject.toml` 的 `tool.setuptools.package-data`
  - `ArenaBuyer.spec`
  - `tools/build_win.bat`
- `tools/build_win.bat` 会用本地 `data/images` 和 `data/config.json` 覆盖包内默认资源，构建前后都要注意“本地运行数据”与“源码默认资源”之间的边界。

### 文档与事实约束

- 文档中仍有 `wg1`、`output/` 等旧描述；当前源码已经把默认输出主路径收敛到 `data/` 体系。
- `README.md` 可作为用户说明，但包路径、目录树和部分描述不一定与现状完全一致；做结构性修改前应以源码为准。

### 协作约束

- 仓库可能处于脏工作区，禁止覆盖、回滚或格式化掉与当前任务无关的用户修改。
- 生成目录、缓存目录、运行数据目录默认不做无关提交。
- 若任务涉及真实自动化行为，优先保持“小改动、可回退、可验证”，不要在不了解游戏界面状态机的情况下大规模重写。

## 规范

### 工作规范

- 先读入口，再读调用链，再改实现。
  - 推荐顺序：`pyproject.toml` → `src/super_buyer/__main__.py` → `src/super_buyer/ui/app.py` → 相关 `core/services/config` 模块。
- 涉及购买流程时，先查对应设计文档，再改代码。
  - 单商品流程优先参考 `docs/单商品购买流程设计指导方案.md`。
- 涉及历史记录时，同时检查：
  - `src/super_buyer/services/history.py`
  - `src/history_store.py`
  - `tests/test_history_store.py`

### 修改规范

- 优先做局部改动，避免跨 `ui`、`core`、`services` 同时大范围重构。
- 新增配置项时：
  - 先更新 `src/super_buyer/config/defaults.py`
  - 再确认 `loader.py` / migration 是否需要兼容处理
  - 最后检查 UI 是否需要暴露编辑入口
- 新增资源文件时，确保开发态、打包态、首次播种逻辑一致。
- 修改与路径相关的代码时，优先复用 `ConfigPaths`，不要再手写新的散落路径约定。
- 修改历史持久化时，优先保持对现有 JSONL 文件的兼容读取；`sqlite_storage_design.md` 目前只是设计，不代表已经可以直接切换。

### 代码风格规范

- 延续现有 Python 风格：
  - 使用类型注解。
  - 模块内以小函数和清晰职责拆分逻辑。
  - 注释以中文为主，解释“为什么”优先于“做了什么”。
- 保持文件编码与中文注释可读性，不要为了“纯英文”牺牲现有维护体验。
- 仓库已显式要求格式化后保持 CRLF；若使用格式化工具，注意不要无意切换换行风格。

### 验证规范

- 文档或轻量改动至少自查受影响文件与路径引用是否一致。
- 修改历史模块后，优先运行：
  - `uv run python -m unittest tests.test_history_store`
- 修改打包相关逻辑后，至少检查：
  - `ArenaBuyer.spec`
  - `tools/build_win.bat`
  - `pyproject.toml`
  三处资源声明是否一致。
- 修改 UI / 自动化流程但无法完整实机验证时，必须在结论中明确说明未验证部分与潜在风险。

### 决策规范

- 当 README、docs、代码不一致时，以代码为准，并顺手修正文档偏差。
- 当“快速修补”会进一步扩大路径混乱、资源混乱、数据混乱时，优先统一约定后再改实现。
- 当改动可能影响模板识别、点击时序、OCR 容错时，必须把稳定性放在“代码看起来更整洁”之前。
