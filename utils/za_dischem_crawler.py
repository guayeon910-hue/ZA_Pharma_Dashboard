"""Dis-Chem 온라인 약국 크롤러 (남아공 2위 약국 체인).

수집 대상: https://www.dischem.co.za
- 일반 소매 정가
- Dis-Chem Loyalty Benefit 할인가
접근 방식: Playwright → Jina AI 폴백
"""

from __future__ import annotations

import asyncio
import os
import re
from decimal import Decimal
from typing import Any

from utils.za_parser import DrugRecord, parse_zar, parse_pack_size
from utils.za_clicks_crawler import INN_SEARCH_MAP  # 동일 검색 키워드 재사용

BASE_URL = "https://www.dischem.co.za"
PLAYWRIGHT_LIVE = os.environ.get("PLAYWRIGHT_LIVE", "0") == "1"


async def crawl_dischem(
    inn_name: str,
    emit: Any = None,
) -> list[DrugRecord]:
    keywords = INN_SEARCH_MAP.get(inn_name, [inn_name.lower()])
    results: list[DrugRecord] = []

    for kw in keywords:
        try:
            records = await _fetch_keyword(kw, inn_name, emit)
            results.extend(records)
        except Exception as exc:
            if emit:
                await emit({"phase": "dischem", "message": f"[{kw}] 오류: {exc}", "level": "warn"})

    return results


async def _fetch_keyword(
    keyword: str,
    inn_name: str,
    emit: Any,
) -> list[DrugRecord]:
    search_url = f"{BASE_URL}/search?q={keyword.replace(' ', '+')}"
    if PLAYWRIGHT_LIVE:
        return await _playwright_fetch(search_url, keyword, inn_name, emit)
    return await _jina_fetch(search_url, keyword, inn_name, emit)


async def _playwright_fetch(
    url: str,
    keyword: str,
    inn_name: str,
    emit: Any,
) -> list[DrugRecord]:
    try:
        from playwright.async_api import async_playwright  # type: ignore[import]
    except ImportError:
        return await _jina_fetch(url, keyword, inn_name, emit)

    records: list[DrugRecord] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await ctx.new_page()
        try:
            await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)
            html = await page.content()
            records = _parse_dischem_html(html, inn_name, keyword, url)
        except Exception as exc:
            if emit:
                await emit({"phase": "dischem", "message": f"Playwright 오류 [{keyword}]: {exc}", "level": "warn"})
        finally:
            await ctx.close()
            await browser.close()

    if not records:
        records = await _jina_fetch(url, keyword, inn_name, emit)
    return records


async def _jina_fetch(
    url: str,
    keyword: str,
    inn_name: str,
    emit: Any,
) -> list[DrugRecord]:
    import httpx

    jina_key = os.environ.get("JINA_API_KEY", "")
    jina_url = f"https://r.jina.ai/{url}"
    headers: dict[str, str] = {"Accept": "text/plain"}
    if jina_key:
        headers["Authorization"] = f"Bearer {jina_key}"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(jina_url, headers=headers)
            resp.raise_for_status()
            text = resp.text
        return _parse_dischem_text(text, inn_name, keyword, url)
    except Exception as exc:
        if emit:
            await emit({"phase": "dischem", "message": f"Jina 폴백 실패 [{keyword}]: {exc}", "level": "warn"})
        return []


def _parse_dischem_html(
    html: str,
    inn_name: str,
    keyword: str,
    source_url: str,
) -> list[DrugRecord]:
    from bs4 import BeautifulSoup  # type: ignore[import]

    soup = BeautifulSoup(html, "html.parser")
    records: list[DrugRecord] = []

    product_cards = (
        soup.select("[data-testid='product-card']")
        or soup.select(".product-card")
        or soup.select("[class*='product-item']")
        or soup.select("[class*='ProductCard']")
        or soup.select("li.product-item")
    )

    for card in product_cards[:20]:
        try:
            rec = _extract_dischem_card(card, inn_name, source_url)
            if rec:
                records.append(rec)
        except Exception:
            continue

    return records


def _extract_dischem_card(card: Any, inn_name: str, source_url: str) -> DrugRecord | None:
    name_el = (
        card.select_one(".product-name")
        or card.select_one("[class*='ProductName']")
        or card.select_one("h3")
        or card.select_one("h2")
    )
    if not name_el:
        return None
    brand_name = name_el.get_text(strip=True)
    if not brand_name:
        return None

    # 일반가
    price_el = (
        card.select_one(".price")
        or card.select_one("[class*='RegularPrice']")
        or card.select_one("[class*='original-price']")
    )
    regular_price = parse_zar(price_el.get_text(strip=True)) if price_el else None
    if not regular_price:
        return None

    # Loyalty Benefit 할인가
    benefit_el = (
        card.select_one("[class*='BenefitPrice']")
        or card.select_one("[class*='loyalty']")
        or card.select_one("[class*='special-price']")
    )
    benefit_price = parse_zar(benefit_el.get_text(strip=True)) if benefit_el else None

    # 프로모션 플래그
    promo_el = card.select_one("[class*='promo']") or card.select_one("[class*='sale']")
    promo_flag = promo_el is not None

    pack_text = ""
    pack_el = card.select_one("[class*='pack']") or card.select_one("[class*='size']")
    if pack_el:
        pack_text = pack_el.get_text(strip=True)
    pack_size, unit_count = parse_pack_size(pack_text or brand_name)
    per_unit = (regular_price / unit_count) if unit_count > 0 else regular_price

    link_el = card.select_one("a[href]")
    product_url = (BASE_URL + link_el["href"]) if link_el else source_url

    return DrugRecord(
        inn_name=inn_name,
        brand_name=brand_name,
        source_site="dischem",
        source_url=product_url,
        total_price_zar=regular_price,
        price_per_unit_zar=per_unit,
        pack_size=pack_size,
        benefit_price_zar=benefit_price,
        promo_flag=promo_flag,
        confidence=0.80,
        raw_text=card.get_text(separator=" ", strip=True)[:300],
    )


def _parse_dischem_text(
    text: str,
    inn_name: str,
    keyword: str,
    source_url: str,
) -> list[DrugRecord]:
    records: list[DrugRecord] = []
    price_pattern = re.compile(r"([\w\s\+\-\/,]+?)\s+R\s*([\d,]+\.?\d*)", re.IGNORECASE)
    for m in price_pattern.finditer(text):
        brand = m.group(1).strip()[-80:]
        price = parse_zar("R " + m.group(2))
        if not price or price < Decimal("5"):
            continue
        pack_size, unit_count = parse_pack_size(brand)
        per_unit = (price / unit_count) if unit_count > 0 else price
        records.append(DrugRecord(
            inn_name=inn_name,
            brand_name=brand,
            source_site="dischem",
            source_url=source_url,
            total_price_zar=price,
            price_per_unit_zar=per_unit,
            pack_size=pack_size,
            confidence=0.65,
            raw_text=brand[:200],
        ))
        if len(records) >= 10:
            break
    return records
