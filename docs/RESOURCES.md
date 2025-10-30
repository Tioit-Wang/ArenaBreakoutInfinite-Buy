# 资源与目录说明

- 内置模板与字体等静态资源存放在包内：`src/wg1/resources/...`
  - 访问接口：`wg1.resources.paths.image_path(name)`、`asset_path(name)`。
- 用户自定义模板/截图建议存放在工作区：`images/`。
  - UI 的“保存/选择图片”默认指向该目录。
- 调试与运行产物统一输出到：`output/`（可在 `config.json -> paths.output_dir` 配置）。
- 兼容旧配置：若 `config.json` 中模板路径仍为 `images\*.png` 且文件缺失，代码会通过默认配置回退到包内资源（详见 `wg1.config.defaults` 的 `_asset_path()`）。

建议：优先使用包内内置模板保证开箱即用；需要替换时，将自定义模板放入 `images/` 并在 UI 中选择。

