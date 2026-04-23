"""ZA 분석 엔진 단위 테스트."""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import za_export_analyzer as analyzer


class TestZaExportAnalyzer(unittest.TestCase):
    """배포 전 빠르게 검증 가능한 무네트워크 경로만 확인."""

    def setUp(self) -> None:
        self._orig = {
            key: os.environ.get(key)
            for key in ("CLAUDE_API_KEY", "ANTHROPIC_API_KEY", "PERPLEXITY_API_KEY")
        }
        for key in self._orig:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in self._orig.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _run(self, coro):
        return asyncio.run(coro)

    def test_analyze_all_without_api_key_returns_all_products(self) -> None:
        results = self._run(analyzer.analyze_all(use_perplexity=False))
        self.assertEqual(len(results), len(analyzer.PRODUCT_LABELS))
        self.assertEqual(
            {result["product_id"] for result in results},
            set(analyzer.PRODUCT_LABELS),
        )

    def test_results_have_required_fields(self) -> None:
        results = self._run(analyzer.analyze_all(use_perplexity=False))
        required = {
            "product_id",
            "inn_name",
            "label",
            "verdict",
            "analysis",
            "retail_count",
            "sep_count",
            "sahpra_count",
            "mhpl_count",
            "no_bid_count",
            "references",
        }
        for result in results:
            for field in required:
                self.assertIn(field, result, f"{result.get('product_id')}: '{field}' 필드 없음")

    def test_verdict_defaults_to_unanalyzed_without_api_key(self) -> None:
        results = self._run(analyzer.analyze_all(use_perplexity=False))
        for result in results:
            self.assertEqual(result["verdict"], "미분석")
            self.assertIn("ANTHROPIC_API_KEY", result["analysis"])

    def test_unknown_product_id_returns_unanalyzed_result(self) -> None:
        result = self._run(analyzer.analyze_product("UNKNOWN_PID"))
        self.assertEqual(result["product_id"], "UNKNOWN_PID")
        self.assertEqual(result["verdict"], "미분석")

    def test_all_product_ids_are_covered(self) -> None:
        self.assertEqual(set(analyzer.PRODUCT_MAP.values()), set(analyzer.PRODUCT_LABELS))

    def test_custom_product_without_api_key_returns_unanalyzed(self) -> None:
        result = self._run(analyzer.analyze_custom_product("Test Drug", "test-inn"))
        self.assertEqual(result["product_id"], "custom")
        self.assertEqual(result["verdict"], "미분석")
        self.assertIn("ANTHROPIC_API_KEY", result["analysis"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
