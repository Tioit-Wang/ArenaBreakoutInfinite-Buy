@echo off

REM 构建 Windows 目录分发版本（dist\ArenaBuyer）
REM 需先在当前环境安装依赖：
REM   python -m pip install -U pyinstaller pyinstaller-hooks-contrib opencv-python pillow pyautogui pyscreeze pymsgbox pytweening PyRect mouseinfo matplotlib requests

setlocal
set SCRIPT_DIR=%~dp0
set ROOT_DIR=%SCRIPT_DIR%..

REM --------------------------------------
REM 1) 使用当前 data\images 作为初始模板
REM --------------------------------------
set DATA_IMAGES=%ROOT_DIR%\data\images
set RES_IMAGES=%ROOT_DIR%\src\super_buyer\resources\images

if exist "%DATA_IMAGES%" (
  echo [INFO] 同步 data\images 至 resources\images...
  xcopy "%DATA_IMAGES%" "%RES_IMAGES%" /E /Y >NUL
) else (
  echo [WARN] 未找到 %DATA_IMAGES%，跳过模板同步。
)

REM --------------------------------------
REM 2) 同步当前 data\config.json 为默认配置快照
REM    （仅用于打包时查看，运行时仍以代码中的 DEFAULT_CONFIG 为准）
REM --------------------------------------
set DATA_CFG=%ROOT_DIR%\data\config.json
set DEF_CFG=%ROOT_DIR%\src\super_buyer\resources\defaults\config.json

if exist "%DATA_CFG%" (
  echo [INFO] 使用 data\config.json 覆盖 resources\defaults\config.json...
  copy /Y "%DATA_CFG%" "%DEF_CFG%" >NUL
) else (
  echo [WARN] 未找到 %DATA_CFG%，保留原始默认配置。
)

REM --------------------------------------
REM 3) 使用已经生成的应用图标（tools\bin\app_icon.ico）
REM    app_icon.ico 可由 tools\bin\app_icon.png 通过 Python 脚本单次转换得到
REM --------------------------------------
set ICON_ICO=%SCRIPT_DIR%bin\app_icon.ico
set ICON_ARG=

if exist "%ICON_ICO%" (
  echo [INFO] 使用图标 %ICON_ICO% 作为应用图标...
  set ICON_ARG=-i "%ICON_ICO%"
) else (
  echo [WARN] 未找到 %ICON_ICO%，将使用 PyInstaller 默认图标。
)

REM --------------------------------------
REM 4) 调用 PyInstaller 生成目录分发版本
REM --------------------------------------

REM 若已存在旧的 ArenaBuyer.spec，先删除以避免残留 datas 配置
if exist "%ROOT_DIR%\ArenaBuyer.spec" (
  echo [INFO] 删除旧的 ArenaBuyer.spec 以重新生成...
  del /F /Q "%ROOT_DIR%\ArenaBuyer.spec" >NUL 2>&1
)

python -m PyInstaller -D -w -n ArenaBuyer -p src %ICON_ARG% --collect-data super_buyer.resources.images --collect-data super_buyer.resources.assets --collect-data super_buyer.resources.defaults --hidden-import=pymsgbox --hidden-import=pyscreeze --hidden-import=pytweening --hidden-import=PyRect --hidden-import=mouseinfo src\super_buyer\__main__.py

endlocal
