"""日志上下文格式测试。"""

from __future__ import annotations

import time
import unittest

from super_buyer.core.logging import build_context_message, ensure_level_tag


class LoggingFormatTests(unittest.TestCase):
    def test_build_context_message_keeps_step_and_phase_order(self) -> None:
        text = build_context_message(
            "步骤6-价格读取与阈值判定",
            phase="均价读取",
            state="开始",
            message="窗口=350ms 步进=15ms",
        )
        self.assertEqual(
            text,
            "步骤6-价格读取与阈值判定 | 阶段=均价读取 | 状态=开始 | 窗口=350ms 步进=15ms",
        )

    def test_ensure_level_tag_strips_legacy_level_tag(self) -> None:
        ts = 1.0
        expected_time = time.strftime("%H:%M:%S", time.localtime(ts))
        text = ensure_level_tag("【DEBUG】【M995】【0/1】：步骤6-价格读取与阈值判定 | 阶段=均价读取", "detail", ts=ts)
        self.assertEqual(text, f"【{expected_time}】【M995】【0/1】：步骤6-价格读取与阈值判定 | 阶段=均价读取")


if __name__ == "__main__":
    unittest.main()
