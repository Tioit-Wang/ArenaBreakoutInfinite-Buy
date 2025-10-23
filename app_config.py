import json
import os
from typing import Any, Dict, Tuple


DEFAULT_CONFIG: Dict[str, Any] = {
    "game": {
        # 可执行文件路径，用于定时到点后自动启动游戏
        "exe_path": "",
        # 启动参数（可选），留空则不传
        "launch_args": "",
        # 启动后等待“首页标识”出现的超时（秒）
        "startup_timeout_sec": 180,
    },
    # Umi-OCR HTTP 服务 (可选)
    "umi_ocr": {
        "base_url": "http://127.0.0.1:1224",
        "timeout_sec": 2.5,
        # 传给 /api/ocr 的 options（可留空）。常用：{"data.format": "text"}
        "options": {
            "data.format": "text"
        },
    },
    "hotkeys": {
        # Tk-style key sequences; prefer 'toggle'. Examples: "<Control-Alt-t>", "<F5>"
        "toggle": "<Control-Alt-t>",
        # Backward compatibility; if set, GUI will fall back to this when 'toggle' missing
        "stop": "<Control-Alt-t>",
    },
    "templates": {
        # template-key -> { path, confidence }
        "btn_launch": {"path": os.path.join("images", "btn_launch.png"), "confidence": 0.85},
        # 首页标识模板（用于状态判定，不用于点击）
        "home_indicator": {"path": os.path.join("images", "home_indicator.png"), "confidence": 0.85},
        # 市场标识模板（用于状态判定，不用于点击）
        "market_indicator": {"path": os.path.join("images", "market_indicator.png"), "confidence": 0.85},
        "btn_market": {"path": os.path.join("images", "btn_market.png"), "confidence": 0.85},
        "btn_home": {"path": os.path.join("images", "btn_home.png"), "confidence": 0.85},
        "input_search": {"path": os.path.join("images", "input_search.png"), "confidence": 0.85},
        "btn_search": {"path": os.path.join("images", "btn_search.png"), "confidence": 0.85},
        "btn_buy": {"path": os.path.join("images", "btn_buy.png"), "confidence": 0.88},
        "buy_ok": {"path": os.path.join("images", "buy_ok.png"), "confidence": 0.90},
        "buy_fail": {"path": os.path.join("images", "buy_fail.png"), "confidence": 0.90},
        "btn_close": {"path": os.path.join("images", "btn_close.png"), "confidence": 0.85},
        "btn_refresh": {"path": os.path.join("images", "btn_refresh.png"), "confidence": 0.85},
        "btn_back": {"path": os.path.join("images", "btn_back.png"), "confidence": 0.85},
        "btn_max": {"path": os.path.join("images", "btn_max.png"), "confidence": 0.85},
        # Quantity input bounding templates (horizontal pair: minus on the left, plus on the right)
        "qty_minus": {"path": os.path.join("images", "qty_minus.png"), "confidence": 0.85},
        "qty_plus": {"path": os.path.join("images", "qty_plus.png"), "confidence": 0.85},
    },
    "purchase": {
        "item_name": "",
        "price_threshold": 0,
        "target_total": 0,
        "max_per_order": 120,
        # 默认每次购买的系统数量（用于简化流程下的计数），可超量累计
        "default_buy_qty": 1,
    },
    "purchase_items": [
        # Example item definition; users edit in GUI
        # {"enabled": False, "item_name": "", "price_threshold": 0, "target_total": 0, "max_per_order": 120, "default_buy_qty": 1}
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
        "ocr_allowlist": "0123456789K",
        # OCR engine: 'tesseract' | 'umi' (default: umi)
        "ocr_engine": "umi",
        # Scale factor applied before binarization/OCR
        "scale": 1.0,
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


def _migrate_from_key_mapping(cfg: Dict[str, Any], mapping_path: str = "key_mapping.json") -> Tuple[Dict[str, Any], bool]:
    """Migrate legacy key_mapping.json into cfg (points + price ROI).

    - Points: copy any {"x","y"} entries to cfg["points"].
    - ROI: if both '价格区域左上' and '价格区域右下' exist, write to cfg['rects']['价格区域'].
    Returns (cfg, changed).
    """
    changed = False
    if not os.path.exists(mapping_path):
        return cfg, False
    try:
        with open(mapping_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return cfg, False
    if not isinstance(data, dict):
        return cfg, False

    # Points (map known Chinese keys to ASCII)
    pts = cfg.setdefault("points", {}) if isinstance(cfg.get("points"), dict) else {}
    key_map = {
        "第一个商品": "first_item",
        "第1个商品": "first_item",
        "第一个商品位置": "first_item",
        "数量输入框": "quantity_input",
        "数量输入": "quantity_input",
        "购买数量": "quantity_input",
        # Allow template-like names to map to same ASCII keys if provided as points
        "首页按钮": "btn_home",
        "市场按钮": "btn_market",
        "市场搜索栏": "input_search",
        "市场搜索按钮": "btn_search",
        "购买按钮": "btn_buy",
        "商品关闭位置": "btn_close",
        "刷新按钮": "btn_refresh",
    }
    for k, v in list(data.items()):
        if isinstance(v, dict) and "x" in v and "y" in v:
            if str(k) in ("价格区域左上", "价格区域右下"):
                continue
            try:
                x, y = int(v.get("x", 0)), int(v.get("y", 0))
            except Exception:
                continue
            dst = key_map.get(str(k)) or (str(k) if str(k).isascii() else None)
            if not dst:
                continue
            if not isinstance(pts, dict):
                pts = {}
                cfg["points"] = pts
            if pts.get(dst) != {"x": x, "y": y}:
                pts[dst] = {"x": x, "y": y}
                changed = True

    # ROI from corners -> rects.price_region
    tl = data.get("价格区域左上")
    br = data.get("价格区域右下")
    if isinstance(tl, dict) and isinstance(br, dict):
        try:
            l, t = int(tl.get("x", 0)), int(tl.get("y", 0))
            r, b = int(br.get("x", 0)), int(br.get("y", 0))
        except Exception:
            l = t = r = b = 0
        if r > l and b > t:
            rects = cfg.setdefault("rects", {}) if isinstance(cfg.get("rects"), dict) else {}
            if not isinstance(rects, dict):
                rects = {}
                cfg["rects"] = rects
            newr = {"x1": l, "y1": t, "x2": r, "y2": b}
            if rects.get("price_region") != newr:
                rects["price_region"] = newr
                changed = True

    return cfg, changed


def load_config(
    path: str = "config.json",
    *,
    migrate_legacy: bool = False,
    normalize_keys: bool = True,
) -> Dict[str, Any]:
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
    changed = False
    # Optional: one-time migration from legacy key_mapping.json
    if migrate_legacy:
        cfg, ch_m = _migrate_from_key_mapping(cfg, mapping_path="key_mapping.json")
        changed = changed or ch_m
    # Normalize keys (Chinese -> ASCII) for templates
    def _normalize_ascii_keys(conf: Dict[str, Any]) -> bool:
        changed_local = False
        # templates
        tmap = {
            "启动按钮": "btn_launch",
            # 标识模板（新的规范键）：
            "首页标识模板": "home_indicator",
            "市场标识模板": "market_indicator",
            # 按钮：
            "首页按钮": "btn_home",
            "市场按钮": "btn_market",
            "市场搜索栏": "input_search",
            "市场搜索按钮": "btn_search",
            "购买按钮": "btn_buy",
            "购买成功": "buy_ok",
            "数量最大按钮": "btn_max",
            "商品关闭位置": "btn_close",
            "刷新按钮": "btn_refresh",
            # quantity pair
            "数量+": "qty_plus",
            "数量-": "qty_minus",
            # ASCII keys (ensure remain stable)
            "btn_home": "btn_home",
            "home_indicator": "home_indicator",
            "market_indicator": "market_indicator",
        }
        tpl = conf.get("templates")
        if isinstance(tpl, dict):
            new_tpl: Dict[str, Any] = {}
            for k, v in tpl.items():
                nk = tmap.get(str(k)) or (str(k) if str(k).isascii() else None)
                if nk is None:
                    # Unknown non-ascii key: skip or create slug
                    nk = f"tpl_{abs(hash(str(k))) % 100000}"
                if nk in new_tpl and isinstance(new_tpl[nk], dict) and isinstance(v, dict):
                    # merge: prefer explicit values in v
                    new_tpl[nk].update(v)
                else:
                    new_tpl[nk] = v
                if nk != k:
                    changed_local = True
            conf["templates"] = new_tpl
        return changed_local

    if normalize_keys and _normalize_ascii_keys(cfg):
        changed = True
    # Ensure new tolerant-launch defaults are present even for older config files
    try:
        g = cfg.setdefault("game", {}) if isinstance(cfg.get("game"), dict) else {}
        if isinstance(g, dict):
            g.setdefault("launcher_timeout_sec", 60)
            g.setdefault("launch_click_delay_sec", 20)
    except Exception:
        pass
    # Remove deprecated field 'game_ready_timeout_sec'
    try:
        g = cfg.get("game") or {}
        if isinstance(g, dict) and "game_ready_timeout_sec" in g:
            g.pop("game_ready_timeout_sec", None)
            changed = True
    except Exception:
        pass
    if changed:
        try:
            save_config(cfg, path)
        except Exception:
            pass
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


# key_mapping.json 已移除；所有坐标与 ROI 已统一放置在 config.json 中。
