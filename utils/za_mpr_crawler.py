"""Medicine Price Registry (MPR) 크롤러 — 남아공 단일 출고가(SEP) 수집.

기능:
  1. MPR 웹사이트에서 최신 SEP 엑셀 파일 감지 및 다운로드
  2. 엑셀 파싱 → SEP 데이터 추출
  3. 법정 최대 조제 수수료(Dispensing Fee) 역산 (4구간)
  4. FOB 역산용 스윗스팟 계산
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

MPR_BASE = "https://www.mpr.gov.za"
MPR_SEARCH_URL = f"{MPR_BASE}/PriceListings/Index"
MPR_DB_URL = f"{MPR_BASE}/MediaStore.aspx"  # 구버전 다운로드 경로

CACHE_DIR = Path("datas/za_mpr_cache")


# ---------------------------------------------------------------------------
# 조제 수수료 계산 (법정 4구간)
# ---------------------------------------------------------------------------

def calc_dispensing_fee(sep_zar: Decimal) -> Decimal:
    """
    남아공 법정 최대 조제 수수료 계산 (VAT 제외).
    출처: Medicines and Related Substances Act — Dispensing Fee Regulations.
    """
    sep = sep_zar
    if sep < Decimal("118.80"):
        fee = Decimal("15.80") + sep * Decimal("0.46")
    elif sep < Decimal("315.53"):
        fee = Decimal("30.24") + sep * Decimal("0.33")
    elif sep < Decimal("1104.40"):
        fee = Decimal("86.11") + sep * Decimal("0.15")
    else:
        fee = Decimal("198.36") + sep * Decimal("0.05")
    return fee.quantize(Decimal("0.01"))


def calc_patient_price(sep_zar: Decimal) -> Decimal:
    """환자 최종 부담액 = SEP + 조제 수수료 + VAT 15%."""
    fee = calc_dispensing_fee(sep_zar)
    subtotal = sep_zar + fee
    vat = subtotal * Decimal("0.15")
    return (subtotal + vat).quantize(Decimal("0.01"))


def find_sweet_spot(
    competitor_sep_zar: Decimal,
    target_margin_pct: float = 5.0,
) -> dict[str, Decimal]:
    """
    약국 입장에서 조제 수수료 절대 금액이 최대화되는 SEP 구간 분석.
    경쟁사 SEP 대비 당사 제안 가격의 적정 구간을 반환.
    """
    fee_at_comp = calc_dispensing_fee(competitor_sep_zar)
    # 동일 구간 내에서 조제 마진이 최대화되는 SEP = 구간 상한 직전
    # 단순 비교: 당사 SEP를 경쟁사의 95% 수준으로 제안
    suggested_sep = (competitor_sep_zar * Decimal("0.95")).quantize(Decimal("0.01"))
    fee_at_suggested = calc_dispensing_fee(suggested_sep)
    return {
        "competitor_sep": competitor_sep_zar,
        "competitor_fee": fee_at_comp,
        "suggested_sep": suggested_sep,
        "suggested_fee": fee_at_suggested,
        "fee_delta": (fee_at_suggested - fee_at_comp).quantize(Decimal("0.01")),
    }


# ---------------------------------------------------------------------------
# SEP 데이터 레코드
# ---------------------------------------------------------------------------

@dataclass
class SepRecord:
    product_name: str
    api: str
    sep_zar: Decimal
    pack_size: str
    dispensing_fee_zar: Decimal
    patient_price_zar: Decimal
    nappi_code: str = ""
    manufacturer: str = ""
    uploaded_date: str = ""
    source_url: str = ""


# ---------------------------------------------------------------------------
# 크롤러
# ---------------------------------------------------------------------------

async def fetch_sep_database(emit: Any = None) -> list[SepRecord]:
    """MPR 웹사이트에서 최신 SEP 엑셀 파일 다운로드 및 파싱."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    download_url = await _detect_latest_excel_url(emit)
    if not download_url:
        if emit:
            await emit({"phase": "mpr", "message": "SEP 엑셀 URL 감지 실패 — 캐시 사용 시도", "level": "warn"})
        return _load_from_cache()

    cache_file = CACHE_DIR / _url_to_filename(download_url)
    if cache_file.exists():
        if emit:
            await emit({"phase": "mpr", "message": "SEP 캐시 파일 사용", "level": "info"})
        return _parse_excel(cache_file.read_bytes(), download_url)

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(download_url)
            resp.raise_for_status()
            data = resp.content
        cache_file.write_bytes(data)
        records = _parse_excel(data, download_url)
        if emit:
            await emit({
                "phase": "mpr",
                "message": f"SEP 데이터 {len(records)}건 파싱 완료",
                "level": "success",
            })
        return records
    except Exception as exc:
        if emit:
            await emit({"phase": "mpr", "message": f"SEP 다운로드 실패: {exc}", "level": "warn"})
        return _load_from_cache()


