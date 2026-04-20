"""남아공 국가 eTender 포털 크롤러.

대상: https://www.etenders.gov.za
필터: Category = Supplies: Medical + NDoH/주립 보건부
수집: 입찰번호, 설명, 게시일, 마감일, 첨부 PDF 링크
방식: Playwright (+ 버튼 클릭 자동화)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

ETENDER_BASE = "https://www.etenders.gov.za"
ETENDER_SEARCH = f"{ETENDER_BASE}/content/notices"

PLAYWRIGHT_LIVE = os.environ.get("PLAYWRIGHT_LIVE", "0") == "1"

MEDICAL_KEYWORDS = [
    "pharmaceutical", "medicine", "drug", "vaccine", "medical supply",
    "contrast media", "oncology", "cardiovascular", "inhaler",
    "gadobutrol", "hydroxyurea", "fluticasone", "rosuvastatin",
    "cilostazol", "mosapride", "omega-3",
]

ORGAN_TARGETS = [
    "national department of health",
    "gauteng department of health",
    "western cape department of health",
    "kwazulu-natal department of health",
    "eastern cape department of health",
]


@dataclass
class TenderRecord:
    tender_no: str
    description: str
    organ_of_state: str
    published_date: str
    closing_date: str
    attachment_urls: list[str] = field(default_factory=list)
    status: str = "open"
    source_url: str = ""
    raw_text: str = ""


async def crawl_etenders(
    keywords: list[str] | None = None,
    emit: Any = None,
) -> list[TenderRecord]:
    search_terms = keywords or ["pharmaceutical", "medicine", "contrast media"]
    results: list[TenderRecord] = []

    for term in search_terms[:3]:
        try:
            records = await _fetch_tenders(term, emit)
            results.extend(records)
            if emit:
                await emit({
                    "phase": "etender",
                    "message": f"[{term}] {len(records)}건 수집",
                    "level": "success" if records else "info",
                })
        except Exception as exc:
            if emit:
                await emit({"phase": "etender", "message": f"[{term}] 오류: {exc}", "level": "warn"})

    # 중복 제거
    seen: set[str] = set()
    unique: list[TenderRecord] = []
    for r in results:
        key = r.tender_no or r.description[:50]
        if key not in seen:
            seen.add(key)
            unique.append(r)

    return unique


async def _fetch_tenders(keyword: str, emit: Any) -> list[TenderRecord]:
    if PLAYWRIGHT_LIVE:
        return await _playwright_fetch(keyword, emit)
    return await _static_fetch(keyword, emit)


async def _playwright_fetch(keyword: str, emit: Any) -> list[TenderRecord]:
    try:
        from playwright.async_api import async_playwright  # type: ignore[import]
    except ImportError:
        return await _static_fetch(keyword, emit)

    records: list[TenderRecord] = []
    search_url = (
        f"{ETENDER_SEARCH}?keyword={keyword.replace(' ', '+')}"
        "&category=Supplies%3A+Medical"
    )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(search_url, timeout=40_000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            # 결과 행의 확장 버튼(+) 클릭하여 첨부파일 링크 노출
            expand_btns = await page.query_selector_all("[class*='expand'], [data-toggle='collapse'], button.detail-btn")
            for btn in expand_btns[:10]:
                try:
                    await btn.click()
                    await page.wait_for_timeout(500)
                except Exception:
                    pass

            html = await page.content()
            records = _parse_etender_html(html, search_url)
        except Exception as exc:
            if emit:
                await emit({"phase": "etender", "message": f"Playwright eTender 오류: {exc}", "level": "warn"})
        finally:
            await page.close()
            await browser.close()

    return records


async def _static_fetch(keyword: str, emit: Any) -> list[TenderRecord]:
    search_url = (
        f"{ETENDER_SEARCH}?keyword={keyword.replace(' ', '+')}"
        "&category=Supplies%3A+Medical"
    )
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(search_url)
            resp.raise_for_status()
        return _parse_etender_html(resp.text, search_url)
    except Exception as exc:
        if emit:
            await emit({"phase": "etender", "message": f"eTender 정적 수집 실패: {exc}", "level": "warn"})
        return []


def _parse_etender_html(html: str, source_url: str) -> list[TenderRecord]:
    from bs4 import BeautifulSoup  # type: ignore[import]

    soup = BeautifulSoup(html, "html.parser")
    records: list[TenderRecord] = []

    rows = (
        soup.select("tr.tender-row")
        or soup.select("[class*='tender-item']")
        or soup.select("table tbody tr")
    )

    for row in rows[:30]:
        cols = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
        if len(cols) < 3:
            continue

        # 보건 관련 필터
        row_text = " ".join(cols).lower()
        if not any(kw in row_text for kw in MEDICAL_KEYWORDS):
            organ_ok = any(org in row_text for org in ORGAN_TARGETS)
            if not organ_ok:
                continue

        tender_no = cols[0] if cols else ""
        description = cols[1] if len(cols) > 1 else ""
        organ = cols[2] if len(cols) > 2 else ""
        pub_date = cols[3] if len(cols) > 3 else ""
        close_date = cols[4] if len(cols) > 4 else ""

        # 첨부파일 PDF 링크 수집
        attachment_urls: list[str] = []
        for link in row.find_all("a", href=True):
            href = str(link["href"])
            if ".pdf" in href.lower() or "download" in href.lower():
                full = href if href.startswith("http") else ETENDER_BASE + href
                attachment_urls.append(full)

        records.append(TenderRecord(
            tender_no=tender_no[:50],
            description=description[:300],
            organ_of_state=organ[:150],
            published_date=pub_date[:30],
            closing_date=close_date[:30],
            attachment_urls=attachment_urls[:5],
            source_url=source_url,
            raw_text=" | ".join(cols[:6])[:500],
        ))

    return records
