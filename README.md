# 图像识别自动购买 GUI（tkinter + OpenCV）

基于模板图像识别与坐标配置的自动购买助手，支持多商品轮询、进度统计、模板管理与截图预览。

## 运行

使用 uv 管理依赖与脚本：

```bash
uv sync
uv run wg1-gui
```

或直接：

```bash
uv run python gui_app.py
```

## 功能概览

- Tabs 布局：
 - 初始化配置：
    - 模板管理表格：模板名 | 路径(只读) | 置信度 | 测试识别 | 截图 | 预览
    - 坐标与区域配置：设置“第一个商品”“数量输入框”坐标及“价格区域”左上/右下
  - 自动购买：
    - 多商品任务：新增/编辑（模态）、右键删除、首列单击启用/禁用
    - 轮询执行多任务，显示每项进度与日志

## 模板截图与预览

- 点击“截图”：进入全屏半透明遮罩，拖拽框选区域后自动保存到 `images/<英文名>.png`，并弹出模态预览。
- 常用模板文件名：
  - 首页按钮 → `btn_home.png`
  - 市场按钮 → `btn_market.png`
  - 搜索框 → `input_search.png`
  - 搜索按钮 → `btn_search.png`
  - 购买按钮 → `btn_buy.png`
  - 购买成功 → `buy_ok.png`
  - 详情关闭 → `btn_close.png`
  - 刷新按钮 → `btn_refresh.png`
  - 未知模板使用 `tpl_<hash>.png`

## 目录结构（精简）

```
wg1/
├─ images/                  # 模板与调试截图
├─ app_config.py            # 配置加载/保存
├─ autobuyer.py             # 单/多商品自动购买逻辑
├─ auto_clicker.py          # 坐标/模板点击封装（AHK+pyautogui）
├─ price_reader.py          # ROI 价格 OCR
├─ gui_app.py               # 主 GUI 程序
├─ config.json              # GUI 配置（模板/坐标/任务）
├─ pyproject.toml           # 依赖定义，包含脚本入口 wg1-gui
└─ uv.lock                  # uv 锁定文件
```

## 注意

- Windows 上建议以管理员权限运行，避免输入被阻止。
- DPI 缩放影响坐标与模板匹配，建议设置为 100% 或在模板中统一截取。
- 若 OCR 识别价格不稳定，可在“价格区域”调整 ROI 并多次保存快照验证。
 - 可选迁移：如需从旧版 `key_mapping.json` 导入坐标与 ROI，可在代码层启用迁移（app_config.load_config 的 migrate_legacy=True）；默认不迁移，建议在界面手动设置。

## OCR 引擎

- 预览下拉可选择：`tesseract`、`easyocr`、`umi`。
- `umi` 使用本地 Umi-OCR HTTP 接口（默认 `http://127.0.0.1:1224/api/ocr`）。
  - 可在 `config.json` 的 `umi_ocr` 节调整 `base_url`、`timeout_sec` 与 `options`。
  - 运行逻辑（自动购买）与预览共用配置键 `avg_price_area.ocr_engine`，可按配置切换至 `umi`。
