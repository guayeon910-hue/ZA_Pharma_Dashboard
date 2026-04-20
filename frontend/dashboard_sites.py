"""대시보드에 표시할 남아프리카공화국(ZA) 소스 라벨 (한국어)."""

from __future__ import annotations

from typing import Any, TypedDict


class SiteDef(TypedDict):
    id: str
    name: str
    hint: str
    domain: str


DASHBOARD_SITES: tuple[SiteDef, ...] = (
    {
        "id": "clicks",
        "name": "Clicks · 소매 약국 (1위 체인)",
        "hint": "남아공 최대 약국 체인 (930개 매장) — 일반가 vs ClubCard 할인가 DOM 분리 수집 (Playwright)",
        "domain": "clicks.co.za",
    },
    {
        "id": "dischem",
        "name": "Dis-Chem · 소매 약국 (2위 체인)",
        "hint": "Dis-Chem Loyalty Benefit 할인율 별도 컬럼 저장 — 프로모션 주기 패턴 분석 (Playwright)",
        "domain": "dischem.co.za",
    },
    {
        "id": "sahpra",
        "name": "SAHPRA · 보건제품규제청",
        "hint": "등록 의약품 DB + 허가 시설 라이선스 + 안전성 경보/리콜 게시판 (주 1회, httpx+BS4)",
        "domain": "sahpra.org.za",
    },
    {
        "id": "mpr",
        "name": "MPR · 단일 출고가 레지스트리",
        "hint": "Medicine Price Registry SEP 엑셀 다운로드 + 법정 조제수수료 4구간 역산 (월 단위)",
        "domain": "mpr.gov.za",
    },
    {
        "id": "mhpl",
        "name": "NDoH · 마스터 보건 제품 목록",
        "hint": "보건부 공공 조달 마스터 카탈로그 — No-Bid 공급 공백 자동 감지 (월 1회, Excel 파싱)",
        "domain": "health.gov.za",
    },
    {
        "id": "etender",
        "name": "eTender · 국가 조달 포털",
        "hint": "NDoH 및 주립 보건부 의약품 입찰 실시간 모니터링 — 첨부 PDF 자동 수집 (Playwright)",
        "domain": "etenders.gov.za",
    },
)


def initial_site_states() -> dict[str, dict[str, Any]]:
    return {
        s["id"]: {
            "status": "pending",
            "message": "아직 시작 전이에요",
            "ts": 0.0,
        }
        for s in DASHBOARD_SITES
    }
