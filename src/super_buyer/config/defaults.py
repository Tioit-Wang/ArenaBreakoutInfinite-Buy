"""
默认配置定义。

集中维护应用初始配置，便于在其它模块中按需导入。
"""

from __future__ import annotations

import os
from typing import Any, Dict

from super_buyer.resources.paths import image_path


def _asset_path(name: str) -> str:
    """返回模板文件的绝对路径。"""
    try:
        return str(image_path(name))
    except FileNotFoundError:
        return os.path.join("images", name)


DEFAULT_CONFIG: Dict[str, Any] = {
    "game": {
        "exe_path": "",
        "launch_args": "",
        "startup_timeout_sec": 180,
    },
    "umi_ocr": {
        "base_url": "http://127.0.0.1:1224",
        "timeout_sec": 2.5,
        "options": {
            "data.format": "text",
        },
    },
    "ocr_allowlist": "0123456789KkMm",
    "paths": {
        "output_dir": "output",
    },
    "debug": {
        # 是否在均价识别轮最终失败时保存 ROI 原图与二值图（默认关闭）
        "save_roi_on_fail": False,
    },
    "hotkeys": {
        "toggle": "<Control-Alt-t>",
        "stop": "<Control-Alt-t>",
    },
    "templates": {
        "btn_launch": {"path": _asset_path("btn_launch.png"), "confidence": 0.85},
        "home_indicator": {"path": _asset_path("home_indicator.png"), "confidence": 0.85},
        "market_indicator": {"path": _asset_path("market_indicator.png"), "confidence": 0.85},
        "btn_market": {"path": _asset_path("btn_market.png"), "confidence": 0.85},
        "btn_home": {"path": _asset_path("btn_home.png"), "confidence": 0.85},
        "input_search": {"path": _asset_path("input_search.png"), "confidence": 0.85},
        "btn_search": {"path": _asset_path("btn_search.png"), "confidence": 0.85},
        "btn_buy": {"path": _asset_path("btn_buy.png"), "confidence": 0.88},
        "buy_ok": {"path": _asset_path("buy_ok.png"), "confidence": 0.90},
        "buy_fail": {"path": _asset_path("buy_fail.png"), "confidence": 0.90},
        "btn_close": {"path": _asset_path("btn_close.png"), "confidence": 0.85},
        "btn_refresh": {"path": _asset_path("btn_refresh.png"), "confidence": 0.85},
        "btn_back": {"path": _asset_path("btn_back.png"), "confidence": 0.85},
        "btn_max": {"path": _asset_path("btn_max.png"), "confidence": 0.85},
        "qty_minus": {"path": _asset_path("qty_minus.png"), "confidence": 0.85},
        "qty_plus": {"path": _asset_path("qty_plus.png"), "confidence": 0.85},
        # 新增：处罚识别与确认模板
        "penalty_warning": {"path": _asset_path("penalty_warning.png"), "confidence": 0.90},
        "btn_penalty_confirm": {"path": _asset_path("btn_penalty_confirm.png"), "confidence": 0.90},
    },
    "purchase": {
        "item_name": "",
        "price_threshold": 0,
        "target_total": 0,
        "max_per_order": 120,
        "default_buy_qty": 1,
    },
    "purchase_items": [],
    "price_roi": {
        "top_template": str(image_path("buy_data_top.png")),
        "top_threshold": 0.55,
        "bottom_template": str(image_path("buy_data_btm.png")),
        "bottom_threshold": 0.55,
        "top_offset": 0,
        "bottom_offset": 0,
        "lr_pad": 0,
    },
    "avg_price_area": {
        "distance_from_buy_top": 5,
        "height": 45,
        "scale": 1.0,
    },
}
