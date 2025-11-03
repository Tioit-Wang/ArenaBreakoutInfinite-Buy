"""
配置迁移与兼容性工具。

用于在加载配置时平滑处理旧版字段及其它结构化调整。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Tuple
from pathlib import Path

from super_buyer.resources.paths import image_path


def deep_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并 src→dst，子字典保留引用安全。"""
    for key, value in src.items():
        if isinstance(value, dict):
            node = dst.setdefault(key, {})
            if isinstance(node, dict):
                deep_merge(node, value)
            else:
                dst[key] = value
        else:
            dst[key] = value
    return dst


def migrate_from_key_mapping(
    cfg: Dict[str, Any],
    *,
    mapping_path: str = "key_mapping.json",
) -> Tuple[Dict[str, Any], bool]:
    """将旧版 key_mapping.json 合并到配置中。

    返回 (cfg, changed) 对。
    """
    changed = False
    if not os.path.exists(mapping_path):
        return cfg, False
    try:
        with open(mapping_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return cfg, False
    if not isinstance(data, dict):
        return cfg, False

    points = cfg.setdefault("points", {}) if isinstance(cfg.get("points"), dict) else {}
    key_map = {
        "第一个商品": "first_item",
        "第1个商品": "first_item",
        "第一个商品位置": "first_item",
        "数量输入框": "quantity_input",
        "数量输入": "quantity_input",
        "购买数量": "quantity_input",
        "首页按钮": "btn_home",
        "市场按钮": "btn_market",
        "市场搜索栏": "input_search",
        "市场搜索按钮": "btn_search",
        "购买按钮": "btn_buy",
        "商品关闭位置": "btn_close",
        "刷新按钮": "btn_refresh",
    }

    for raw_key, value in list(data.items()):
        if not isinstance(value, dict) or "x" not in value or "y" not in value:
            continue
        if str(raw_key) in ("价格区域左上", "价格区域右下"):
            continue
        try:
            x, y = int(value.get("x", 0)), int(value.get("y", 0))
        except Exception:
            continue
        target_key = key_map.get(str(raw_key)) or (
            str(raw_key) if str(raw_key).isascii() else None
        )
        if not target_key:
            continue
        if not isinstance(points, dict):
            points = {}
            cfg["points"] = points
        payload = {"x": x, "y": y}
        if points.get(target_key) != payload:
            points[target_key] = payload
            changed = True

    tl = data.get("价格区域左上")
    br = data.get("价格区域右下")
    if isinstance(tl, dict) and isinstance(br, dict):
        try:
            left, top = int(tl.get("x", 0)), int(tl.get("y", 0))
            right, bottom = int(br.get("x", 0)), int(br.get("y", 0))
        except Exception:
            left = top = right = bottom = 0
        if right > left and bottom > top:
            rects = (
                cfg.setdefault("rects", {})
                if isinstance(cfg.get("rects"), dict)
                else {}
            )
            if not isinstance(rects, dict):
                rects = {}
                cfg["rects"] = rects
            value = {"x1": left, "y1": top, "x2": right, "y2": bottom}
            if rects.get("price_region") != value:
                rects["price_region"] = value
                changed = True

    return cfg, changed


def normalize_template_keys(cfg: Dict[str, Any]) -> bool:
    """将模板字典中的中文键迁移为 ASCII 键。"""
    templates = cfg.get("templates")
    if not isinstance(templates, dict):
        return False
    mapping = {
        "启动按钮": "btn_launch",
        "首页标识": "home_indicator",
        "市场标识": "market_indicator",
        "市场按钮": "btn_market",
        "首页按钮": "btn_home",
        "搜索输入框": "input_search",
        "搜索按钮": "btn_search",
        "购买按钮": "btn_buy",
        "购买成功": "buy_ok",
        "购买失败": "buy_fail",
        "关闭按钮": "btn_close",
        "刷新按钮": "btn_refresh",
        "返回按钮": "btn_back",
        "最大按钮": "btn_max",
        # 新增：处罚识别模板及确认
        "处罚识别模板": "penalty_warning",
        "处罚识别确认模板": "btn_penalty_confirm",
    }
    changed = False
    for cn_key, ascii_key in mapping.items():
        if cn_key in templates:
            if ascii_key not in templates:
                templates[ascii_key] = templates.pop(cn_key)
            else:
                templates.pop(cn_key)
            changed = True
    return changed


def migrate_template_paths_to_resources(
    cfg: Dict[str, Any], *, base_dir: str | Path | None = None
) -> bool:
    """迁移模板路径到包内资源路径。

    - 仅迁移顶层模板（btn_*.png、buy_*.png、input_search.png 等），即旧式 `images/<name>.png`。
    - 若工作区 `images/<name>.png` 存在，则保留用户覆盖路径不迁移。
    - goods/ 等子目录下的自定义模板不触碰。
    - 同时迁移 `price_roi.top_template/bottom_template` 类似路径。
    """
    changed = False
    base = Path(base_dir) if base_dir else None

    def _should_migrate(p: str) -> tuple[bool, str | None]:
        if not isinstance(p, str) or not p:
            return False, None
        norm = p.replace("\\", "/").lstrip("./")
        if not norm.startswith("images/"):
            return False, None
        name = norm.split("/", 1)[1] if "/" in norm else ""
        # 仅处理顶层文件名（不含子目录）
        if not name or "/" in name:
            return False, None
        # 如果本地 images/<name> 存在，则视为用户覆盖，不迁移
        if base is not None and (base / "images" / name).exists():
            return False, None
        return True, name

    # templates.*.path
    tmpls = cfg.get("templates")
    if isinstance(tmpls, dict):
        for k, v in list(tmpls.items()):
            if not isinstance(v, dict):
                continue
            op = v.get("path")
            do, name = _should_migrate(str(op) if isinstance(op, str) else "")
            if do and name:
                try:
                    v["path"] = str(image_path(name))
                    changed = True
                except Exception:
                    # 包内不存在则忽略
                    pass

    # price_roi.{top_template,bottom_template}
    proi = cfg.get("price_roi")
    if isinstance(proi, dict):
        for key in ("top_template", "bottom_template"):
            op = proi.get(key)
            do, name = _should_migrate(str(op) if isinstance(op, str) else "")
            if do and name:
                try:
                    proi[key] = str(image_path(name))
                    changed = True
                except Exception:
                    pass

    return changed


def migrate_debug_overlay_dir_to_output(
    cfg: Dict[str, Any], *, output_dir: str | Path | None = None
) -> bool:
    """迁移 debug.overlay_dir 从 images/debug/* 到 output/debug/*。

    - 若 overlay_dir 为相对路径且以 images/debug 开头，则改写到 output/debug。
    - 若已为绝对路径或用户自定义非 images/debug，保持不变。
    """
    dbg = cfg.get("debug")
    if not isinstance(dbg, dict):
        return False
    val = dbg.get("overlay_dir")
    if not isinstance(val, str) or not val:
        return False
    out_root = str(output_dir or "output")
    norm = val.replace("\\", "/").lstrip("./").lower()
    if norm.startswith("images/debug"):
        # 迁移到 output/debug，尽量保留最后一级目录名
        try:
            tail = Path(val).name
        except Exception:
            tail = "overlay"
        dbg["overlay_dir"] = str(Path(out_root) / "debug" / tail)
        return True
    return False