async def _detect_latest_excel_url(emit: Any) -> str | None:
    """MPR 페이지에서 가장 최근 엑셀 파일 링크 추출."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import]
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(MPR_SEARCH_URL)
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for link in soup.find_all("a", href=True):
            href = str(link["href"])
            if any(ext in href.lower() for ext in [".xlsx", ".xls", ".csv"]):
                if "sep" in href.lower() or "price" in href.lower() or "database" in href.lower():
                    return href if href.startswith("http") else MPR_BASE + href
        # 범용 엑셀 링크
        for link in soup.find_all("a", href=True):
            href = str(link["href"])
            if any(ext in href.lower() for ext in [".xlsx", ".xls"]):
                return href if href.startswith("http") else MPR_BASE + href
    except Exception:
        pass
    return None


def _parse_excel(data: bytes, source_url: str) -> list[SepRecord]:
    try:
        import pandas as pd  # type: ignore[import]
    except ImportError:
        return []

    try:
        df = pd.read_excel(io.BytesIO(data), dtype=str)
    except Exception:
        return []

    df.columns = [str(c).strip().lower() for c in df.columns]
    records: list[SepRecord] = []

    for _, row in df.iterrows():
        try:
            sep_raw = _find_col(row, ["sep", "single exit price", "price", "mfc sep"])
            if not sep_raw:
                continue
            sep_str = str(sep_raw).replace("R", "").replace(",", "").strip()
            sep = Decimal(sep_str)
            if sep <= 0:
                continue

            product = str(_find_col(row, ["product name", "name", "description"]) or "")
            api = str(_find_col(row, ["api", "active ingredient", "inn", "substance"]) or "")
            pack = str(_find_col(row, ["pack size", "pack", "quantity", "size"]) or "")
            nappi = str(_find_col(row, ["nappi", "nappi code"]) or "")
            mfr = str(_find_col(row, ["manufacturer", "applicant", "company"]) or "")

            records.append(SepRecord(
                product_name=product[:200],
                api=api[:100],
                sep_zar=sep,
                pack_size=pack[:50],
                dispensing_fee_zar=calc_dispensing_fee(sep),
                patient_price_zar=calc_patient_price(sep),
                nappi_code=nappi[:20],
                manufacturer=mfr[:100],
                source_url=source_url,
            ))
        except Exception:
            continue

    return records


def _find_col(row: Any, candidates: list[str]) -> Any:
    """컬럼명 후보 목록에서 첫 번째 유효 값 반환."""
    for col in candidates:
        val = row.get(col)
        if val and str(val).strip() and str(val).strip().lower() not in ("nan", "none", ""):
            return val
    return None


def _url_to_filename(url: str) -> str:
    import hashlib
    return hashlib.md5(url.encode()).hexdigest()[:12] + ".xlsx"


def _load_from_cache() -> list[SepRecord]:
    if not CACHE_DIR.exists():
        return []
    files = sorted(CACHE_DIR.glob("*.xlsx"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return []
    return _parse_excel(files[0].read_bytes(), str(files[0]))


async def search_sep_by_inn(inn_name: str, emit: Any = None) -> list[SepRecord]:
    """특정 INN명에 해당하는 SEP 레코드만 필터링."""
    all_records = await fetch_sep_database(emit)
    inn_lower = inn_name.lower()
    return [
        r for r in all_records
        if inn_lower in r.api.lower() or inn_lower in r.product_name.lower()
    ]
