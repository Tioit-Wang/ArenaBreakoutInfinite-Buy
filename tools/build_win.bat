@echo off

REM Build Windows folder distribution: dist\ArenaBuyer
REM Required packages in the current environment:
REM   python -m pip install -U pyinstaller pyinstaller-hooks-contrib opencv-python pillow pyautogui pyscreeze pymsgbox pytweening pyrect mouseinfo matplotlib requests

setlocal
set SCRIPT_DIR=%~dp0
set ROOT_DIR=%SCRIPT_DIR%..
set VENV_PYTHON=%ROOT_DIR%\.venv\Scripts\python.exe
set SPEC_FILE=%ROOT_DIR%\ArenaBuyer.spec
set DIST_DIR=%ROOT_DIR%\dist\ArenaBuyer
set BUILD_DIR=%ROOT_DIR%\build\ArenaBuyer
set UMI_OCR_DIR=%UMI_OCR_SOURCE_DIR%
if "%UMI_OCR_DIR%"=="" set UMI_OCR_DIR=%ROOT_DIR%\Umi-OCR_Paddle_v2.1.5
if not exist "%UMI_OCR_DIR%\Umi-OCR.exe" if "%UMI_OCR_SOURCE_DIR%"=="" set UMI_OCR_DIR=%ROOT_DIR%\Umi-OCR

REM --------------------------------------
REM 1) Mirror current data\images into packaged default templates
REM    Exclude debug folders, temporary ROI files and package init file.
REM --------------------------------------
set DATA_IMAGES=%ROOT_DIR%\data\images
set RES_IMAGES=%ROOT_DIR%\src\super_buyer\resources\images

if exist "%DATA_IMAGES%" (
  echo [INFO] Mirroring data\images to resources\images...
  robocopy "%DATA_IMAGES%" "%RES_IMAGES%" /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XD debug __pycache__ /XF .seeded _*.png __init__.py
  if errorlevel 8 (
    echo [ERROR] Template mirror sync failed.
    exit /b 1
  )
  REM robocopy /MIR may delete the package init file. Restore it if needed.
  if not exist "%RES_IMAGES%\__init__.py" (
    > "%RES_IMAGES%\__init__.py" echo """Packaged image resources."""
  )
) else (
  echo [WARN] %DATA_IMAGES% not found. Skip template sync.
)

REM --------------------------------------
REM 2) Sync current data\config.json as packaged default snapshot
REM    On first launch in a new environment, it becomes data\config.json.
REM --------------------------------------
set DATA_CFG=%ROOT_DIR%\data\config.json
set DEF_CFG=%ROOT_DIR%\src\super_buyer\resources\defaults\config.json

if exist "%DATA_CFG%" (
  echo [INFO] Copying data\config.json to resources\defaults\config.json...
  copy /Y "%DATA_CFG%" "%DEF_CFG%" >NUL
) else (
  echo [WARN] %DATA_CFG% not found. Keep existing default config.
)

REM --------------------------------------
REM 3) Resolve bundled Umi-OCR source directory
REM --------------------------------------
if exist "%UMI_OCR_DIR%\Umi-OCR.exe" (
  echo [INFO] Using Umi-OCR bundle source: %UMI_OCR_DIR%
  set UMI_OCR_SOURCE_DIR=%UMI_OCR_DIR%
) else (
  echo [WARN] Umi-OCR source not found: %UMI_OCR_DIR%
  echo [WARN] Build will continue without bundling Umi-OCR. Expected project-local folder: %ROOT_DIR%\Umi-OCR_Paddle_v2.1.5
)

REM --------------------------------------
REM 4) Choose Python interpreter
REM --------------------------------------
if exist "%VENV_PYTHON%" (
  echo [INFO] Using virtualenv interpreter: %VENV_PYTHON%
  set PYTHON_EXE=%VENV_PYTHON%
) else (
  echo [WARN] .venv\Scripts\python.exe not found. Fallback to python in PATH.
  set PYTHON_EXE=python
)

REM --------------------------------------
REM 5) Build folder distribution via PyInstaller spec
REM --------------------------------------
if not exist "%SPEC_FILE%" (
  echo [ERROR] Spec file not found: %SPEC_FILE%
  exit /b 1
)

REM Try to stop a previous packaged process before cleaning old outputs.
taskkill /F /IM ArenaBuyer.exe >NUL 2>&1

if exist "%DIST_DIR%" (
  echo [INFO] Removing previous dist\ArenaBuyer...
  rmdir /S /Q "%DIST_DIR%" >NUL 2>&1
  if exist "%DIST_DIR%" (
    echo [ERROR] Failed to remove %DIST_DIR%. Close ArenaBuyer or any program using the folder, then retry.
    exit /b 1
  )
)

if exist "%BUILD_DIR%" (
  echo [INFO] Removing previous build\ArenaBuyer...
  rmdir /S /Q "%BUILD_DIR%" >NUL 2>&1
  if exist "%BUILD_DIR%" (
    echo [ERROR] Failed to remove %BUILD_DIR%. Retry after closing file locks.
    exit /b 1
  )
)

pushd "%ROOT_DIR%"
"%PYTHON_EXE%" -m PyInstaller --clean --noconfirm "%SPEC_FILE%"
if errorlevel 1 (
  popd
  echo [ERROR] PyInstaller build failed.
  exit /b 1
)
popd

endlocal
