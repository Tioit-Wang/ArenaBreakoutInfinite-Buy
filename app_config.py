import json
import os
from typing import Any, Dict


DEFAULT_CONFIG: Dict[str, Any] = {
    "templates": {
        # image-name -> path/confidence
        "首页按钮": {"path": os.path.join("images", "btn_home.png"), "confidence": 0.85},
        "市场按钮": {"path": os.path.join("images", "btn_market.png"), "confidence": 0.85},
        "市场搜索栏": {"path": os.path.join("images", "input_search.png"), "confidence": 0.85},
        "市场搜索按钮": {"path": os.path.join("images", "btn_search.png"), "confidence": 0.85},
        "购买按钮": {"path": os.path.join("images", "btn_buy.png"), "confidence": 0.88},
        "购买成功": {"path": os.path.join("images", "buy_ok.png"), "confidence": 0.90},
        "商品关闭位置": {"path": os.path.join("images", "btn_close.png"), "confidence": 0.85},
        "刷新按钮": {"path": os.path.join("images", "btn_refresh.png"), "confidence": 0.85},
    },
    "points": {
        # 单点坐标
        "第一个商品": {"x": 0, "y": 0},
        "数量输入框": {"x": 0, "y": 0},
    },
    "rects": {
        # 区域坐标
        "价格区域": {"x1": 0, "y1": 0, "x2": 0, "y2": 0},
        # 可扩展: "数量区域": {...}
    },
    "purchase": {
        "item_name": "",
        "price_threshold": 0,
        "target_total": 0,
        "max_per_order": 120,
    },
    "purchase_items": [
        # Example item definition; users edit in GUI
        # {"enabled": False, "item_name": "", "price_threshold": 0, "target_total": 0, "max_per_order": 120}
    ],
    "price_roi": {
        "top_template": os.path.join(".", "buy_data_top.png"),
        "top_threshold": 0.55,
        "bottom_template": os.path.join(".", "buy_data_btm.png"),
        "bottom_threshold": 0.55,
        "top_offset": 0,
        "bottom_offset": 0,
        "lr_pad": 0,
    },
    "avg_price_area": {
        # ROI width follows the Buy button width; these control vertical position/size
        "distance_from_buy_top": 5,
        "height": 45,
        # Allowlist for PyTesseract preview (kept configurable for future tuning)
        "ocr_allowlist": "0123456789KM",
    },
}


def _deep_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in src.items():
        if isinstance(v, dict):
            node = dst.setdefault(k, {})
            if isinstance(node, dict):
                _deep_merge(node, v)
            else:
                dst[k] = v
        else:
            dst[k] = v
    return dst


def load_config(path: str = "config.json") -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if isinstance(data, dict):
        _deep_merge(cfg, data)
    return cfg


def save_config(cfg: Dict[str, Any], path: str = "config.json") -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)
    except Exception:
        pass


def ensure_default_config(path: str = "config.json") -> Dict[str, Any]:
    if not os.path.exists(path):
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        save_config(cfg, path)
        return cfg
    return load_config(path)


def sync_to_key_mapping(cfg: Dict[str, Any], mapping_path: str = "key_mapping.json") -> None:
    """Bridge minimal data to legacy key_mapping.json used by existing modules.

    Writes:
      - 单点坐标: ‘第一个商品’, ‘数量输入框’, 以及模板名中部分常用键（若存在 point 则优先）
      - ROI: 价格区域左上/右下
    """
    out: Dict[str, Any] = {}

    # Points (preferred keys used by MappingAutomator)
    for key in ["第一个商品", "数量输入框", "首页按钮", "市场按钮", "市场搜索栏", "市场搜索按钮", "购买按钮", "商品关闭位置", "刷新按钮"]:
        pt = cfg.get("points", {}).get(key)
        if isinstance(pt, dict) and "x" in pt and "y" in pt:
            out[key] = {"x": int(pt["x"]), "y": int(pt["y"])}

    # ROI for price
    rect = cfg.get("rects", {}).get("价格区域")
    if isinstance(rect, dict):
        x1 = int(rect.get("x1", 0)); y1 = int(rect.get("y1", 0))
        x2 = int(rect.get("x2", 0)); y2 = int(rect.get("y2", 0))
        out["价格区域左上"] = {"x": x1, "y": y1}
        out["价格区域右下"] = {"x": x2, "y": y2}

    try:
        with open(mapping_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=4)
    except Exception:
        pass
