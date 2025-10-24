# 仓库指南

## 项目结构与模块组织

- `gui_app.py`：Tkinter 图形界面入口
- `task_runner.py`：任务运行器
- `app_config.py`：配置相关
- `images/`：点击模板及商品模板图片
  - `goods/`：商品图片
  - `templates/`：点击模板图片

## 构建、测试与开发命令

- Python 版本：`3.13`（见 `.python-version`）。
- 安装依赖：`uv sync`（基于 `pyproject.toml`/`uv.lock`）。
- 运行 GUI：`uv python gui_app.py`。
- 添加依赖：`uv add <pkg>`。

## 编码风格与命名约定

- 遵循 PEP 8，4 空格缩进；优先使用类型注解与 docstring。
- 命名：模块/文件 `snake_case.py`；函数/变量 `snake_case`；类 `PascalCase`。
- UI 文案保持用户可读；同一条消息尽量避免中英文混用，默认采用中文。
- 变更尽量小且与现有风格一致。
