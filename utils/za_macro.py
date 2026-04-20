"""남아프리카공화국(ZA) 거시경제 지표 수집 모듈."""

from __future__ import annotations

import time
from typing import Any

_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_TTL = 300.0  # 5분 캐시


def get_za_macro() -> dict[str, Any]:
    if _cache["data"] and time.time() - _cache["ts"] < _TTL:
        return _cache["data"]

    exchange = _fetch_exchange()
    macro = {
        **exchange,
        "country": "South Africa",
        "country_code": "ZA",
        "currency": "ZAR",
        "market_size_usd_bn": 11.52,        # 2024년 기준 약 115억 달러
        "cagr_2025_2033_pct": 7.66,
        "population_million": 62.0,
        "public_sector_share_pct": 80,      # 인구 80%가 공공 보건 이용
        "private_sector_share_pct": 20,
        "vat_pharma_pct": 15.0,
        "hs_code_pharma": "3004",
        "top_importers": ["China", "India", "Germany", "USA"],
        "nhi_status": "Implementation in progress",
        "fetched_at": time.time(),
        "ok": exchange.get("ok", False),
    }
    _cache["data"] = macro
    _cache["ts"] = time.time()
    return macro


def _fetch_exchange() -> dict[str, Any]:
    try:
        import yfinance as yf  # type: ignore[import]
        zar_krw = float(yf.Ticker("ZARKRW=X").fast_info.last_price)
        zar_usd = float(yf.Ticker("ZARUSD=X").fast_info.last_price)
        usd_krw = float(yf.Ticker("USDKRW=X").fast_info.last_price)
        return {
            "zar_krw": round(zar_krw, 4),
            "zar_usd": round(zar_usd, 6),
            "usd_krw": round(usd_krw, 2),
            "source": "Yahoo Finance",
            "ok": True,
        }
    except Exception as exc:
        return {
            "zar_krw": 76.5,
            "zar_usd": 0.055,
            "usd_krw": 1390.0,
            "source": "폴백 (Yahoo Finance 연결 실패)",
            "ok": False,
            "error": str(exc)[:80],
        }
