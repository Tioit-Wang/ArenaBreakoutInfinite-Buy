@echo off

REM 构建 Windows 目录分发版本（dist\ArenaBuyer）
REM 需先在当前环境安装依赖：
REM   python -m pip install -U pyinstaller pyinstaller-hooks-contrib opencv-python pillow pyautogui pyscreeze pymsgbox pytweening PyRect mouseinfo matplotlib requests

python -m PyInstaller -D -w -n ArenaBuyer -p src ^
  --collect-data super_buyer.resources.images ^
  --collect-data super_buyer.resources.assets ^
  --collect-data super_buyer.resources.defaults ^
  --hidden-import=pymsgbox ^
  --hidden-import=pyscreeze ^
  --hidden-import=pytweening ^
  --hidden-import=PyRect ^
  --hidden-import=mouseinfo ^
  src\super_buyer\__main__.py
