"""
简单的图标生成脚本。

用途：
- 将 PNG 源图转换为 Windows 可用的 ICO 图标文件；
- 供 tools/build_win.bat 在打包前调用。

用法：
- python gen_icon.py path/to/input.png path/to/output.ico
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("用法: python gen_icon.py input.png output.ico", file=sys.stderr)
        return 1
    src = Path(argv[1])
    dst = Path(argv[2])
    if not src.is_file():
        print(f"[ERROR] 源图不存在: {src}", file=sys.stderr)
        return 1
    try:
        from PIL import Image  # type: ignore
    except Exception as exc:  # pragma: no cover - 仅运行时检查
        print(f"[ERROR] 缺少 Pillow，请先安装: {exc}", file=sys.stderr)
        return 1

    try:
        img = Image.open(src).convert("RGBA")
        sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
        dst.parent.mkdir(parents=True, exist_ok=True)
        img.save(dst, sizes=sizes)
        print(f"[INFO] 已生成 ICO 图标: {dst}")
        return 0
    except Exception as exc:  # pragma: no cover - 仅运行时检查
        print(f"[ERROR] 生成图标失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main(sys.argv))

