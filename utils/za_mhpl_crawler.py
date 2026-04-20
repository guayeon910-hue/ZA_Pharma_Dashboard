"""NDoH Master Health Products List (MHPL) 크롤러.

남아공 보건부 마스터 조달 카탈로그(엑셀) 다운로드 및 파싱.
No-Bid 항목(공급 공백 기회) 자동 감지.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

NDOH_BASE = "https://www.health.gov.za"
MHPL_SEARCH_URLS = [
    f"{NDOH_BASE}/index.php/master-health-products-list",
    f"{NDOH_BASE}/index.php/procurement/master-health-products-list",
    "https://open.data.gov.za",
]

CACHE_DIR = Path("datas/za_mhpl_cache")


@dataclass
class MhplRecord:
    description: str
    supplier: str
    price_incl_vat_zar: Decimal
    eml_status: str         # EML approved | NON-EML | Unknown
    facility_level: str     # HOSP | PHC | TERTIARY | All
    inn_name: str = ""
    pack_size: str = ""
    contract_expiry: str = ""
    no_bid: bool = False
    source_url: str = ""
    raw_text: str = ""


async def fetch_mhpl(emit: Any = None) -> list[MhplRecord]:
    """MHPL 엑셀 파일 다운로드 및 파싱."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    excel_url = await _detect_mhpl_url(emit)
    if not excel_url:
        if emit:
            await emit({"phase": "mhpl", "message": "MHPL 엑셀 URL 감지 실패", "level": "warn"})
        return _load_from_cache()

    cache_file = CACHE_DIR / (_url_hash(excel_url) + ".xlsx")
    if cache_file.exists():
        if emit:
            await emit({"phase": "mhpl", "message": "MHPL 캐시 사용", "level": "info"})
        return _parse_mhpl_excel(cache_file.read_bytes(), excel_url)

    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.get(excel_url)
            resp.raise_for_status()
        cache_file.write_bytes(resp.content)
        records = _parse_mhpl_excel(resp.content, excel_url)
        no_bid_count = sum(1 for r in records if r.no_bid)
        if emit:
            await emit({
                "phase": "mhpl",
                "message": f"MHPL {len(records)}건 파싱 (No-Bid {no_bid_count}건)",
                "level": "success",
            })
        return records
    except Exception as exc:
        if emit:
            await emit({"phase": "mhpl", "message": f"MHPL 다운로드 실패: {exc}", "level": "warn"})
        return _load_from_cache()


async def _detect_mhpl_url(emit: Any) -> str | None:
    from bs4 import BeautifulSoup  # type: ignore[import]

    for base_url in MHPL_SEARCH_URLS:
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                resp = await client.get(base_url)
                resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for link in soup.find_all("a", href=True):
                href = str(link["href"])
                text_lower = link.get_text(strip=True).lower()
                if ("mhpl" in href.lower() or "master" in text_lower or "health product" in text_lower):
                    if any(ext in href.lower() for ext in [".xlsx", ".xls", ".csv"]):
                        return href if href.startswith("http") else NDOH_BASE + href
        except Exception:
            continue
    return None


def _parse_mhpl_excel(data: bytes, source_url: str) -> list[MhplRecord]:
    try:
        import pandas as pd  # type: ignore[import]
    except ImportError:
        return []

    try:
        df = pd.read_excel(io.BytesIO(data), dtype=str)
    except Exception:
        return []

    df.columns = [str(c).strip().lower() for c in df.columns]
    records: list[MhplRecord] = []

    for _, row in df.iterrows():
        try:
            desc = str(_col(row, ["description as per contract", "description", "product"]) or "")
            supplier = str(_col(row, ["supplier name", "supplier", "vendor", "company"]) or "")
            price_raw = _col(row, ["price 15% vat", "price incl vat", "price", "unit price"])
            eml = str(_col(row, ["eml", "eml status", "formulary"]) or "Unknown")
            facility = str(_col(row, ["facility level", "level", "facility"]) or "")

            price_str = str(price_raw or "0").replace("R", "").replace(",", "").strip()
            try:
                price = Decimal(price_str)
            except Exception:
                continue

            no_bid = (
                "no bid" in supplier.lower()
                or "no bid" in desc.lower()
                or price <= 0
            )

            records.append(MhplRecord(
                description=desc[:300],
                supplier=supplier[:150],
                price_incl_vat_zar=price,
                eml_status=eml[:50],
                facility_level=facility[:50],
                inn_name=_extract_inn(desc),
                no_bid=no_bid,
                source_url=source_url,
                raw_text=f"{desc} | {supplier} | {price_raw}",
            ))
        except Exception:
            continue

    return records


def _extract_inn(description: str) -> str:
    """설명 문자열에서 INN명 추출 (간단한 키워드 매칭)."""
    INN_KEYWORDS = [
        "Rosuvastatin", "Atorvastatin", "Cilostazol", "Mosapride",
        "Fluticasone", "Salmeterol", "Gadobutrol", "Hydroxyurea", "Omega",
    ]
    desc_lower = description.lower()
    for inn in INN_KEYWORDS:
        if inn.lower() in desc_lower:
            return inn
    return ""


def get_no_bid_items(records: list[MhplRecord]) -> list[MhplRecord]:
    """No-Bid 항목 (공급 공백) 반환 — 진입 기회 탐지."""
    return [r for r in records if r.no_bid]


def _col(row: Any, candidates: list[str]) -> Any:
    for c in candidates:
        val = row.get(c)
        if val and str(val).strip() not in ("nan", "none", ""):
            return val
    return None


def _url_hash(url: str) -> str:
    import hashlib
    return hashlib.md5(url.encode()).hexdigest()[:12]


def _load_from_cache() -> list[MhplRecord]:
    if not CACHE_DIR.exists():
        return []
    files = sorted(CACHE_DIR.glob("*.xlsx"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return []
    return _parse_mhpl_excel(files[0].read_bytes(), str(files[0]))
