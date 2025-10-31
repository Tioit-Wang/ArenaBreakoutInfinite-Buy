"""
统一启动流程封装。
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from super_buyer.core.common import safe_sleep
from super_buyer.core.models import LaunchResult
from super_buyer.services.screen_ops import ScreenOps


def run_launch_flow(
    cfg: Dict[str, Any],
    *,
    on_log: Optional[Callable[[str], None]] = None,
    click_delay_override: Optional[float] = None,
) -> LaunchResult:
    """统一启动流程（预览与任务共用）。"""

    def emit(message: str) -> None:
        try:
            if on_log:
                on_log(message)
        except Exception:
            pass

    game = cfg.get("game") or {}
    exe_path = str(game.get("exe_path", "")).strip()
    args = str(game.get("launch_args", "")).strip()
    try:
        launcher_timeout = float(game.get("launcher_timeout_sec", 60) or 60)
    except Exception:
        launcher_timeout = 60.0
    try:
        click_delay = float(
            click_delay_override
            if click_delay_override is not None
            else (game.get("launch_click_delay_sec", 20) or 20)
        )
    except Exception:
        click_delay = 20.0
    try:
        startup_timeout = float(game.get("startup_timeout_sec", 180) or 180)
    except Exception:
        startup_timeout = 180.0

    templates = (cfg.get("templates", {}) or {})

    def template_path(key: str) -> str:
        tpl = templates.get(key) or {}
        return str(tpl.get("path", "")).strip()

    home_key = "home_indicator"
    home_path = template_path(home_key)
    market_key = "market_indicator"
    market_path = template_path(market_key)
    launch_path = template_path("btn_launch")

    screen = ScreenOps(cfg, step_delay=0.02)

    try:
        if (
            home_path
            and os.path.exists(home_path)
            and screen.locate(home_key, timeout=0.4) is not None
        ) or (
            market_path
            and os.path.exists(market_path)
            and screen.locate(market_key, timeout=0.4) is not None
        ):
            emit("[启动流程] 已检测到首页/市场标识，跳过启动。")
            return LaunchResult(
                True,
                code="ok",
                details={"skipped": True, "reason": "home_or_market_present"},
            )
    except Exception:
        pass

    if not exe_path:
        return LaunchResult(False, code="missing_config", error="未配置启动器路径")
    if not os.path.exists(exe_path):
        return LaunchResult(False, code="exe_missing", error="启动器路径不存在")
    if not (launch_path and os.path.exists(launch_path)):
        return LaunchResult(
            False, code="missing_launch_template", error="未配置或找不到“启动”按钮模板文件"
        )
    if not (
        (home_path and os.path.exists(home_path))
        or (market_path and os.path.exists(market_path))
    ):
        return LaunchResult(
            False,
            code="missing_indicator_template",
            error="未配置首页/市场标识模板，无法判定启动完成",
        )

    screen = ScreenOps(cfg, step_delay=0.02)

    try:
        working_dir = os.path.dirname(exe_path)
        if args:
            try:
                import shlex

                cmd: List[str] = [exe_path] + shlex.split(args, posix=False)
            except Exception:
                cmd = [exe_path] + args.split()
        else:
            cmd = [exe_path]
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        subprocess.Popen(
            cmd,
            cwd=working_dir or None,
            creationflags=creationflags,
        )
        emit(f"[启动流程] 已执行: {exe_path} {' '+args if args else ''}")
    except Exception as exc:
        return LaunchResult(False, code="launch_error", error=str(exc))

    end_launch = time.time() + max(1.0, float(launcher_timeout))
    launch_box: Optional[Tuple[int, int, int, int]] = None
    while time.time() < end_launch:
        box = screen.locate("btn_launch", timeout=0.2)
        if box is not None:
            launch_box = box
            break
        safe_sleep(0.2)
    if launch_box is None:
        return LaunchResult(False, code="launch_button_timeout", error="等待启动按钮超时")

    if float(click_delay) > 0:
        target = time.time() + float(click_delay)
        while time.time() < target:
            safe_sleep(0.2)
    screen.click_center(launch_box)
    emit("[启动流程] 已点击启动按钮")

    end_home = time.time() + max(1.0, float(startup_timeout))
    while time.time() < end_home:
        if (
            screen.locate(home_key, timeout=0.3) is not None
            or screen.locate(market_key, timeout=0.3) is not None
        ):
            return LaunchResult(True, code="ok")
        safe_sleep(0.3)
    return LaunchResult(False, code="home_timeout", error="等待首页标识超时")


__all__ = ["run_launch_flow"]

