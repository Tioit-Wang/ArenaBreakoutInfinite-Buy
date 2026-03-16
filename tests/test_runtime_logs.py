"""运行日志存储与回放测试。"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from super_buyer.services.runtime_logs import (
    append_runtime_log,
    read_latest_runtime_logs,
)


class RuntimeLogStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.output_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_append_runtime_log_strips_level_tag_and_writes_daily_file(self) -> None:
        ts = 1710000001.0
        expected_time = time.strftime("%H:%M:%S", time.localtime(ts))
        expected_day = time.strftime("%Y-%m-%d", time.localtime(ts))
        text = append_runtime_log(
            self.output_dir,
            "exec",
            "【DEBUG】【测试物品】【0/1】：按钮来源=cache | 坐标=(1,2,3,4)",
            ts=ts,
        )

        self.assertNotIn("DEBUG", text)
        self.assertTrue(text.startswith(f"【{expected_time}】"))
        self.assertIn("【测试物品】【0/1】：按钮来源=cache", text)

        day_file = self.output_dir / "logs" / expected_day / "exec.jsonl"
        self.assertTrue(day_file.exists())

    def test_read_latest_runtime_logs_keeps_latest_limit_across_days(self) -> None:
        ts1 = 1710000001.0
        ts2 = 1710000002.0
        ts3 = 1710086401.0
        ts4 = 1710086402.0
        append_runtime_log(self.output_dir, "exec", "第1条", ts=ts1)
        append_runtime_log(self.output_dir, "exec", "第2条", ts=ts2)
        append_runtime_log(self.output_dir, "exec", "第3条", ts=ts3)
        append_runtime_log(self.output_dir, "exec", "第4条", ts=ts4)

        latest = read_latest_runtime_logs(self.output_dir, "exec", limit=3)

        self.assertEqual(
            latest,
            [
                f"【{time.strftime('%H:%M:%S', time.localtime(ts2))}】第2条",
                f"【{time.strftime('%H:%M:%S', time.localtime(ts3))}】第3条",
                f"【{time.strftime('%H:%M:%S', time.localtime(ts4))}】第4条",
            ],
        )

    def test_read_latest_runtime_logs_filters_by_channel(self) -> None:
        ts_exec = 1710000001.0
        ts_multi = 1710000002.0
        append_runtime_log(self.output_dir, "exec", "执行日志", ts=ts_exec)
        append_runtime_log(self.output_dir, "multi", "多商品日志", ts=ts_multi)

        exec_logs = read_latest_runtime_logs(self.output_dir, "exec", limit=10)
        multi_logs = read_latest_runtime_logs(self.output_dir, "multi", limit=10)

        self.assertEqual(exec_logs, [f"【{time.strftime('%H:%M:%S', time.localtime(ts_exec))}】执行日志"])
        self.assertEqual(multi_logs, [f"【{time.strftime('%H:%M:%S', time.localtime(ts_multi))}】多商品日志"])


if __name__ == "__main__":
    unittest.main()
