"""남아프리카공화국(ZA) 의약품 시장 분석 오케스트레이터.

주요 흐름:
  1. 크롤러 6종(Clicks, Dis-Chem, SAHPRA, MPR, MHPL, eTender) 병렬 실행
  2. za_parser.py로 ZAR 파싱 및 이상치 탐지
  3. Supabase za_pricing 테이블에 INSERT
  4. SEP 조제수수료 스윗스팟 계산 (민간 시장)
  5. MHPL No-Bid 기회 탐지 (공공 시장)
  6. Claude API로 8품목 수출 적합성 판정
"""

from __future__ import annotations

import asyncio
import os
import time
from decimal import Decimal
from typing import Any

DEFAULT_INN_NAMES: list[str] = [
    "Rosuvastatin",
    "Atorvastatin",
    "Cilostazol",
    "Mosapride",
    "Fluticasone",
    "Gadobutrol",
    "Hydroxyurea",
    "Omega-3",
]

PRODUCT_MAP: dict[str, str] = {
    "Rosuvastatin": "rosumeg_combigel",
    "Atorvastatin": "atmeg_combigel",
    "Cilostazol":   "ciloduo",
    "Mosapride":    "gastiin_cr",
    "Fluticasone":  "sereterol_activair",
    "Gadobutrol":   "gadvoa_inj",
    "Hydroxyurea":  "hydrine",
    "Omega-3":      "omethyl_cutielet",
}

PRODUCT_LABELS: dict[str, str] = {
    "rosumeg_combigel":  "Rosumeg Combigel (Rosuvastatin + Omega-3)",
    "atmeg_combigel":    "Atmeg Combigel (Atorvastatin + Omega-3)",
    "ciloduo":           "Ciloduo (Cilostazol + Rosuvastatin)",
    "gastiin_cr":        "Gastiin CR (Mosapride Citrate 서방정)",
    "sereterol_activair":"Sereterol Activair (Fluticasone + Salmeterol)",
    "gadvoa_inj":        "Gadvoa Inj. (Gadobutrol PFS)",
    "hydrine":           "Hydrine (Hydroxyurea 500mg)",
    "omethyl_cutielet":  "Omethyl Cutielet (Omega-3 2g Pouch)",
}


