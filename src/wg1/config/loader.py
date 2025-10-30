"""
配置文件加载与写入工具。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .defaults import DEFAULT_CONFIG
from .migrations import (
    deep_merge,
    migrate_from_key_mapping,
    normalize_template_keys,
)


@dataclass(frozen=True)
class ConfigPaths:
    """集中管理配置及资源路径。"""

    root: Path
    config_file: Path
    output_dir: Path

    assets_dir: Path
    images_dir: Path
    resources_dir: Path

    @classmethod
    def from_root(cls, root: str | Path) -> "ConfigPaths":
        base = Path(root).resolve()
        return cls(
            root=base,
            config_file=base / "config.json",
            output_dir=base / "output",
            assets_dir=base / "resources" / "assets",
            images_dir=base / "images",
            resources_dir=base / "resources",
        )


def ensure_default_config(paths: ConfigPaths, *, overwrite: bool = False) -> Path:
    """若配置文件不存在则写入默认值。"""
    cfg_path = paths.config_file
    if cfg_path.exists() and not overwrite:
        return cfg_path
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg_path.open("w", encoding="utf-8") as fh:
        json.dump(DEFAULT_CONFIG, fh, ensure_ascii=False, indent=2)
    return cfg_path


def load_config(
    path: Optional[str | Path] = None,
    *,
    paths: Optional[ConfigPaths] = None,
    migrate_legacy: bool = False,
    normalize_keys: bool = True,
) -> Dict[str, Any]:
    """加载配置文件并按需处理兼容逻辑。"""
    cfg_path = Path(path) if path is not None else (paths.config_file if paths else Path("config.json"))
    data: Dict[str, Any] = {}
    if cfg_path.exists():
        try:
            with cfg_path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                data = raw
        except Exception:
            data = {}
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if data:
        deep_merge(cfg, data)

    changed = False
    if migrate_legacy:
        cfg, migrated = migrate_from_key_mapping(cfg, mapping_path=str(cfg_path.parent / "key_mapping.json"))
        changed = changed or migrated
    if normalize_keys:
        changed = normalize_template_keys(cfg) or changed

    if changed:
        try:
            save_config(cfg, path=cfg_path)
        except Exception:
            pass
    return cfg


def save_config(
    data: Dict[str, Any],
    *,
    path: Optional[str | Path] = None,
    indent: int = 2,
) -> Path:
    """保存配置到指定路径。"""
    cfg_path = Path(path or "config.json")
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg_path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=indent)
    return cfg_path
