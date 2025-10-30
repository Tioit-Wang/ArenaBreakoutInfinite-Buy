"""
配置迁移与兼容性工具。

用于在加载配置时平滑处理旧版字段及其它结构化调整。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Tuple


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

