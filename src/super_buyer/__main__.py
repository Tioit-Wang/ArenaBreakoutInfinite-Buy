"""wg1 应用入口。

提供命令行入口 `wg1`，调用 GUI 启动函数。
"""

from __future__ import annotations

from super_buyer.ui.app import run_app


def main() -> None:
    """控制台脚本入口。"""
    run_app()


if __name__ == "__main__":
    main()
