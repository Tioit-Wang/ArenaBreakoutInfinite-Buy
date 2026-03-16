"""配置加载与多商品默认参数迁移测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from super_buyer.config.defaults import DEFAULT_MULTI_SNIPE_TUNING
from super_buyer.config.loader import load_config


class ConfigLoaderTuningMigrationTests(unittest.TestCase):
    def test_load_config_upgrades_known_legacy_multi_snipe_defaults(self) -> None:
        legacy = {
            "buy_result_timeout_sec": 0.35,
            "buy_result_poll_step_sec": 0.01,
            "poll_step_sec": 0.02,
            "ocr_round_window_sec": 0.35,
            "ocr_round_step_sec": 0.015,
            "ocr_round_fail_limit": 10,
            "post_close_detail_sec": 0.08,
            "post_success_click_sec": 0.08,
            "post_nav_sec": 0.08,
            "ocr_miss_penalty_threshold": 10,
            "penalty_confirm_delay_sec": 5.0,
            "penalty_wait_sec": 180.0,
            "fast_chain_mode": True,
            "fast_chain_max": 10,
            "fast_chain_interval_ms": 35.0,
        }

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(
                json.dumps({"multi_snipe_tuning": legacy}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            cfg = load_config(cfg_path)
            tuning = cfg["multi_snipe_tuning"]

            self.assertEqual(tuning["ocr_round_window_sec"], DEFAULT_MULTI_SNIPE_TUNING["ocr_round_window_sec"])
            self.assertEqual(tuning["ocr_round_fail_limit"], DEFAULT_MULTI_SNIPE_TUNING["ocr_round_fail_limit"])
            self.assertEqual(tuning["post_close_detail_sec"], DEFAULT_MULTI_SNIPE_TUNING["post_close_detail_sec"])
            self.assertEqual(tuning["detail_open_settle_sec"], DEFAULT_MULTI_SNIPE_TUNING["detail_open_settle_sec"])
            self.assertEqual(tuning["anchor_stabilize_sec"], DEFAULT_MULTI_SNIPE_TUNING["anchor_stabilize_sec"])

            saved = json.loads(cfg_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["multi_snipe_tuning"]["ocr_round_window_sec"], DEFAULT_MULTI_SNIPE_TUNING["ocr_round_window_sec"])
            self.assertEqual(saved["multi_snipe_tuning"]["detail_cache_verify_timeout_sec"], DEFAULT_MULTI_SNIPE_TUNING["detail_cache_verify_timeout_sec"])

    def test_load_config_keeps_user_custom_tuning_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "multi_snipe_tuning": {
                            "post_close_detail_sec": 0.12,
                            "ocr_round_fail_limit": 3,
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            cfg = load_config(cfg_path)
            tuning = cfg["multi_snipe_tuning"]

            self.assertEqual(tuning["post_close_detail_sec"], 0.12)
            self.assertEqual(tuning["ocr_round_fail_limit"], 3)
            self.assertEqual(tuning["detail_open_settle_sec"], DEFAULT_MULTI_SNIPE_TUNING["detail_open_settle_sec"])
            self.assertEqual(tuning["post_nav_sec"], DEFAULT_MULTI_SNIPE_TUNING["post_nav_sec"])


if __name__ == "__main__":
    unittest.main()
