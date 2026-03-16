"""
Umi-OCR 进程托管。
"""

from __future__ import annotations

import atexit
import os
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional
from urllib.parse import urlparse

__all__ = ["ManagedUmiOcrProcess", "ManagedUmiOcrStatus"]


@dataclass
class ManagedUmiOcrStatus:
    managed: bool
    ready: bool
    using_existing: bool = False
    started: bool = False
    exe_path: str = ""
    message: str = ""


class ManagedUmiOcrProcess:
    """管理 Umi-OCR 随应用启动与退出的生命周期。"""

    def __init__(
        self,
        cfg: Dict[str, Any],
        *,
        app_root: Path,
        on_event: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.cfg = cfg
        self.app_root = Path(app_root).resolve()
        self.on_event = on_event or (lambda _level, _msg: None)
        self._proc: Optional[subprocess.Popen[Any]] = None
        self._spawned_by_app = False
        self._stopped = False
        atexit.register(self.stop)

    def _emit(self, level: str, message: str) -> None:
        try:
            self.on_event(level, message)
        except Exception:
            pass

    def _umi_cfg(self) -> Dict[str, Any]:
        try:
            umi_cfg = self.cfg.get("umi_ocr") or {}
        except Exception:
            umi_cfg = {}
        return umi_cfg if isinstance(umi_cfg, dict) else {}

    def _base_url(self) -> str:
        return str(self._umi_cfg().get("base_url", "http://127.0.0.1:1224") or "http://127.0.0.1:1224")

    def _parse_host_port(self) -> tuple[str, int]:
        base_url = self._base_url()
        parsed = urlparse(base_url if "://" in base_url else f"http://{base_url}")
        host = str(parsed.hostname or "127.0.0.1")
        port = int(parsed.port or 80)
        return host, port

    def _is_local_endpoint(self) -> bool:
        host, _ = self._parse_host_port()
        return host in {"127.0.0.1", "localhost", "::1"}

    def _should_manage(self) -> bool:
        umi_cfg = self._umi_cfg()
        auto_start = bool(umi_cfg.get("auto_start", True))
        return auto_start and self._is_local_endpoint()

    def _wait_timeout_sec(self) -> float:
        try:
            return max(1.0, float(self._umi_cfg().get("startup_wait_sec", 20.0) or 20.0))
        except Exception:
            return 20.0

    def _configured_exe_path(self) -> Path | None:
        raw = str(self._umi_cfg().get("exe_path", "") or "").strip()
        if not raw:
            return None
        return Path(raw).expanduser()

    def _candidate_executables(self) -> list[Path]:
        candidates: list[Path] = []
        configured = self._configured_exe_path()
        if configured is not None:
            candidates.append(configured)

        for rel in (
            Path("Umi-OCR_Paddle_v2.1.5") / "Umi-OCR.exe",
            Path("Umi-OCR") / "Umi-OCR.exe",
        ):
            candidates.append(self.app_root / rel)

        env_dir = os.environ.get("UMI_OCR_SOURCE_DIR", "").strip()
        if env_dir:
            candidates.append(Path(env_dir) / "Umi-OCR.exe")

        seen: set[str] = set()
        uniq: list[Path] = []
        for path in candidates:
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            uniq.append(path)
        return uniq

    def _find_executable(self) -> Path | None:
        for path in self._candidate_executables():
            try:
                if path.exists() and path.is_file():
                    return path.resolve()
            except Exception:
                continue
        return None

    def _endpoint_ready(self, *, timeout_sec: float = 0.6) -> bool:
        host, port = self._parse_host_port()
        try:
            with socket.create_connection((host, port), timeout=max(0.1, float(timeout_sec))):
                return True
        except OSError:
            return False

    def _wait_until_ready(self, timeout_sec: float) -> bool:
        deadline = time.time() + max(0.0, float(timeout_sec))
        while time.time() < deadline:
            if self._endpoint_ready(timeout_sec=0.4):
                return True
            time.sleep(0.25)
        return self._endpoint_ready(timeout_sec=0.4)

    def start(self) -> ManagedUmiOcrStatus:
        if not self._should_manage():
            return ManagedUmiOcrStatus(managed=False, ready=self._endpoint_ready())

        if self._endpoint_ready():
            msg = f"检测到 Umi-OCR 已在运行：{self._base_url()}"
            self._emit("info", msg)
            return ManagedUmiOcrStatus(managed=True, ready=True, using_existing=True, message=msg)

        exe_path = self._find_executable()
        if exe_path is None:
            msg = "未找到可启动的 Umi-OCR.exe，OCR 功能将不可用。"
            self._emit("warn", msg)
            return ManagedUmiOcrStatus(managed=True, ready=False, message=msg)

        creationflags = 0
        startupinfo = None
        if os.name == "nt":
            creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )
            try:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0
            except Exception:
                startupinfo = None

        try:
            self._proc = subprocess.Popen(
                [str(exe_path)],
                cwd=str(exe_path.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
            self._spawned_by_app = True
            self._stopped = False
        except Exception as exc:
            msg = f"启动 Umi-OCR 失败：{exc}"
            self._emit("error", msg)
            return ManagedUmiOcrStatus(managed=True, ready=False, exe_path=str(exe_path), message=msg)

        if self._wait_until_ready(self._wait_timeout_sec()):
            msg = f"Umi-OCR 已启动：{exe_path}"
            self._emit("success", msg)
            return ManagedUmiOcrStatus(
                managed=True,
                ready=True,
                started=True,
                exe_path=str(exe_path),
                message=msg,
            )

        msg = f"Umi-OCR 启动后未在预期时间内就绪：{exe_path}"
        self._emit("warn", msg)
        self.stop()
        return ManagedUmiOcrStatus(
            managed=True,
            ready=False,
            started=True,
            exe_path=str(exe_path),
            message=msg,
        )

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if not self._spawned_by_app:
            return
        proc = self._proc
        self._proc = None
        self._spawned_by_app = False
        if proc is None:
            return
        try:
            if proc.poll() is not None:
                return
        except Exception:
            pass

        try:
            proc.terminate()
            proc.wait(timeout=5)
            return
        except Exception:
            pass

        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
                return
            except Exception:
                pass

        try:
            proc.kill()
        except Exception:
            pass
