"""SAHPRA (남아공 보건제품규제청) 크롤러.

기능:
  A. 등록 의약품 DB — API명, 등록번호, 등록사, 등록일, 상태
  B. 허가 시설 목록 — 유통사 라이선스 번호/만료일
  C. 안전성 경보 & 리콜 게시판 — 신규 공지 감지
주간 1회 실행 권장.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from bs4 import BeautifulSoup  # type: ignore[import]

SAHPRA_BASE = "https://www.sahpra.org.za"
# 실제 등록 의약품 DB는 아래 주소로 리다이렉트됨 (follow_redirects=True 필요)
SAHPRA_MEDAPPS = "https://medapps.sahpra.org.za:6006"

INN_LIST = [
    "Rosuvastatin", "Atorvastatin", "Cilostazol", "Mosapride",
    "Fluticasone", "Salmeterol", "Gadobutrol", "Hydroxyurea", "Omega-3",
]


@dataclass
class SahpraProduct:
    applicant: str
    product_name: str
    api: str
    registration_number: str
    application_number: str
    registration_date: str
    status: str
    source_url: str
    raw_text: str = ""


@dataclass
class SahpraLicence:
    company_name: str
    licence_number: str
    licence_type: str
    responsible_pharmacist: str
    issued_date: str
    expiry_date: str
    address: str
    source_url: str


@dataclass
class SafetyAlert:
    title: str
    date: str
    category: str       # recall | pharmacovigilance | safety_communication
    url: str
    summary: str = ""


async def crawl_sahpra_products(
    inn_names: list[str] | None = None,
    emit: Any = None,
) -> list[SahpraProduct]:
    targets = inn_names or INN_LIST
    results: list[SahpraProduct] = []

    # www.sahpra.org.za/registered-health-products/ → medapps.sahpra.org.za:6006 로 리다이렉트됨
    search_url = f"{SAHPRA_MEDAPPS}/"
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for inn in targets:
            try:
                records = await _search_products(client, inn, search_url, emit)
                results.extend(records)
                if emit:
                    await emit({
                        "phase": "sahpra",
                        "message": f"[{inn}] {len(records)}건 수집",
                        "level": "success" if records else "info",
                    })
            except Exception as exc:
                if emit:
                    await emit({"phase": "sahpra", "message": f"[{inn}] 오류: {exc}", "level": "warn"})

    return results


async def _search_products(
    client: httpx.AsyncClient,
    inn: str,
    base_url: str,
    emit: Any,
) -> list[SahpraProduct]:
    # SAHPRA 검색 폼: GET 파라미터로 검색어 전송
    params = {"search": inn, "type": "medicine"}
    try:
        resp = await client.get(base_url, params=params)
        resp.raise_for_status()
    except Exception:
        # 검색 API 엔드포인트가 다를 경우 직접 접근
        try:
            api_url = f"{SAHPRA_BASE}/api/registered-products/"
            resp = await client.get(api_url, params={"q": inn})
            resp.raise_for_status()
        except Exception:
            return []

    return _parse_products_page(resp.text, inn, str(resp.url))


def _parse_products_page(html: str, inn: str, source_url: str) -> list[SahpraProduct]:
    soup = BeautifulSoup(html, "html.parser")
    records: list[SahpraProduct] = []

    # 테이블 기반 파싱
    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")[1:]  # 헤더 스킵
        for row in rows:
            cols = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            if len(cols) < 4:
                continue
            # 컬럼 순서: Applicant, Product Name, API, Reg#, App#, Reg Date, Status (가변적)
            records.append(SahpraProduct(
                applicant=cols[0] if len(cols) > 0 else "",
                product_name=cols[1] if len(cols) > 1 else "",
                api=cols[2] if len(cols) > 2 else inn,
                registration_number=cols[3] if len(cols) > 3 else "",
                application_number=cols[4] if len(cols) > 4 else "",
                registration_date=cols[5] if len(cols) > 5 else "",
                status=cols[6] if len(cols) > 6 else "Unknown",
                source_url=source_url,
                raw_text=" | ".join(cols[:8]),
            ))

    return records[:50]  # 최대 50건


async def crawl_sahpra_licences(emit: Any = None) -> list[SahpraLicence]:
    licence_url = f"{SAHPRA_BASE}/pharmaceutical-licenced-establishments/"
    records: list[SahpraLicence] = []

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            resp = await client.get(licence_url)
            resp.raise_for_status()
            records = _parse_licences_page(resp.text, str(resp.url))
            if emit:
                await emit({
                    "phase": "sahpra_licences",
                    "message": f"라이선스 {len(records)}건 수집",
                    "level": "success",
                })
        except Exception as exc:
            if emit:
                await emit({"phase": "sahpra_licences", "message": f"오류: {exc}", "level": "warn"})

    return records


def _parse_licences_page(html: str, source_url: str) -> list[SahpraLicence]:
    soup = BeautifulSoup(html, "html.parser")
    records: list[SahpraLicence] = []

    for table in soup.find_all("table"):
        for row in table.find_all("tr")[1:]:
            cols = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            if len(cols) < 5:
                continue
            records.append(SahpraLicence(
                company_name=cols[0] if len(cols) > 0 else "",
                licence_number=cols[1] if len(cols) > 1 else "",
                licence_type=cols[2] if len(cols) > 2 else "",
                responsible_pharmacist=cols[3] if len(cols) > 3 else "",
                issued_date=cols[4] if len(cols) > 4 else "",
                expiry_date=cols[5] if len(cols) > 5 else "",
                address=cols[6] if len(cols) > 6 else "",
                source_url=source_url,
            ))

    return records


async def crawl_safety_alerts(emit: Any = None) -> list[SafetyAlert]:
    alerts_url = f"{SAHPRA_BASE}/safety-information-and-updates/"
    results: list[SafetyAlert] = []

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        try:
            resp = await client.get(alerts_url)
            resp.raise_for_status()
            results = _parse_alerts(resp.text, str(resp.url))
            if emit:
                await emit({
                    "phase": "sahpra_alerts",
                    "message": f"안전성 공지 {len(results)}건 수집",
                    "level": "success" if results else "info",
                })
        except Exception as exc:
            if emit:
                await emit({"phase": "sahpra_alerts", "message": f"오류: {exc}", "level": "warn"})

    return results


def _parse_alerts(html: str, source_url: str) -> list[SafetyAlert]:
    soup = BeautifulSoup(html, "html.parser")
    alerts: list[SafetyAlert] = []

    for link in soup.select("a[href]"):
        href = link.get("href", "")
        title = link.get_text(strip=True)
        if not title or len(title) < 10:
            continue

        cat = "safety_communication"
        text_lower = title.lower()
        if "recall" in text_lower:
            cat = "recall"
        elif "pharmacovigilance" in text_lower or "adverse" in text_lower:
            cat = "pharmacovigilance"

        if any(kw in text_lower for kw in ["recall", "alert", "warning", "safety", "pharmacovigilance"]):
            full_url = href if href.startswith("http") else SAHPRA_BASE + href
            alerts.append(SafetyAlert(
                title=title,
                date="",
                category=cat,
                url=full_url,
            ))

    return alerts[:30]
