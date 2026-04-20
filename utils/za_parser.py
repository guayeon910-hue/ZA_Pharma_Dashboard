"""남아프리카공화국(ZA) 의약품 데이터 공통 파서.

책임:
- ZAR 가격 문자열 파싱 (R 기호, 천단위 쉼표)
- ZAR → USD → KRW 변환
- 공통 DrugRecord 데이터클래스
- SEP 기준 이상치 탐지 (±30%)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------

@dataclass
class DrugRecord:
    inn_name: str
    brand_name: str
    source_site: str          # clicks | dischem | sahpra | mpr | mhpl | etender
    source_url: str
    total_price_zar: Decimal
    pack_size: str | None = None
    price_per_unit_zar: Decimal = Decimal("0")
    strength_mg: str | None = None
    dosage_form: str | None = None
    manufacturer: str | None = None
    clubcard_price_zar: Decimal | None = None  # Clicks ClubCard 할인가
    benefit_price_zar: Decimal | None = None   # Dis-Chem Loyalty 할인가
    promo_flag: bool = False
    availability: str | None = None
    confidence: float = 0.8
    extra: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""

    @property
    def price_per_unit_usd(self) -> Decimal:
        rate = _zar_to_usd_rate()
        if rate <= 0 or self.price_per_unit_zar <= 0:
            return Decimal("0")
        return (self.price_per_unit_zar * Decimal(str(rate))).quantize(Decimal("0.0001"))


# ---------------------------------------------------------------------------
# 가격 파싱
# ---------------------------------------------------------------------------

_ZAR_PATTERN = re.compile(r"R\s*([\d,]+\.?\d*)", re.IGNORECASE)
_NUMERIC_PATTERN = re.compile(r"([\d,]+\.?\d*)")


def parse_zar(text: str) -> Decimal | None:
    """'R 1,234.56' 또는 '1234.56' 형태의 ZAR 가격 문자열을 Decimal로 변환."""
    if not text:
        return None
    text = text.strip()

    m = _ZAR_PATTERN.search(text)
    if m:
        raw = m.group(1).replace(",", "")
        try:
            return Decimal(raw)
        except Exception:
            return None

    m = _NUMERIC_PATTERN.search(text)
    if m:
        raw = m.group(1).replace(",", "")
        try:
            val = Decimal(raw)
            return val if val > 0 else None
        except Exception:
            return None
    return None


def parse_pack_size(text: str) -> tuple[str | None, int]:
    """
    '30 Tablets', '60ml', '1 x 10ml Vial' 같은 문자열에서
    (pack_size_str, unit_count) 반환.
    unit_count 파싱 불가 시 1 반환.
    """
    if not text:
        return None, 1
    text = text.strip()
    m = re.search(r"(\d+)\s*(tablet|tab|cap|capsule|ml|g|mg|vial|ampoule|puff|dose|sachet|pouch)",
                  text, re.IGNORECASE)
    if m:
        return text, int(m.group(1))
    return text, 1


# ---------------------------------------------------------------------------
# 환율
# ---------------------------------------------------------------------------

def _zar_to_usd_rate() -> float:
    """ZAR/USD 환율. yfinance 실패 시 폴백값 사용."""
    try:
        import yfinance as yf  # type: ignore[import]
        return float(yf.Ticker("ZARUSD=X").fast_info.last_price)
    except Exception:
        return 0.055  # 약 18 ZAR = 1 USD (폴백)


def zar_to_usd(zar: Decimal) -> Decimal:
    rate = _zar_to_usd_rate()
    return (zar * Decimal(str(rate))).quantize(Decimal("0.0001"))


def zar_to_krw(zar: Decimal) -> Decimal:
    try:
        import yfinance as yf  # type: ignore[import]
        rate = float(yf.Ticker("ZARKRW=X").fast_info.last_price)
    except Exception:
        rate = 76.5  # 폴백
    return (zar * Decimal(str(rate))).quantize(Decimal("1"))


# ---------------------------------------------------------------------------
# 이상치 탐지
# ---------------------------------------------------------------------------

OUTLIER_THRESHOLD = 0.30


def detect_outliers(
    records: list[DrugRecord],
    sep_benchmark_zar: float | None = None,
) -> list[DrugRecord]:
    """
    SEP 기준가 대비 ±30% 초과 시 confidence를 0.5 이하로 강등.
    benchmark 없으면 동일 INN 내 중앙값을 기준으로 사용.
    """
    if not records:
        return records

    if sep_benchmark_zar and sep_benchmark_zar > 0:
        benchmark = Decimal(str(sep_benchmark_zar))
    else:
        prices = [r.price_per_unit_zar for r in records if r.price_per_unit_zar > 0]
        if not prices:
            return records
        sorted_prices = sorted(prices)
        mid = len(sorted_prices) // 2
        benchmark = sorted_prices[mid]

    for rec in records:
        if rec.price_per_unit_zar <= 0:
            continue
        deviation = abs(rec.price_per_unit_zar - benchmark) / benchmark
        if deviation > OUTLIER_THRESHOLD:
            rec.confidence = min(rec.confidence, 0.5)
            rec.extra["outlier"] = True
            rec.extra["deviation_pct"] = round(float(deviation) * 100, 1)

    return records


# ---------------------------------------------------------------------------
# DB 행 빌더
# ---------------------------------------------------------------------------

_CONFIDENCE_MAP = {
    "mpr":      0.95,
    "mhpl":     0.90,
    "clicks":   0.80,
    "dischem":  0.80,
    "sahpra":   0.85,
    "etender":  0.75,
}


def build_db_row(rec: DrugRecord, product_id: str | None = None) -> dict[str, Any]:
    vat_rate = 0.15  # 남아공 표준 VAT 15%

    segment = "public" if rec.source_site in ("mhpl", "etender") else "private"
    base_conf = _CONFIDENCE_MAP.get(rec.source_site, 0.6)
    final_conf = min(rec.confidence, base_conf)

    return {
        "product_id": product_id,
        "market_segment": segment,
        "fob_estimated_usd": None,
        "confidence": round(final_conf, 2),
        "inn_name": rec.inn_name or "",
        "brand_name": rec.brand_name or "",
        "source_site": rec.source_site,
        "raw_price_zar": float(rec.total_price_zar),
        "price_per_unit_zar": float(rec.price_per_unit_zar),
        "clubcard_price_zar": float(rec.clubcard_price_zar) if rec.clubcard_price_zar else None,
        "benefit_price_zar": float(rec.benefit_price_zar) if rec.benefit_price_zar else None,
        "pack_size": rec.pack_size,
        "vat_rate": vat_rate,
        "strength_mg": rec.strength_mg,
        "dosage_form": rec.dosage_form,
        "manufacturer": rec.manufacturer,
        "source_url": rec.source_url or "",
        "raw_text": (rec.raw_text or "")[:1000],
    }