async def run_all_crawlers(
    inn_names: list[str] | None = None,
    emit: Any = None,
) -> dict[str, Any]:
    from utils.za_clicks_crawler import crawl_clicks
    from utils.za_dischem_crawler import crawl_dischem
    from utils.za_sahpra_crawler import crawl_sahpra_products, crawl_safety_alerts
    from utils.za_mpr_crawler import fetch_sep_database
    from utils.za_mhpl_crawler import fetch_mhpl, get_no_bid_items
    from utils.za_etender_crawler import crawl_etenders

    targets = inn_names or DEFAULT_INN_NAMES

    if emit:
        await emit({"phase": "za_crawl", "message": "ZA 크롤러 6종 병렬 시작", "level": "info"})

    # 소매 크롤러: INN별 병렬
    retail_tasks = []
    for inn in targets:
        retail_tasks.append(crawl_clicks(inn, emit=emit))
        retail_tasks.append(crawl_dischem(inn, emit=emit))

    retail_results = await asyncio.gather(*retail_tasks, return_exceptions=True)

    # 규제/가격/조달: 전체 데이터셋 단위
    reg_results = await asyncio.gather(
        crawl_sahpra_products(targets, emit=emit),
        crawl_safety_alerts(emit=emit),
        fetch_sep_database(emit=emit),
        fetch_mhpl(emit=emit),
        crawl_etenders(emit=emit),
        return_exceptions=True,
    )

    # 소매 결과 INN별 집계
    retail_by_inn: dict[str, list[Any]] = {inn: [] for inn in targets}
    for i, result in enumerate(retail_results):
        if isinstance(result, Exception):
            continue
        inn = targets[(i // 2)]
        retail_by_inn[inn].extend(result or [])

    sahpra_products  = reg_results[0] if not isinstance(reg_results[0], Exception) else []
    safety_alerts    = reg_results[1] if not isinstance(reg_results[1], Exception) else []
    sep_records      = reg_results[2] if not isinstance(reg_results[2], Exception) else []
    mhpl_records     = reg_results[3] if not isinstance(reg_results[3], Exception) else []
    tenders          = reg_results[4] if not isinstance(reg_results[4], Exception) else []

    no_bid_items = get_no_bid_items(mhpl_records) if mhpl_records else []

    return {
        "retail_by_inn": retail_by_inn,
        "sahpra_products": sahpra_products,
        "safety_alerts": safety_alerts,
        "sep_records": sep_records,
        "mhpl_records": mhpl_records,
        "tenders": tenders,
        "no_bid_items": no_bid_items,
    }


async def analyze_za_market(
    inn_names: list[str] | None = None,
    save_db: bool = True,
    emit: Any = None,
) -> dict[str, Any]:
    start = time.time()
    targets = inn_names or DEFAULT_INN_NAMES

    crawl_data = await run_all_crawlers(targets, emit=emit)

    from utils.za_parser import detect_outliers, build_db_row

    all_db_rows: list[dict[str, Any]] = []
    sep_analysis: dict[str, Any] = {}

    for inn in targets:
        retail_list = crawl_data["retail_by_inn"].get(inn, [])
        cleaned = detect_outliers(retail_list)
        product_id = PRODUCT_MAP.get(inn)
        rows = [build_db_row(r, product_id) for r in cleaned]
        all_db_rows.extend(rows)

        # SEP 스윗스팟 분석
        from utils.za_mpr_crawler import search_sep_by_inn, find_sweet_spot
        sep_recs = [r for r in crawl_data["sep_records"]
                    if inn.lower() in r.api.lower() or inn.lower() in r.product_name.lower()]
        if sep_recs:
            avg_sep = sum(r.sep_zar for r in sep_recs) / len(sep_recs)
            sep_analysis[inn] = find_sweet_spot(avg_sep)

    saved_count = 0
    if save_db:
        saved_count = await _save_to_supabase(all_db_rows)
        if emit:
            await emit({
                "phase": "za_db",
                "message": f"Supabase za_pricing {saved_count}건 적재",
                "level": "success",
            })

    total_retail = sum(len(v) for v in crawl_data["retail_by_inn"].values())
    elapsed = round(time.time() - start, 1)

    return {
        "ok": True,
        "elapsed_sec": elapsed,
        "inn_names": targets,
        "total_retail_collected": total_retail,
        "sahpra_products": len(crawl_data["sahpra_products"]),
        "sep_records": len(crawl_data["sep_records"]),
        "mhpl_records": len(crawl_data["mhpl_records"]),
        "tenders": len(crawl_data["tenders"]),
        "no_bid_opportunities": len(crawl_data["no_bid_items"]),
        "safety_alerts": len(crawl_data["safety_alerts"]),
        "sep_analysis": sep_analysis,
        "saved_to_db": saved_count,
    }


async def _save_to_supabase(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    try:
        from utils.db import get_supabase_client
        sb = get_supabase_client()
        result = sb.table("za_pricing").insert(rows).execute()
        return len(result.data) if result.data else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Claude 기반 수출 적합성 분석
# ---------------------------------------------------------------------------

async def analyze_all(
    use_perplexity: bool = True,
    emit: Any = None,
) -> list[dict[str, Any]]:
    """8품목 전체 수출 적합성 판정."""
    import os
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return [_no_api_result(pid) for pid in PRODUCT_LABELS]

    crawl_data = await run_all_crawlers(emit=emit)
    results = []

    for inn, product_id in PRODUCT_MAP.items():
        if emit:
            await emit({"phase": "analyze", "message": f"{product_id} 분석 중…", "level": "info"})
        result = await _analyze_single(
            inn=inn,
            product_id=product_id,
            crawl_data=crawl_data,
            use_perplexity=use_perplexity,
            emit=emit,
        )
        results.append(result)

    return results


async def _analyze_single(
    inn: str,
    product_id: str,
    crawl_data: dict[str, Any],
    use_perplexity: bool,
    emit: Any,
) -> dict[str, Any]:
    import os
    import anthropic  # type: ignore[import]

    retail_list = crawl_data["retail_by_inn"].get(inn, [])
    sep_recs = [r for r in crawl_data["sep_records"]
                if inn.lower() in r.api.lower() or inn.lower() in r.product_name.lower()]
    sahpra_recs = [p for p in crawl_data["sahpra_products"]
                   if inn.lower() in p.api.lower() or inn.lower() in p.product_name.lower()]
    mhpl_recs = [m for m in crawl_data["mhpl_records"]
                 if inn.lower() in m.inn_name.lower() or inn.lower() in m.description.lower()]
    no_bids = [m for m in crawl_data["no_bid_items"]
               if inn.lower() in m.inn_name.lower() or inn.lower() in m.description.lower()]

    context_parts = [
        f"제품: {PRODUCT_LABELS.get(product_id, product_id)}",
        f"INN: {inn}",
        f"\n=== 소매 약가 (Clicks/Dis-Chem) ===",
    ]
    for r in retail_list[:5]:
        context_parts.append(
            f"  {r.brand_name} — R{r.total_price_zar} "
            f"(ClubCard: R{r.clubcard_price_zar or '-'})"
        )

    context_parts.append(f"\n=== SEP (단일 출고가) ===")
    for s in sep_recs[:5]:
        context_parts.append(
            f"  {s.product_name} — SEP: R{s.sep_zar} "
            f"/ 조제수수료: R{s.dispensing_fee_zar} "
            f"/ 환자 최종가: R{s.patient_price_zar}"
        )

    context_parts.append(f"\n=== SAHPRA 등록 현황 ===")
    if sahpra_recs:
        for p in sahpra_recs[:3]:
            context_parts.append(f"  {p.applicant} — {p.product_name} ({p.status})")
    else:
        context_parts.append("  등록된 경쟁 제품 없음 (진입 기회)")

    context_parts.append(f"\n=== 공공 조달 (MHPL) ===")
    for m in mhpl_recs[:3]:
        context_parts.append(f"  {m.description} — {m.supplier} / R{m.price_incl_vat_zar} ({m.eml_status})")
    if no_bids:
        context_parts.append(f"  ⚠️ No-Bid 항목 {len(no_bids)}건 — 공급 공백 기회 존재")

    context = "\n".join(context_parts)

    perplexity_insight = ""
    if use_perplexity:
        perplexity_insight = await _fetch_perplexity(inn)

    system_prompt = (
        "당신은 남아프리카공화국(ZA) 의약품 시장 전문 분석가입니다. "
        "한국 제약사의 완제의약품 수출 적합성을 판정하되 반드시 한국어로 답변하세요.\n\n"
        "판정 기준:\n"
        "- 적합: SAHPRA 미등록 경쟁사 없거나 극소수, SEP 경쟁력 있음, EML 등재 가능성 높음\n"
        "- 조건부: 경쟁 존재하나 가격/품질 우위 가능, 추가 규제 작업 필요\n"
        "- 부적합: 강력한 경쟁 제품 다수, SEP 경쟁 불가, 특허 장벽 존재\n\n"
        "남아공의 이중 시장(공공 80%/민간 20%) 특성, NHI 도입 동향, "
        "SEP 조제수수료 구조를 반드시 분석에 반영하세요."
    )

    user_content = (
        f"아래 데이터를 기반으로 {PRODUCT_LABELS.get(product_id)}의 남아공 수출 적합성을 판정하세요.\n\n"
        f"{context}\n"
    )
    if perplexity_insight:
        user_content += f"\n=== 최신 시장 인사이트 ===\n{perplexity_insight}\n"
    user_content += (
        "\n판정(적합/조건부/부적합), 주요 근거 3가지, "
        "민간/공공 시장 각각의 진입 전략, "
        "추천 수출 단가 범위(ZAR/KRW)를 포함하여 답변하세요."
    )

    try:
        client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        analysis_text = response.content[0].text if response.content else ""
    except Exception as exc:
        analysis_text = f"Claude 분석 실패: {exc}"

    verdict = "조건부"
    text_lower = analysis_text.lower()
    if "부적합" in analysis_text:
        verdict = "부적합"
    elif "적합" in analysis_text and "조건부" not in analysis_text:
        verdict = "적합"

    return {
        "product_id": product_id,
        "inn_name": inn,
        "label": PRODUCT_LABELS.get(product_id, product_id),
        "verdict": verdict,
        "analysis": analysis_text,
        "retail_count": len(retail_list),
        "sep_count": len(sep_recs),
        "sahpra_count": len(sahpra_recs),
        "mhpl_count": len(mhpl_recs),
        "no_bid_count": len(no_bids),
        "references": [],
    }


async def _fetch_perplexity(inn: str) -> str:
    import os
    import httpx

    px_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    if not px_key:
        return ""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={"Authorization": f"Bearer {px_key}"},
                json={
                    "model": "sonar-pro",
                    "messages": [{
                        "role": "user",
                        "content": (
                            f"South Africa pharmaceutical market for {inn}: "
                            "SAHPRA registration status, SEP price range, "
                            "major competitors, NHI impact. 3 bullet points max."
                        ),
                    }],
                    "max_tokens": 300,
                    "temperature": 0.1,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        return ""


def _no_api_result(product_id: str) -> dict[str, Any]:
    return {
        "product_id": product_id,
        "inn_name": "",
        "label": PRODUCT_LABELS.get(product_id, product_id),
        "verdict": "미분석",
        "analysis": "ANTHROPIC_API_KEY 미설정 — Claude 분석을 실행할 수 없습니다.",
        "retail_count": 0,
        "sep_count": 0,
        "sahpra_count": 0,
        "mhpl_count": 0,
        "no_bid_count": 0,
        "references": [],
    }


# ---------------------------------------------------------------------------
# 파이프라인 호환 헬퍼 (server.py pipeline 엔드포인트용)
# ---------------------------------------------------------------------------

# Reverse map: product_id → INN
_PRODUCT_ID_TO_INN: dict[str, str] = {v: k for k, v in PRODUCT_MAP.items()}


async def analyze_product(product_id: str, emit: Any = None) -> dict[str, Any]:
    """단일 품목 ZA 수출 적합성 분석 (파이프라인 엔드포인트용)."""
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return _no_api_result(product_id)
    inn = _PRODUCT_ID_TO_INN.get(product_id)
    if not inn:
        return _no_api_result(product_id)
    crawl_data = await run_all_crawlers([inn], emit=emit)
    return await _analyze_single(
        inn=inn,
        product_id=product_id,
        crawl_data=crawl_data,
        use_perplexity=True,
        emit=emit,
    )


async def analyze_custom_product(
    trade_name: str,
    inn: str,
    dosage_form: str = "",
    emit: Any = None,
) -> dict[str, Any]:
    """사용자 정의 신약 ZA 수출 적합성 분석."""
    import os
    import anthropic  # type: ignore[import]

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return {
            "product_id": "custom",
            "inn_name": inn,
            "label": f"{trade_name} ({inn})",
            "verdict": "미분석",
            "analysis": "ANTHROPIC_API_KEY 미설정 — Claude 분석을 실행할 수 없습니다.",
            "references": [],
        }

    crawl_data = await run_all_crawlers([inn], emit=emit)

    system_prompt = (
        "당신은 남아프리카공화국(ZA) 의약품 시장 전문 분석가입니다. "
        "한국 제약사의 완제의약품 수출 적합성을 판정하되 반드시 한국어로 답변하세요."
    )
    user_content = (
        f"신약 '{trade_name}' (INN: {inn}, 제형: {dosage_form or '미상'})의 "
        f"남아공 수출 적합성을 판정하세요.\n\n"
        f"판정(적합/조건부/부적합), 주요 근거 3가지, "
        f"민간/공공 시장 각각의 진입 전략, 추천 수출 단가 범위(ZAR/KRW)를 포함해 답변하세요."
    )

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        analysis_text = response.content[0].text if response.content else ""
    except Exception as exc:
        analysis_text = f"Claude 분석 실패: {exc}"

    verdict = "조건부"
    if "부적합" in analysis_text:
        verdict = "부적합"
    elif "적합" in analysis_text and "조건부" not in analysis_text:
        verdict = "적합"

    return {
        "product_id": "custom",
        "inn_name": inn,
        "label": f"{trade_name} ({inn})",
        "verdict": verdict,
        "analysis": analysis_text,
        "retail_count": len(crawl_data["retail_by_inn"].get(inn, [])),
        "sep_count": len(crawl_data["sep_records"]),
        "sahpra_count": len(crawl_data["sahpra_products"]),
        "mhpl_count": len(crawl_data["mhpl_records"]),
        "no_bid_count": len(crawl_data["no_bid_items"]),
        "references": [],
    }
