"""历史查询与统计相关测试。"""

from __future__ import annotations

import os
import tempfile
import unittest

import history_store
from super_buyer.services import history as history_service


class QueryPriceMinutelyTests(unittest.TestCase):
    """验证分钟聚合查询的兼容与补齐逻辑。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base_dir = self._tmp.name
        self._old_output_dir = os.environ.get("ARENA_BUYER_OUTPUT_DIR")
        os.environ["ARENA_BUYER_OUTPUT_DIR"] = self.base_dir
        self.paths = history_service.resolve_paths(self.base_dir)
        history_service._MIN_AGG.clear()
        history_service._LAST_PRICE_CACHE.clear()
        history_service._LAST_RAW_WRITE.clear()
        history_store._JSONL_CACHE.clear()

    def tearDown(self) -> None:
        history_service._MIN_AGG.clear()
        history_service._LAST_PRICE_CACHE.clear()
        history_service._LAST_RAW_WRITE.clear()
        history_store._JSONL_CACHE.clear()
        if self._old_output_dir is None:
            os.environ.pop("ARENA_BUYER_OUTPUT_DIR", None)
        else:
            os.environ["ARENA_BUYER_OUTPUT_DIR"] = self._old_output_dir
        self._tmp.cleanup()

    def test_query_price_minutely_fills_current_minute_from_raw_history(self) -> None:
        """当前分钟尚未 flush 时，查询仍应返回可绘图的分钟聚合记录。"""
        history_service.append_price(
            item_id="item-1",
            item_name="测试物品",
            price=12345,
            paths=self.paths,
            ts=1710000001.0,
        )

        records = history_store.query_price_minutely("item-1", 0.0)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["ts"], 1710000000.0)
        self.assertEqual(records[0]["ts_min"], 1710000000.0)
        self.assertEqual(records[0]["min"], 12345)
        self.assertEqual(records[0]["max"], 12345)
        self.assertEqual(records[0]["avg"], 12345)

    def test_query_price_minutely_merges_file_data_and_current_minute(self) -> None:
        """已落盘分钟聚合与当前分钟原始数据应能同时返回。"""
        history_service.append_price(
            item_id="item-1",
            item_name="测试物品",
            price=100,
            paths=self.paths,
            ts=1710000001.0,
        )
        history_service.append_price(
            item_id="item-1",
            item_name="测试物品",
            price=120,
            paths=self.paths,
            ts=1710000061.0,
        )

        records = history_store.query_price_minutely("item-1", 0.0)

        self.assertEqual([record["ts"] for record in records], [1710000000.0, 1710000060.0])
        self.assertEqual([record["avg"] for record in records], [100, 120])
        self.assertTrue(all(record["ts"] == record["ts_min"] for record in records))

    def test_summarize_prices_by_item_returns_batch_stats(self) -> None:
        """批量价格统计应只汇总目标物品并返回统一口径。"""
        history_service.append_price(
            item_id="item-1",
            item_name="测试物品1",
            price=100,
            paths=self.paths,
            ts=1710000001.0,
        )
        history_service.append_price(
            item_id="item-1",
            item_name="测试物品1",
            price=200,
            paths=self.paths,
            ts=1710000301.0,
        )
        history_service.append_price(
            item_id="item-2",
            item_name="测试物品2",
            price=300,
            paths=self.paths,
            ts=1710000001.0,
        )

        stats = history_store.summarize_prices_by_item(["item-1", "item-2", "missing"], 1710000000.0)

        self.assertEqual(stats["item-1"]["count"], 2)
        self.assertEqual(stats["item-1"]["min_price"], 100)
        self.assertEqual(stats["item-1"]["max_price"], 200)
        self.assertEqual(stats["item-1"]["avg_price"], 150)
        self.assertEqual(stats["item-1"]["latest_price"], 200)
        self.assertEqual(stats["item-2"]["count"], 1)
        self.assertEqual(stats["item-2"]["avg_price"], 300)
        self.assertEqual(stats["missing"]["count"], 0)
        self.assertEqual(stats["missing"]["avg_price"], 0)

    def test_summarize_purchases_returns_consistent_metrics(self) -> None:
        """购买统计应统一给出数量、金额、均价和价格区间。"""
        history_service.append_purchase(
            item_id="item-1",
            item_name="测试物品",
            price=100,
            qty=2,
            paths=self.paths,
            ts=1710000001.0,
        )
        history_service.append_purchase(
            item_id="item-1",
            item_name="测试物品",
            price=160,
            qty=1,
            paths=self.paths,
            ts=1710000301.0,
        )

        summary = history_store.summarize_purchases(history_store.query_purchase("item-1", 0.0))

        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["quantity"], 3)
        self.assertEqual(summary["total_amount"], 360)
        self.assertEqual(summary["avg_price"], 120)
        self.assertEqual(summary["min_price"], 100)
        self.assertEqual(summary["max_price"], 160)


if __name__ == "__main__":
    unittest.main()
