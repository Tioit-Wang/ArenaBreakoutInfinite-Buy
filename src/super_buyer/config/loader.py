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
    migrate_template_paths_to_resources,
    migrate_debug_overlay_dir_to_output,
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
    migrate_paths: bool = True,
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
    if migrate_paths:
        try:
            # 迁移模板路径到包内资源；迁移调试目录到 output/debug
            changed = migrate_template_paths_to_resources(cfg, base_dir=cfg_path.parent) or changed
            out_dir = (cfg.get("paths") or {}).get("output_dir") if isinstance(cfg.get("paths"), dict) else None
            changed = migrate_debug_overlay_dir_to_output(cfg, output_dir=out_dir or (paths.output_dir if paths else "output")) or changed
        except Exception:
            pass

    if changed:
        try:
            save_config(cfg, path=cfg_path)
        except Exception:
            pass
    # 仅内存态解析相对路径为绝对路径（相对于配置文件所在目录）
    try:
        _resolve_relative_paths_in_memory(cfg, base_dir=cfg_path.parent)
    except Exception:
        pass
    return cfg


def _resolve_relative_paths_in_memory(cfg: Dict[str, Any], *, base_dir: str | Path) -> None:
    """将配置中相对路径解析为绝对路径（仅内存态，不落盘）。

    - 解析 templates.*.path 与 price_roi.{top_template,bottom_template}
    - 相对路径以配置文件夹为基准；已为绝对路径的保持不变。
    """
    base = Path(base_dir).resolve()
    # templates
    tmpls = cfg.get("templates")
    if isinstance(tmpls, dict):
        for _k, v in tmpls.items():
            if not isinstance(v, dict):
                continue
            p = v.get("path")
            if not isinstance(p, str) or not p:
                continue
            pp = Path(p)
            if not pp.is_absolute():
                v["path"] = str((base / pp).resolve())
    # price_roi
    roi = cfg.get("price_roi")
    if isinstance(roi, dict):
        for key in ("top_template", "bottom_template"):
            p = roi.get(key)
            if not isinstance(p, str) or not p:
                continue
            pp = Path(p)
            if not pp.is_absolute():
                roi[key] = str((base / pp).resolve())


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
