"""ZA 파서 단위 테스트."""

from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.za_parser import DrugRecord, build_db_row, detect_outliers, parse_pack_size, parse_zar


class TestZaParser(unittest.TestCase):
    def test_parse_zar_handles_currency_and_commas(self) -> None:
        self.assertEqual(parse_zar("R 1,234.56"), Decimal("1234.56"))
        self.assertEqual(parse_zar("1234.56"), Decimal("1234.56"))

    def test_parse_pack_size_extracts_unit_count(self) -> None:
        pack_size, unit_count = parse_pack_size("30 Tablets")
        self.assertEqual(pack_size, "30 Tablets")
        self.assertEqual(unit_count, 30)

    def test_detect_outliers_marks_large_deviation(self) -> None:
        records = [
            DrugRecord(
                inn_name="Fluticasone",
                brand_name="Base",
                source_site="clicks",
                source_url="https://example.com/base",
                total_price_zar=Decimal("100"),
                price_per_unit_zar=Decimal("10"),
            ),
            DrugRecord(
                inn_name="Fluticasone",
                brand_name="Outlier",
                source_site="clicks",
                source_url="https://example.com/outlier",
                total_price_zar=Decimal("1000"),
                price_per_unit_zar=Decimal("100"),
            ),
        ]

        detect_outliers(records, sep_benchmark_zar=10)

        self.assertGreater(records[1].extra["deviation_pct"], 30)
        self.assertTrue(records[1].extra["outlier"])
        self.assertLessEqual(records[1].confidence, 0.5)

    def test_build_db_row_sets_private_segment(self) -> None:
        record = DrugRecord(
            inn_name="Fluticasone",
            brand_name="Sereterol Activair",
            source_site="clicks",
            source_url="https://example.com/product",
            total_price_zar=Decimal("299.99"),
            price_per_unit_zar=Decimal("10.00"),
            pack_size="30 Tablets",
            manufacturer="KUP",
            raw_text="sample",
        )

        row = build_db_row(record, product_id="sereterol_activair")

        self.assertEqual(row["product_id"], "sereterol_activair")
        self.assertEqual(row["market_segment"], "private")
        self.assertEqual(row["source_site"], "clicks")
        self.assertEqual(row["raw_price_zar"], 299.99)
        self.assertEqual(row["pack_size"], "30 Tablets")


if __name__ == "__main__":
    unittest.main(verbosity=2)
