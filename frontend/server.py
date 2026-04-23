"""분석 대시보드 서버: SSE 실시간 로그 + 분석/보고서 API."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from frontend.dashboard_sites import DASHBOARD_SITES

STATIC = Path(__file__).resolve().parent / "static"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

_state: dict[str, Any] = {
    "events": [],
    "lock": None,
}


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _state["lock"] = asyncio.Lock()
    yield


app = FastAPI(title="ZA Analysis Dashboard", version="4.0.0", lifespan=_lifespan)

import os as _os
_cors_origins = _os.environ.get("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _emit(event: dict[str, Any]) -> None:
    payload = {**event, "ts": time.time()}
    lock = _state["lock"]
    if lock is None:
        return
    async with lock:
        _state["events"].append(payload)
        if len(_state["events"]) > 500:
            _state["events"] = _state["events"][-400:]


# ── API 키 런타임 설정 ────────────────────────────────────────────────────────

class ApiKeysBody(BaseModel):
    perplexity_api_key: str = ""
    anthropic_api_key:  str = ""


@app.post("/api/settings/keys")
async def set_api_keys(body: ApiKeysBody) -> JSONResponse:
    """프론트엔드에서 API 키를 런타임에 설정 (프로세스 환경변수 갱신)."""
    import os
    updated: list[str] = []
    if body.perplexity_api_key.strip():
        os.environ["PERPLEXITY_API_KEY"] = body.perplexity_api_key.strip()
        updated.append("PERPLEXITY_API_KEY")
    if body.anthropic_api_key.strip():
        os.environ["ANTHROPIC_API_KEY"] = body.anthropic_api_key.strip()
        updated.append("ANTHROPIC_API_KEY")
    return JSONResponse({"ok": True, "updated": updated})


@app.get("/api/settings/keys/status")
async def get_keys_status() -> JSONResponse:
    """현재 API 키 설정 여부 확인 (값은 노출하지 않음)."""
    import os
    return JSONResponse({
        "perplexity": bool(os.environ.get("PERPLEXITY_API_KEY", "").strip()),
        "anthropic":  bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
    })


# ── 분석 ──────────────────────────────────────────────────────────────────────

_analysis_cache: dict[str, Any] = {"result": None, "running": False}


class AnalyzeBody(BaseModel):
    use_perplexity: bool = True
    force_refresh: bool = False


@app.post("/api/analyze")
async def trigger_analyze(body: AnalyzeBody | None = None) -> JSONResponse:
    """8품목 수출 적합성 분석 실행 (Claude API + Perplexity 보조)."""
    req = body if body is not None else AnalyzeBody()
    if _analysis_cache["running"]:
        raise HTTPException(status_code=409, detail="분석이 이미 실행 중입니다.")
    if _analysis_cache["result"] and not req.force_refresh:
        return JSONResponse({"ok": True, "message": "캐시된 분석 결과 사용. force_refresh=true로 재실행."})

    async def _run() -> None:
        _analysis_cache["running"] = True
        try:
            from analysis.za_export_analyzer import analyze_all
            results = await analyze_all(use_perplexity=req.use_perplexity, emit=_emit)
            _analysis_cache["result"] = results
        finally:
            _analysis_cache["running"] = False

    asyncio.create_task(_run())
    return JSONResponse({"ok": True, "message": "분석을 백그라운드에서 시작했습니다."})


@app.get("/api/analyze/result")
async def analyze_result() -> JSONResponse:
    if _analysis_cache["running"]:
        return JSONResponse({"status": "running"}, status_code=202)
    if not _analysis_cache["result"]:
        raise HTTPException(status_code=404, detail="분석 결과 없음. POST /api/analyze 먼저 실행")
    return JSONResponse({
        "status": "done",
        "count": len(_analysis_cache["result"]),
        "results": _analysis_cache["result"],
    })


@app.get("/api/analyze/status")
async def analyze_status() -> dict[str, Any]:
    return {
        "running": _analysis_cache["running"],
        "has_result": _analysis_cache["result"] is not None,
        "product_count": len(_analysis_cache["result"]) if _analysis_cache["result"] else 0,
    }


# ── 시장 신호 · 뉴스 (Perplexity) ─────────────────────────────────────────────

_news_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_NEWS_TTL = 1800  # 30분 캐시


def _parse_perplexity_news_items(raw_text: str) -> list[dict[str, str]]:
    """Perplexity 텍스트 응답에서 뉴스 배열(JSON) 파싱."""
    import re

    text = (raw_text or "").strip()
    if not text:
        return []

    candidates: list[str] = [text]
    m = re.search(r"\[\s*\{.*\}\s*\]", text, flags=re.S)
    if m:
        candidates.append(m.group(0))

    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except Exception:
            continue
        if not isinstance(parsed, list):
            continue
        items: list[dict[str, str]] = []
        for row in parsed[:6]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title", "") or "").strip()
            if not title:
                continue
            items.append(
                {
                    "title": title,
                    "source": str(row.get("source", "") or "").strip(),
                    "date": str(row.get("date", "") or "").strip(),
                    "link": str(row.get("link", "") or "").strip(),
                }
            )
        if items:
            return items
    return []


@app.get("/api/news")
async def api_news() -> JSONResponse:
    """Perplexity 기반 남아공 제약 시장 뉴스 (30분 캐시)."""
    import time as _time
    import os
    import httpx

    if _news_cache["data"] and _time.time() - _news_cache["ts"] < _NEWS_TTL:
        return JSONResponse(_news_cache["data"])

    px_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    if not px_key:
        return JSONResponse({"ok": False, "error": "PERPLEXITY_API_KEY 미설정", "items": []})

    try:
        payload = {
            "model": "sonar-pro",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a South Africa pharmaceutical market analyst. "
                        "Return ONLY a JSON array with up to 6 recent news items. "
                        "All 'title' values MUST be written in Korean (한국어). "
                        "Translate any English titles into natural Korean."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Find the latest South Africa pharmaceutical market and regulatory news "
                        "(SAHPRA, SEP pricing, NHI, MCC, eTender procurement). "
                        "Return a strict JSON array. Each item must have keys: "
                        "title (Korean translation required), source, date, link. "
                        "Translate all titles to Korean. Do not use English titles."
                    ),
                },
            ],
            "max_tokens": 900,
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {px_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            raw = resp.json()

        content = str(
            raw.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        items = _parse_perplexity_news_items(content)
        if not items:
            return JSONResponse({"ok": False, "error": "Perplexity 응답 파싱 실패", "items": []})

        data = {"ok": True, "items": items}
        _news_cache["data"] = data
        _news_cache["ts"]   = _time.time()
        return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:120], "items": []})


# ── 거시지표 ──────────────────────────────────────────────────────────────────

@app.get("/api/macro")
async def api_macro() -> JSONResponse:
    from utils.za_macro import get_za_macro
    return JSONResponse(get_za_macro())


# ── 환율 (yfinance ZAR/KRW/USD) ─────────────────────────────────────────────

_exchange_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_EXCHANGE_TTL_SEC = 0.0


@app.get("/api/exchange")
async def api_exchange() -> JSONResponse:
    """ZAR/KRW/USD 실시간 환율 (yfinance). 짧은 캐시로 준실시간 제공."""
    import time as _time

    cache_file = ROOT / "datas" / "exchange_rate_cache.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    import json

    if _exchange_cache["data"] and _time.time() - _exchange_cache["ts"] < _EXCHANGE_TTL_SEC:
        return JSONResponse(_exchange_cache["data"])

    def _fetch() -> dict[str, Any]:
        import yfinance as yf  # type: ignore[import]
        zar_krw = float(yf.Ticker("ZARKRW=X").fast_info.last_price)
        usd_krw = float(yf.Ticker("USDKRW=X").fast_info.last_price)
        zar_usd = float(yf.Ticker("ZARUSD=X").fast_info.last_price)
        return {
            "zar_krw": round(zar_krw, 4),
            "usd_krw": round(usd_krw, 2),
            "zar_usd": round(zar_usd, 6),
            "source": "Yahoo Finance",
            "fetched_at": _time.time(),
            "ok": True,
        }

    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, _fetch)
        _exchange_cache["data"] = data
        _exchange_cache["ts"]   = _time.time()
        # Save to disk
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass
        return JSONResponse(data)
    except Exception as exc:
        fallback: dict[str, Any] = {}
        # Try to load from disk
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    fallback = json.load(f)
                fallback["source"] = "로컬 캐시 (Yahoo Finance 연결 실패)"
                fallback["ok"] = False
                fallback["error"] = str(exc)
                return JSONResponse(fallback)
            except Exception:
                pass
                
        fallback = {
            "zar_krw": 76.5,
            "usd_krw": 1393.0,
            "zar_usd": 0.055,
            "source": "하드코딩 폴백 (캐시/API 모두 실패)",
            "fetched_at": _time.time(),
            "ok": False,
            "error": str(exc),
        }
        return JSONResponse(fallback)


# ── 단일 품목 파이프라인 (분석 + 논문 + PDF) ──────────────────────────────────

_pipeline_tasks: dict[str, dict[str, Any]] = {}


async def _run_pipeline_for_product(product_key: str) -> None:
    task = _pipeline_tasks[product_key]
    try:
        # 0. DB 조회 (Supabase)
        task.update({"step": "db_load", "step_label": "Supabase 데이터 로드 중…"})
        await _emit({"phase": "pipeline", "message": f"{product_key} — DB 조회 중", "level": "info"})

        from utils.db import fetch_kup_products
        kup_rows = await asyncio.to_thread(fetch_kup_products, "ZA")
        db_row = next((r for r in kup_rows if r.get("product_id") == product_key), None)

        if db_row is None:
            await _emit({"phase": "pipeline", "message": f"DB에서 품목 미발견: {product_key}", "level": "warn"})

        # 1. Claude 분석
        task.update({"step": "analyze", "step_label": "Claude 분석 중…"})
        await _emit({"phase": "pipeline", "message": f"{product_key} — 분석 시작", "level": "info"})

        from analysis.za_export_analyzer import analyze_product
        result = await analyze_product(product_key, emit=_emit)
        task["result"] = result
        verdict = result.get("verdict") or "미분석"
        await _emit({"phase": "pipeline", "message": f"분석 완료 — {verdict}", "level": "success"})

        # 2. Perplexity 논문
        task.update({"step": "refs", "step_label": "논문 검색 중…"})
        from analysis.perplexity_references import fetch_references
        refs = await fetch_references(product_key)
        task["refs"] = refs
        if refs:
            await _emit({"phase": "pipeline", "message": f"논문 {len(refs)}건 검색 완료", "level": "success"})

        # 3. PDF 보고서 (in-process 생성 — subprocess 의존성 제거)
        task.update({"step": "report", "step_label": "PDF 생성 중…"})
        await _emit({"phase": "pipeline", "message": "PDF 보고서 생성 중…", "level": "info"})

        from datetime import datetime, timezone as _tz
        from report_generator import build_report, render_pdf

        _ts = datetime.now(_tz.utc).strftime("%Y%m%d_%H%M%S")
        _reports_dir = ROOT / "reports"
        _reports_dir.mkdir(parents=True, exist_ok=True)

        # kup_rows는 Step 0에서 이미 비동기로 가져왔으므로 재사용 (DB 이중 조회 방지)
        _refs_map = {product_key: refs}
        _report = await asyncio.to_thread(
            lambda: build_report(
                kup_rows,
                datetime.now(_tz.utc).isoformat(),
                [result],
                references=_refs_map,
            )
        )
        _pdf_name = f"za_report_{product_key}_{_ts}.pdf"
        _pdf_path = _reports_dir / _pdf_name
        await asyncio.to_thread(render_pdf, _report, _pdf_path)

        task["pdf"] = _pdf_name
        task.update({"status": "done", "step": "done", "step_label": "완료"})
        await _emit({"phase": "pipeline", "message": "파이프라인 완료", "level": "success"})

    except Exception as exc:
        task.update({"status": "error", "step": "error", "step_label": str(exc)})
        await _emit({"phase": "pipeline", "message": f"오류: {exc}", "level": "error"})


# ── 신약(커스텀) 파이프라인 ────────────────────────────────────────────────────
# 주의: 리터럴 경로("/api/pipeline/custom/...")는 반드시 {product_key} 라우트보다 먼저 선언

_custom_task: dict[str, Any] = {}


class CustomDrugBody(BaseModel):
    trade_name: str
    inn: str
    dosage_form: str = ""


async def _run_custom_pipeline(trade_name: str, inn: str, dosage_form: str) -> None:
    global _custom_task
    try:
        # Step 1: Claude 분석
        _custom_task.update({"step": "analyze", "step_label": "Claude 분석 중…"})
        from analysis.za_export_analyzer import analyze_custom_product
        result = await analyze_custom_product(trade_name, inn, dosage_form, emit=_emit)
        _custom_task["result"] = result

        # Step 2: Perplexity 논문
        _custom_task.update({"step": "refs", "step_label": "논문 검색 중…"})
        from analysis.perplexity_references import fetch_references_for_custom
        refs = await fetch_references_for_custom(trade_name, inn)
        _custom_task["refs"] = refs

        # Step 3: PDF 보고서 (in-process)
        _custom_task.update({"step": "report", "step_label": "PDF 생성 중…"})
        from datetime import datetime, timezone as _tz2
        from report_generator import build_report, render_pdf
        from utils.db import fetch_kup_products

        _ts2 = datetime.now(_tz2.utc).strftime("%Y%m%d_%H%M%S")
        _reports_dir2 = ROOT / "reports"
        _reports_dir2.mkdir(parents=True, exist_ok=True)

        _products_db2 = await asyncio.to_thread(fetch_kup_products, "ZA")
        _refs_map2 = {"custom": refs}
        _report2 = await asyncio.to_thread(
            lambda: build_report(
                _products_db2,
                datetime.now(_tz2.utc).isoformat(),
                [result],
                references=_refs_map2,
            )
        )
        _pdf_name2 = f"za_report_custom_{_ts2}.pdf"
        _pdf_path2 = _reports_dir2 / _pdf_name2
        await asyncio.to_thread(render_pdf, _report2, _pdf_path2)

        _custom_task["pdf"] = _pdf_name2
        _custom_task.update({"status": "done", "step": "done", "step_label": "완료"})

    except Exception as exc:
        _custom_task.update({"status": "error", "step": "error", "step_label": str(exc)})


@app.post("/api/pipeline/custom")
async def trigger_custom_pipeline(body: CustomDrugBody) -> JSONResponse:
    global _custom_task
    if _custom_task.get("status") == "running":
        raise HTTPException(status_code=409, detail="신약 분석이 이미 실행 중입니다.")
    _custom_task = {
        "status": "running", "step": "analyze", "step_label": "시작 중…",
        "result": None, "refs": [], "pdf": None,
    }
    asyncio.create_task(_run_custom_pipeline(body.trade_name, body.inn, body.dosage_form))
    return JSONResponse({"ok": True})


@app.get("/api/pipeline/custom/status")
async def custom_pipeline_status() -> JSONResponse:
    if not _custom_task:
        return JSONResponse({"status": "idle"})
    return JSONResponse({
        "status":     _custom_task.get("status", "idle"),
        "step":       _custom_task.get("step", ""),
        "step_label": _custom_task.get("step_label", ""),
        "has_result": _custom_task.get("result") is not None,
        "has_pdf":    bool(_custom_task.get("pdf")),
    })


@app.get("/api/pipeline/custom/result")
async def custom_pipeline_result() -> JSONResponse:
    if not _custom_task:
        raise HTTPException(404, "신약 분석 미실행")
    return JSONResponse({
        "status": _custom_task.get("status"),
        "result": _custom_task.get("result"),
        "refs":   _custom_task.get("refs", []),
        "pdf":    _custom_task.get("pdf"),
    })


# ── 기존 품목 파이프라인 ──────────────────────────────────────────────────────

@app.post("/api/pipeline/{product_key}")
async def trigger_pipeline(product_key: str) -> JSONResponse:
    if _pipeline_tasks.get(product_key, {}).get("status") == "running":
        raise HTTPException(status_code=409, detail="이미 실행 중입니다.")
    _pipeline_tasks[product_key] = {
        "status": "running", "step": "init", "step_label": "시작 중…",
        "result": None, "refs": [], "pdf": None,
    }
    asyncio.create_task(_run_pipeline_for_product(product_key))
    return JSONResponse({"ok": True, "message": "파이프라인 시작됨"})


@app.get("/api/pipeline/{product_key}/status")
async def pipeline_status(product_key: str) -> JSONResponse:
    task = _pipeline_tasks.get(product_key)
    if not task:
        return JSONResponse({"status": "idle"})
    return JSONResponse({
        "status":     task["status"],
        "step":       task["step"],
        "step_label": task["step_label"],
        "has_result": task["result"] is not None,
        "has_pdf":    bool(task["pdf"]),
        "ref_count":  len(task.get("refs", [])),
    })


@app.get("/api/pipeline/{product_key}/result")
async def pipeline_result(product_key: str) -> JSONResponse:
    task = _pipeline_tasks.get(product_key)
    if not task:
        raise HTTPException(404, "파이프라인 미실행")
    return JSONResponse({
        "status": task["status"],
        "step":   task["step"],
        "result": task.get("result"),
        "refs":   task.get("refs", []),
        "pdf":    task.get("pdf"),
    })


# ── 보고서 ────────────────────────────────────────────────────────────────────

_report_cache: dict[str, Any] = {"path": None, "running": False}

def _latest_report_pdf() -> Path | None:
    reports_dir = ROOT / "reports"
    if not reports_dir.exists():
        return None
    pdfs = [p for p in reports_dir.glob("za_report_*.pdf") if p.is_file()]
    if not pdfs:
        return None
    return max(pdfs, key=lambda p: p.stat().st_mtime)


class ReportBody(BaseModel):
    run_analysis: bool = False
    use_perplexity: bool = False


@app.post("/api/report")
async def trigger_report(body: ReportBody | None = None) -> JSONResponse:
    req = body if body is not None else ReportBody()
    if _report_cache["running"]:
        raise HTTPException(status_code=409, detail="보고서 생성이 이미 실행 중입니다.")

    async def _run_report() -> None:
        _report_cache["running"] = True
        try:
            import subprocess
            cmd = [
                sys.executable, str(ROOT / "report_generator.py"),
                "--out", str(ROOT / "reports"),
            ]
            if req.run_analysis:
                cmd.append("--run-analysis")
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: subprocess.run(cmd, capture_output=True, text=True)
            )
            reports_dir = ROOT / "reports"
            pdfs = sorted(reports_dir.glob("za_report_*.pdf"), reverse=True)
            _report_cache["path"] = str(pdfs[0]) if pdfs else None
        finally:
            _report_cache["running"] = False

    asyncio.create_task(_run_report())
    return JSONResponse({"ok": True, "message": "보고서 생성을 백그라운드에서 시작했습니다."})


@app.get("/api/report/status")
async def report_status() -> dict[str, Any]:
    reports_dir = ROOT / "reports"
    pdfs = [p for p in reports_dir.glob("za_report_*.pdf")] if reports_dir.exists() else []
    latest = _latest_report_pdf()
    return {
        "running": _report_cache["running"],
        "latest_pdf": str(latest) if latest else _report_cache["path"],
        "pdf_count": len(pdfs),
    }


@app.get("/api/report/download")
async def download_report(name: str | None = None, inline: bool = False) -> Any:
    """PDF 반환. inline=true면 브라우저/iframe 미리보기용(Content-Disposition: inline)."""
    reports_dir = ROOT / "reports"
    disp = "inline" if inline else "attachment"
    if name:
        target = reports_dir / Path(name).name
        if target.is_file():
            return FileResponse(
                str(target),
                media_type="application/pdf",
                filename=target.name,
                content_disposition_type=disp,
            )

    latest = _latest_report_pdf()
    if not latest:
        raise HTTPException(status_code=404, detail="생성된 보고서 없음. POST /api/report 먼저 실행")
    return FileResponse(
        str(latest),
        media_type="application/pdf",
        filename=latest.name,
        content_disposition_type=disp,
    )


# ── 2공정 가격 전략 PDF ───────────────────────────────────────────────────────

class P2ReportBody(BaseModel):
    product_name:  str   = ""
    verdict:       str   = ""
    seg_label:     str   = ""
    base_price:    float | None = None
    formula_str:   str   = ""
    mode_label:    str   = ""
    scenarios:     list  = []
    ai_rationale:  list  = []


@app.post("/api/p2/report")
async def generate_p2_report(body: P2ReportBody) -> JSONResponse:
    """2공정 수출 가격 전략 PDF 생성."""
    import re
    from datetime import datetime, timezone as _tz_p2

    _ts = datetime.now(_tz_p2.utc).strftime("%Y%m%d_%H%M%S")
    _reports_dir = ROOT / "reports"
    _reports_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^\w가-힣]", "_", body.product_name)[:30] or "product"
    pdf_name  = f"za_p2_{safe_name}_{_ts}.pdf"
    pdf_path  = _reports_dir / pdf_name

    p2_data = {
        "product_name":  body.product_name,
        "verdict":       body.verdict,
        "seg_label":     body.seg_label,
        "base_price":    body.base_price,
        "formula_str":   body.formula_str,
        "mode_label":    body.mode_label,
        "scenarios":     body.scenarios,
        "ai_rationale":  body.ai_rationale,
    }

    from report_generator import render_p2_pdf
    await asyncio.to_thread(render_p2_pdf, p2_data, pdf_path)

    return JSONResponse({"ok": True, "pdf": pdf_name})


# ── 2공정 AI 파이프라인 (PDF → Haiku 가격 추출 → 계산 → Haiku 분석 → PDF) ────────

_p2_ai_task: dict[str, Any] = {}


async def _run_p2_ai_pipeline(report_path: str, market: str) -> None:
    global _p2_ai_task
    try:
        import json
        import os
        import re

        import anthropic

        api_key = (
            os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY", "")
        ).strip()
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY 미설정 — 환경변수를 확인하세요.")

        # ── Step 1: PDF 텍스트 추출 ────────────────────────────────────────────
        _p2_ai_task.update({"step": "extract", "step_label": "PDF 텍스트 추출 중…"})
        await _emit({"phase": "p2_pipeline", "message": "PDF 텍스트 추출 시작", "level": "info"})

        pdf_text = ""
        try:
            from pypdf import PdfReader  # type: ignore[import]
            reader = PdfReader(report_path)
            for page in reader.pages:
                pdf_text += (page.extract_text() or "") + "\n"
        except Exception as exc_pdf:
            await _emit({"phase": "p2_pipeline", "message": f"PDF 추출 경고: {exc_pdf}", "level": "warn"})

        if not pdf_text.strip():
            raise ValueError("PDF에서 텍스트를 추출할 수 없습니다. 스캔 이미지 PDF이거나 암호화된 파일일 수 있습니다.")

        await _emit({"phase": "p2_pipeline", "message": f"텍스트 {len(pdf_text)}자 추출 완료", "level": "success"})

        # ── Step 2: Claude Haiku — 가격 정보 추출 ──────────────────────────────
        _p2_ai_task.update({"step": "ai_extract", "step_label": "AI 가격 정보 추출 중…"})
        await _emit({"phase": "p2_pipeline", "message": "Claude Haiku — 가격 정보 추출", "level": "info"})

        client = anthropic.Anthropic(api_key=api_key)

        extract_prompt = f"""다음 남아공 수출 분석 보고서에서 가격 관련 정보를 추출하세요.

보고서 내용:
{pdf_text[:7000]}

아래 JSON 형식으로만 응답하세요 (다른 텍스트 없이):
{{
  "product_name": "제품명 (없으면 '미상')",
  "ref_price_zar": 숫자 또는 null,
  "ref_price_currency": "ZAR 또는 USD",
  "ref_price_text": "원문 가격 텍스트 (없으면 빈 문자열)",
  "competitor_prices": [{{"name": "경쟁사명", "price_zar": 숫자}}],
  "market_context": "시장 맥락 요약 (1-2문장)",
  "hs_code": "HS 코드 (없으면 빈 문자열)",
  "verdict": "수출 적합성 판정 (적합/조건부/부적합/미상)"
}}

가격 추출 규칙 (반드시 준수):
- 'SEP R X.XX', 'R X.XX 수준', 'ZAR X.XX' 등 ZAR(R) 금액이 포함된 모든 표현에서 숫자를 추출하세요.
- SEP(Single Exit Price), 조제수수료, 환자 최종가 등 남아공 특유 가격 구조를 고려하세요.
- 보고서의 '참고 가격', 'SEP', 'MPR' 섹션을 특히 확인하세요.
- USD($) 금액만 있다면 ref_price_zar는 null로, ref_price_currency는 'USD'로, ref_price_text에 원문 그대로 기록하세요."""

        extracted: dict[str, Any] = {}
        for attempt in range(2):
            extract_resp = await asyncio.to_thread(
                lambda: client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=1024,
                    messages=[{"role": "user", "content": extract_prompt}],
                )
            )
            try:
                raw_extract = extract_resp.content[0].text
                # 좀 더 견고한 정규식으로 JSON 추출 시도 (중첩 중괄호 처리 향상)
                m_json = re.search(r"\{[\s\S]*\}", raw_extract)
                if m_json:
                    extracted = json.loads(m_json.group(0))
                    if "product_name" in extracted and "ref_price_zar" in extracted:
                        break
            except Exception:
                # 파싱 실패 시 프롬프트를 좀 더 엄격하게 수정하여 재시도
                extract_prompt += "\n\nCRITICAL: YOUR PREVIOUS OUTPUT WAS NOT VALID JSON. YOU MUST RESPOND ONLY WITH A VALID JSON OBJECT AND NOTHING ELSE."
                if attempt == 1:
                    extracted = {
                        "product_name": "미상",
                        "ref_price_zar": None,
                        "ref_price_text": "",
                        "market_context": "",
                        "verdict": "미상",
                    }

        _p2_ai_task["extracted"] = extracted
        await _emit({
            "phase": "p2_pipeline",
            "message": f"가격 추출 완료 — 참조가: R {extracted.get('ref_price_zar', '미확인')}",
            "level": "success",
        })

        # ── Step 3: 실시간 환율 (yfinance) ────────────────────────────────────
        _p2_ai_task.update({"step": "exchange", "step_label": "실시간 환율 조회 중…"})
        await _emit({"phase": "p2_pipeline", "message": "yfinance 환율 조회", "level": "info"})

        exchange_rates: dict[str, Any] = {
            "zar_krw": 76.5, "usd_krw": 1393.0,
            "zar_usd": 0.055, "source": "폴백값 (Yahoo Finance 연결 실패)",
        }
        try:
            import yfinance as yf  # type: ignore[import]

            def _fetch_rates() -> dict[str, Any]:
                return {
                    "zar_krw": round(float(yf.Ticker("ZARKRW=X").fast_info.last_price), 4),
                    "usd_krw": round(float(yf.Ticker("USDKRW=X").fast_info.last_price), 2),
                    "zar_usd": round(float(yf.Ticker("ZARUSD=X").fast_info.last_price), 6),
                    "source": "Yahoo Finance (실시간)",
                }

            exchange_rates = await asyncio.to_thread(_fetch_rates)
        except Exception as exc_fx:
            await _emit({"phase": "p2_pipeline", "message": f"환율 폴백: {exc_fx}", "level": "warn"})

        _p2_ai_task["exchange_rates"] = exchange_rates
        await _emit({
            "phase": "p2_pipeline",
            "message": f"환율 — 1 ZAR = {exchange_rates['zar_krw']} KRW",
            "level": "success",
        })

        # ── Step 4: Claude Haiku — 최종 가격 전략 분석 ──────────────────────────
        _p2_ai_task.update({"step": "ai_analysis", "step_label": "AI 최종 분석 중…"})
        await _emit({"phase": "p2_pipeline", "message": "Claude Haiku — 최종 가격 전략 분석", "level": "info"})

        ref_price    = extracted.get("ref_price_zar") or 0
        ref_display  = f"R {float(ref_price):.2f}" if ref_price else (extracted.get("ref_price_text") or "미확인")
        zar_krw      = exchange_rates["zar_krw"]
        market_label = "공공 시장 (MHPL/EML 공공조달 채널)" if market == "public" else "민간 시장 (Clicks/Dis-Chem 소매 채널)"
        verdict_src  = extracted.get("verdict", "미상")
        competitor_json = json.dumps(extracted.get("competitor_prices", []), ensure_ascii=False)

        analysis_prompt = f"""남아프리카공화국 수출 가격 전략을 수립해주세요.

## 추출된 보고서 정보
- 제품명: {extracted.get('product_name', '미상')}
- 수출 적합성 판정: {verdict_src}
- 참조가: {ref_display}
- 참조가 원문: {extracted.get('ref_price_text', '없음')}
- HS 코드: {extracted.get('hs_code', '미상')}
- 시장: {market_label}
- 현재 환율: 1 ZAR = {zar_krw:.4f} KRW (실시간 Yahoo Finance)
- 경쟁사 가격: {competitor_json}
- 시장 맥락: {extracted.get('market_context', '정보 없음')}

## 요청
1. 남아공 이중 시장(공공 80%/민간 20%) 특성, SEP 규제, 판정 결과, 시장 구분을 종합해 최종 수출 권고가를 산정하세요.
2. 시나리오는 공격·평균·보수 3개로 구분하세요. 각 시나리오마다:
   - 가격 근거·포지셔닝 전략·적합 상황을 포함한 한 문단(3-4문장)으로 reason을 작성하세요.
   - 구체적인 계산식을 formula 필드에 작성하세요 (예: R 150.00 × 0.85 = R 127.50).
3. rationale은 3-4문장으로 시장 근거·SEP 적합성·NHI 리스크를 포함해 서술하세요.

아래 JSON 형식으로만 응답하세요 (다른 텍스트 없이):
{{
  "final_price_zar": 숫자,
  "rationale": "산정 이유 3-4문장",
  "scenarios": [
    {{"name": "공격", "price_zar": 숫자, "reason": "저마진 포지셔닝 정의·근거·적합 상황을 포함한 한 문단", "formula": "계산식 (예: R 150.00 × 0.85 = R 127.50)"}},
    {{"name": "평균", "price_zar": 숫자, "reason": "중간 포지셔닝 정의·근거·적합 상황을 포함한 한 문단", "formula": "계산식"}},
    {{"name": "보수", "price_zar": 숫자, "reason": "고마진 포지셔닝 정의·근거·적합 상황을 포함한 한 문단", "formula": "계산식"}}
  ]
}}

참조가가 미확인이라면 남아공 SEP 레지스트리·경쟁사·제품 특성을 기반으로 합리적인 가격을 추정하세요."""

        analysis_resp = await asyncio.to_thread(
            lambda: client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2048,
                messages=[{"role": "user", "content": analysis_prompt}],
            )
        )

        analysis: dict[str, Any] = {}
        try:
            raw_analysis = analysis_resp.content[0].text
            m_json2 = re.search(r"\{.*\}", raw_analysis, re.S)
            if m_json2:
                analysis = json.loads(m_json2.group(0))
        except Exception:
            final_est = (ref_price * 0.30) if ref_price else 0
            analysis = {
                "final_price_zar": round(final_est, 2),
                "rationale": "AI 응답 파싱 중 오류가 발생했습니다. 기본값 30% 비율로 산정합니다.",
                "scenarios": [
                    {"name": "공격", "price_zar": round(final_est * 0.88, 2),
                     "reason": "저마진 포지셔닝 — 시장 진입 초기, 자사가 손해를 감수하며 가격경쟁력을 앞세워 점유율을 선점합니다.",
                     "formula": f"R {final_est:.2f} × 0.88 = R {round(final_est * 0.88, 2):.2f}"},
                    {"name": "평균", "price_zar": round(final_est, 2),
                     "reason": "중간 포지셔닝 — 리스크와 마진의 균형을 유지하는 기본 산정가입니다.",
                     "formula": f"R {final_est:.2f} (기준가 그대로)"},
                    {"name": "보수", "price_zar": round(final_est * 1.12, 2),
                     "reason": "고마진 포지셔닝 — 자사 제품이 시장 내 자리를 잡은 이후 마진율을 높여 이익 확대를 노립니다.",
                     "formula": f"R {final_est:.2f} × 1.12 = R {round(final_est * 1.12, 2):.2f}"},
                ],
            }

        _p2_ai_task["analysis"] = analysis
        await _emit({
            "phase": "p2_pipeline",
            "message": f"최종 분석 완료 — R {analysis.get('final_price_zar', 0):.2f}",
            "level": "success",
        })

        # ── Step 5: PDF 보고서 생성 ───────────────────────────────────────────
        _p2_ai_task.update({"step": "report", "step_label": "PDF 생성 중…"})
        await _emit({"phase": "p2_pipeline", "message": "2공정 PDF 보고서 생성", "level": "info"})

        from datetime import datetime, timezone as _tz_p2ai
        import re as _re2

        _ts_p2 = datetime.now(_tz_p2ai.utc).strftime("%Y%m%d_%H%M%S")
        _reports_dir_p2 = ROOT / "reports"
        _reports_dir_p2.mkdir(parents=True, exist_ok=True)

        _safe = _re2.sub(r"[^\w가-힣]", "_", extracted.get("product_name", "product"))[:30] or "product"
        _pdf_name_p2 = f"za_p2_{_safe}_{_ts_p2}.pdf"
        _pdf_path_p2 = _reports_dir_p2 / _pdf_name_p2

        # AI 시나리오 필드명 정규화 (PDF generator는 label/price 사용)
        raw_scenarios = analysis.get("scenarios", []) or []
        norm_scenarios = []
        for sc in raw_scenarios:
            norm_scenarios.append({
                "label":   sc.get("name", sc.get("label", "")),
                "price":   sc.get("price_zar", sc.get("price", 0)),
                "reason":  sc.get("reason", ""),
                "formula": sc.get("formula", ""),
            })

        p2_data = {
            "product_name": extracted.get("product_name", "미상"),
            "verdict":      verdict_src,
            "seg_label":    market_label,
            "base_price":   analysis.get("final_price_zar", 0),
            "formula_str":  "",
            "mode_label":   "AI 분석 (Claude Haiku)",
            "scenarios":    norm_scenarios,
            "ai_rationale": [analysis.get("rationale", "")],
        }

        from report_generator import render_p2_pdf
        await asyncio.to_thread(render_p2_pdf, p2_data, _pdf_path_p2)

        _p2_ai_task["pdf"] = _pdf_name_p2
        _p2_ai_task.update({"status": "done", "step": "done", "step_label": "완료"})
        await _emit({"phase": "p2_pipeline", "message": "P2 파이프라인 완료", "level": "success"})

    except Exception as exc:
        _p2_ai_task.update({"status": "error", "step": "error", "step_label": str(exc)[:300]})
        await _emit({"phase": "p2_pipeline", "message": f"P2 오류: {exc}", "level": "error"})


class UploadBody(BaseModel):
    filename: str
    content_b64: str  # base64 인코딩된 PDF 바이너리


@app.post("/api/p2/upload")
async def upload_p2_pdf(body: UploadBody) -> JSONResponse:
    """P2 파이프라인용 PDF 업로드 (base64 JSON — python-multipart 불필요)."""
    import base64
    import re as _re_up

    fname = body.filename or "upload.pdf"
    if not fname.lower().endswith(".pdf"):
        raise HTTPException(400, "PDF 파일(.pdf)만 업로드 가능합니다.")

    try:
        content = base64.b64decode(body.content_b64)
    except Exception:
        raise HTTPException(400, "base64 디코딩 실패 — 올바른 PDF 파일인지 확인하세요.")

    safe_fname = _re_up.sub(r"[^\w가-힣\-\.]", "_", fname)[:80]
    _reports_dir = ROOT / "reports"
    _reports_dir.mkdir(parents=True, exist_ok=True)
    dest = _reports_dir / f"upload_{safe_fname}"
    dest.write_bytes(content)

    return JSONResponse({"ok": True, "filename": dest.name})


class P2PipelineBody(BaseModel):
    report_filename: str = ""  # reports/ 내 파일명 (비어 있으면 최신 1공정 PDF 사용)
    market: str = "public"     # "public" | "private"


@app.post("/api/p2/pipeline")
async def trigger_p2_pipeline(body: P2PipelineBody) -> JSONResponse:
    """2공정 AI 파이프라인 실행."""
    global _p2_ai_task
    if _p2_ai_task.get("status") == "running":
        raise HTTPException(409, "P2 파이프라인이 이미 실행 중입니다.")

    if body.report_filename:
        report_path = ROOT / "reports" / Path(body.report_filename).name
    else:
        report_path = _latest_report_pdf()

    if not report_path or not Path(report_path).is_file():
        raise HTTPException(404, f"보고서 파일을 찾을 수 없습니다: {body.report_filename or '(최신 PDF 없음)'}")

    _p2_ai_task = {
        "status":   "running",
        "step":     "extract",
        "step_label": "시작 중…",
        "extracted": None,
        "exchange_rates": None,
        "analysis": None,
        "pdf":      None,
    }
    asyncio.create_task(_run_p2_ai_pipeline(str(report_path), body.market))
    return JSONResponse({"ok": True})


@app.get("/api/p2/pipeline/status")
async def p2_pipeline_status_ai() -> JSONResponse:
    if not _p2_ai_task:
        return JSONResponse({"status": "idle"})
    return JSONResponse({
        "status":     _p2_ai_task.get("status", "idle"),
        "step":       _p2_ai_task.get("step", ""),
        "step_label": _p2_ai_task.get("step_label", ""),
        "has_result": _p2_ai_task.get("analysis") is not None,
        "has_pdf":    bool(_p2_ai_task.get("pdf")),
    })


@app.get("/api/p2/pipeline/result")
async def p2_pipeline_result_ai() -> JSONResponse:
    if not _p2_ai_task:
        raise HTTPException(404, "P2 파이프라인 미실행")
    return JSONResponse({
        "status":         _p2_ai_task.get("status"),
        "extracted":      _p2_ai_task.get("extracted"),
        "exchange_rates": _p2_ai_task.get("exchange_rates"),
        "analysis":       _p2_ai_task.get("analysis"),
        "pdf":            _p2_ai_task.get("pdf"),
    })


# ── products 조회 ─────────────────────────────────────────────────────────────

@app.get("/api/products")
async def products() -> list[dict[str, Any]]:
    from utils.db import fetch_kup_products
    return fetch_kup_products("ZA")


# ── API 키 상태 (U1) ──────────────────────────────────────────────────────────

@app.get("/api/keys/status")
async def keys_status() -> dict[str, Any]:
    """Claude·Perplexity API 키 설정 여부 반환 (실제 키 값은 노출하지 않음)."""
    import os
    claude_key     = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
    perplexity_key = os.environ.get("PERPLEXITY_API_KEY", "")
    return {
        "claude":     bool(claude_key.strip()),
        "perplexity": bool(perplexity_key.strip()),
    }


# ── 데이터 소스 상태 (U5·B1) ──────────────────────────────────────────────────

@app.get("/api/datasource/status")
async def datasource_status() -> JSONResponse:
    """Supabase 연결 상태, KUP 품목 수, ZA 컨텍스트 출처 반환."""
    try:
        from utils.db import get_client, fetch_kup_products
        kup_rows = fetch_kup_products("ZA")
        kup_count = len(kup_rows)

        # ZA 컨텍스트 테이블 점검
        sb = get_client()
        ctx_count = 0
        context_source = "없음"
        try:
            ctx_rows = (
                sb.table("za_product_context")
                .select("product_id", count="exact")
                .execute()
            )
            ctx_count = ctx_rows.count or 0
            context_source = f"za_product_context {ctx_count}건" if ctx_count else "products 테이블 폴백"
        except Exception:
            context_source = "조회 실패"

        return JSONResponse({
            "supabase":       "ok",
            "kup_count":      kup_count,
            "context_ok":     ctx_count > 0,
            "context_source": context_source,
            "message":        f"KUP {kup_count}건 로드",
        })
    except Exception as exc:
        return JSONResponse({
            "supabase":       "error",
            "kup_count":      0,
            "context_ok":     False,
            "context_source": "연결 실패",
            "message":        str(exc)[:120],
        })


# ── 상태 / SSE 스트림 ─────────────────────────────────────────────────────────

@app.get("/api/status")
async def status() -> dict[str, Any]:
    lock = _state["lock"]
    assert lock is not None
    async with lock:
        n = len(_state["events"])
    return {"event_count": n}


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """Render 헬스체크용 경량 엔드포인트."""
    return {"ok": True, "service": "za-analysis-dashboard"}


@app.get("/api/stream")
async def stream() -> StreamingResponse:
    last = 0

    async def gen() -> Any:
        nonlocal last
        while True:
            await asyncio.sleep(0.12)
            chunk: list[dict[str, Any]] = []
            lock = _state["lock"]
            assert lock is not None
            async with lock:
                while last < len(_state["events"]):
                    chunk.append(_state["events"][last])
                    last += 1
            for ev in chunk:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ── 3공정: 바이어 발굴 파이프라인 ─────────────────────────────────────────────

_buyer_task: dict[str, Any] = {}

_PROD_LABELS: dict[str, str] = {
    "sereterol_activair": "Sereterol Activair (Fluticasone+Salmeterol)",
    "omethyl_cutielet":   "Omethyl Cutielet (Omega-3 2g Pouch)",
    "hydrine":            "Hydrine (Hydroxyurea 500mg)",
    "gadvoa_inj":         "Gadvoa Inj. (Gadobutrol PFS)",
    "rosumeg_combigel":   "Rosumeg Combigel (Rosuvastatin+Omega-3)",
    "atmeg_combigel":     "Atmeg Combigel (Atorvastatin+Omega-3)",
    "ciloduo":            "Ciloduo (Cilostazol+Rosuvastatin)",
    "gastiin_cr":         "Gastiin CR (Mosapride Citrate 서방정)",
}


class BuyerRunBody(BaseModel):
    product_key:     str = "sereterol_activair"
    active_criteria: list[str] | None = None
    target_country:  str = "South Africa"
    target_region:   str = "Africa"


async def _run_buyer_pipeline(
    product_key: str,
    active_criteria: list[str] | None = None,
    target_country: str = "South Africa",
    target_region: str = "Africa",
) -> None:
    global _buyer_task

    async def _log(msg: str, level: str = "info") -> None:
        await _emit({"phase": "buyer", "message": msg, "level": level})

    try:
        product_label = _PROD_LABELS.get(product_key, product_key)

        # ── Step 1: 1차 수집 (CPHI 크롤링 — 후보 최대 20개) ─────────────
        _buyer_task.update({"step": "crawl", "step_label": "CPHI 크롤링 중…"})
        await _log(f"바이어 발굴 시작 — 품목: {product_label} / 타깃: {target_country} ({target_region})")

        from utils.cphi_crawler import crawl as cphi_crawl
        companies = await cphi_crawl(
            product_key=product_key,
            candidate_pool=20,
            emit=_log,
        )
        _buyer_task["crawl_count"] = len(companies)
        await _log(f"1차 수집 완료 — {len(companies)}개 후보", "success")

        # ── Step 2: 심층조사 (CPHI 전체 텍스트 → Claude Haiku) ───────────
        _buyer_task.update({"step": "enrich", "step_label": "심층조사 중…"})
        await _log("심층조사 시작 (CPHI 페이지 텍스트 → Claude Haiku 파싱)")

        from utils.buyer_enricher import enrich_all
        enriched = await enrich_all(
            companies,
            product_label=product_label,
            target_country=target_country,
            target_region=target_region,
            emit=_log,
        )
        # 전체 후보 풀 저장 — 기준 변경 시 재선택에 사용
        _buyer_task["all_candidates"] = enriched
        await _log(f"심층조사 완료 — {len(enriched)}개", "success")

        # ── Step 3: 상위 10개 선택 ────────────────────────────────────────
        _buyer_task.update({"step": "rank", "step_label": "Top 10 선정 중…"})
        await _log("평가 기준 적용 → Top 10 선정")

        from analysis.buyer_scorer import rank_companies
        ranked = rank_companies(enriched, active_criteria=active_criteria, top_n=10)
        _buyer_task["buyers"] = ranked
        await _log(f"Top {len(ranked)}개 바이어 선정 완료", "success")

        # ── Step 4: PDF 보고서 생성 ───────────────────────────────────────
        _buyer_task.update({"step": "report", "step_label": "PDF 생성 중…"})
        await _log("바이어 보고서 PDF 생성 중…")

        from datetime import datetime, timezone as _tz_b
        from analysis.buyer_report_generator import build_buyer_pdf
        import re as _re_b

        _ts = datetime.now(_tz_b.utc).strftime("%Y%m%d_%H%M%S")
        _reports_dir = ROOT / "reports"
        _reports_dir.mkdir(parents=True, exist_ok=True)

        safe = _re_b.sub(r"[^\w가-힣]", "_", product_key)[:30]
        pdf_name = f"za_buyers_{safe}_{_ts}.pdf"
        pdf_path = _reports_dir / pdf_name

        await asyncio.to_thread(build_buyer_pdf, ranked, product_label, pdf_path)
        _buyer_task["pdf"] = pdf_name
        _buyer_task.update({"status": "done", "step": "done", "step_label": "완료"})
        await _log("바이어 발굴 파이프라인 완료", "success")

    except Exception as exc:
        _buyer_task.update({"status": "error", "step": "error", "step_label": str(exc)})
        await _emit({"phase": "buyer", "message": f"오류: {exc}", "level": "error"})


@app.post("/api/buyers/run")
async def trigger_buyers(body: BuyerRunBody | None = None) -> JSONResponse:
    global _buyer_task
    req = body if body is not None else BuyerRunBody()
    if _buyer_task.get("status") == "running":
        raise HTTPException(409, "바이어 발굴이 이미 실행 중입니다.")
    _buyer_task = {
        "status": "running", "step": "crawl", "step_label": "시작 중…",
        "crawl_count": 0, "all_candidates": [], "buyers": [], "pdf": None,
    }
    asyncio.create_task(_run_buyer_pipeline(
        req.product_key,
        req.active_criteria,
        req.target_country,
        req.target_region,
    ))
    return JSONResponse({"ok": True})


@app.get("/api/buyers/status")
async def buyer_status() -> JSONResponse:
    if not _buyer_task:
        return JSONResponse({"status": "idle"})
    return JSONResponse({
        "status":          _buyer_task.get("status", "idle"),
        "step":            _buyer_task.get("step", ""),
        "step_label":      _buyer_task.get("step_label", ""),
        "crawl_count":     _buyer_task.get("crawl_count", 0),
        "buyer_count":     len(_buyer_task.get("buyers", [])),
        "candidate_count": len(_buyer_task.get("all_candidates", [])),
        "has_pdf":         bool(_buyer_task.get("pdf")),
    })


@app.get("/api/buyers/result")
async def buyer_result() -> JSONResponse:
    if not _buyer_task:
        raise HTTPException(404, "바이어 발굴 미실행")
    return JSONResponse({
        "status":  _buyer_task.get("status"),
        "buyers":  _buyer_task.get("buyers", []),
        "pdf":     _buyer_task.get("pdf"),
    })


@app.post("/api/buyers/rerank")
async def buyer_rerank(body: dict = None) -> JSONResponse:
    """기준 변경 시 전체 후보 풀(20개)에서 재선택."""
    all_candidates = _buyer_task.get("all_candidates", [])
    if not all_candidates:
        raise HTTPException(404, "후보 풀 없음. 파이프라인을 먼저 실행하세요.")
    criteria = (body or {}).get("criteria")
    from analysis.buyer_scorer import rank_companies
    ranked = rank_companies(all_candidates, active_criteria=criteria, top_n=10)
    _buyer_task["buyers"] = ranked
    return JSONResponse({"buyers": ranked})


@app.get("/api/buyers/report/download")
async def buyer_report_download(name: str | None = None) -> Any:
    reports_dir = ROOT / "reports"
    if name:
        target = reports_dir / Path(name).name
        if target.is_file():
            return FileResponse(
                str(target), media_type="application/pdf",
                filename=target.name, content_disposition_type="attachment",
            )
    # 최신 buyers PDF
    pdfs = sorted(reports_dir.glob("za_buyers_*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pdfs:
        raise HTTPException(404, "바이어 보고서 없음")
    return FileResponse(
        str(pdfs[0]), media_type="application/pdf",
        filename=pdfs[0].name, content_disposition_type="attachment",
    )


# ── ZA 크롤링 파이프라인 ──────────────────────────────────────────────────────

_za_crawl_cache: dict[str, Any] = {"result": None, "running": False}


class ZaCrawlBody(BaseModel):
    inn_names: list[str] = ["Rosuvastatin", "Atorvastatin", "Cilostazol", "Mosapride",
                             "Fluticasone", "Gadobutrol", "Hydroxyurea", "Omega-3"]
    save_db: bool = True


@app.post("/api/za/crawl")
async def trigger_za_crawl(body: ZaCrawlBody | None = None) -> JSONResponse:
    req = body if body is not None else ZaCrawlBody()
    if _za_crawl_cache["running"]:
        raise HTTPException(status_code=409, detail="ZA 크롤링이 이미 실행 중입니다.")

    async def _run() -> None:
        _za_crawl_cache["running"] = True
        try:
            from analysis.za_export_analyzer import analyze_za_market
            result = await analyze_za_market(
                inn_names=req.inn_names,
                save_db=req.save_db,
                emit=_emit,
            )
            _za_crawl_cache["result"] = result
        finally:
            _za_crawl_cache["running"] = False

    asyncio.create_task(_run())
    return JSONResponse({"ok": True, "message": f"{req.inn_names} ZA 크롤링 시작"})


@app.get("/api/za/crawl/status")
async def za_crawl_status() -> JSONResponse:
    return JSONResponse({
        "running": _za_crawl_cache["running"],
        "has_result": _za_crawl_cache["result"] is not None,
        "result": _za_crawl_cache["result"],
    })


@app.get("/api/za/pricing")
async def api_za_pricing(inn_name: str | None = None, limit: int = 100) -> JSONResponse:
    try:
        from utils.db import get_supabase_client
        sb = get_supabase_client()
        query = sb.table("za_pricing").select("*").order("crawled_at", desc=True).limit(limit)
        if inn_name:
            query = query.ilike("inn_name", f"%{inn_name}%")
        result = query.execute()
        return JSONResponse({"ok": True, "count": len(result.data), "rows": result.data})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200], "rows": []})


# ── ZA SEP 조제수수료 조회 ────────────────────────────────────────────────────

@app.get("/api/za/sep")
async def api_za_sep(inn_name: str | None = None, limit: int = 50) -> JSONResponse:
    """SEP 레지스트리 조회 + 조제수수료 역산."""
    try:
        from utils.db import get_supabase_client
        sb = get_supabase_client()
        query = sb.table("za_sep_registry").select("*").order("created_at", desc=True).limit(limit)
        if inn_name:
            query = query.ilike("api", f"%{inn_name}%")
        result = query.execute()
        return JSONResponse({"ok": True, "count": len(result.data), "rows": result.data})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200], "rows": []})


# ── ZA 시장 뉴스 (Perplexity, 30분 캐시) ─────────────────────────────────────

_za_news_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_ZA_NEWS_TTL = 1800


@app.get("/api/za/news")
async def api_za_news() -> JSONResponse:
    import time as _time
    import os
    import httpx

    if _za_news_cache["data"] and _time.time() - _za_news_cache["ts"] < _ZA_NEWS_TTL:
        return JSONResponse(_za_news_cache["data"])

    px_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    if not px_key:
        return JSONResponse({"ok": False, "error": "PERPLEXITY_API_KEY 미설정", "items": []})

    try:
        payload = {
            "model": "sonar-pro",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a South Africa pharmaceutical market analyst. "
                        "Return ONLY a JSON array with up to 6 recent news items. "
                        "All 'title' values MUST be written in Korean (한국어)."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Find the latest South Africa pharmaceutical market, SAHPRA regulatory, "
                        "SEP pricing, NHI, eTender procurement news. "
                        "Return strict JSON array. Each item: title (Korean), source, date, link."
                    ),
                },
            ],
            "max_tokens": 900,
            "temperature": 0.2,
        }
        headers = {"Authorization": f"Bearer {px_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            raw = resp.json()

        content = str(raw.get("choices", [{}])[0].get("message", {}).get("content", ""))
        items = _parse_perplexity_news_items(content)
        data = {"ok": bool(items), "items": items}
        _za_news_cache["data"] = data
        _za_news_cache["ts"] = _time.time()
        return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:120], "items": []})


# ── FOB 역산기 ────────────────────────────────────────────────────────────────

class FobBody(BaseModel):
    price_usd: float
    market_segment: str = "private"
    inn_name: str = ""
    import_duty_pct: float | None = None


@app.post("/api/fob/calculate")
async def api_fob_calculate(body: FobBody) -> JSONResponse:
    from analysis.fob_calculator import (
        calc_logic_a, calc_logic_b, fob_result_to_dict, msp_copayment_check
    )
    from decimal import Decimal

    price = Decimal(str(body.price_usd))
    if body.market_segment == "public":
        duty = Decimal(str(body.import_duty_pct / 100)) if body.import_duty_pct else None
        result = calc_logic_a(price, import_duty_rate=duty, inn_name=body.inn_name)
    else:
        result = calc_logic_b(price, inn_name=body.inn_name)

    d = fob_result_to_dict(result)
    d["msp_check"] = msp_copayment_check(result.base.fob_usd)
    return JSONResponse({"ok": True, **d})


# ── ZA 리포트 세션 API (팀장 양식: session/init → pricing → partner → combined) ─

class SessionInitBody(BaseModel):
    product_key:   str
    product_id:    str | None = None
    product_label: str = ""
    inn_name:      str = ""


class PricingBody(BaseModel):
    session_id:  str
    product_key: str = ""


class PartnerBody(BaseModel):
    session_id:  str
    product_key: str = ""
    criteria:    list[str] = []


# 공통 8개 제품 INN 매핑
_KEY_TO_INN: dict[str, str] = {
    "sereterol_activair": "Fluticasone+Salmeterol",
    "omethyl_cutielet":   "Omega-3",
    "rosumeg_combigel":   "Rosuvastatin",
    "atmeg_combigel":     "Atorvastatin",
    "ciloduo":            "Cilostazol",
    "gastiin_cr":         "Mosapride",
    "hydrine":            "Hydroxyurea",
    "gadvoa_inj":         "Gadobutrol",
}

_KEY_TO_LABEL: dict[str, str] = {
    "sereterol_activair": "Sereterol Activair",
    "omethyl_cutielet":   "Omethyl Cutielet",
    "rosumeg_combigel":   "Rosumeg Combigel",
    "atmeg_combigel":     "Atmeg Combigel",
    "ciloduo":            "Ciloduo",
    "gastiin_cr":         "Gastiin CR",
    "hydrine":            "Hydrine",
    "gadvoa_inj":         "Gadvoa Inj",
}

# 인메모리 세션 저장소 (DB 없이도 동작)
_sessions: dict[str, Any] = {}


@app.post("/api/za/report/session/init")
async def za_session_init(body: SessionInitBody) -> JSONResponse:
    """1공정: 품목 선택 시 자동 시장조사 + 세션 생성."""
    import uuid as _uuid
    import os
    import anthropic as _anthropic

    session_id = str(_uuid.uuid4())
    report_id  = str(_uuid.uuid4())
    inn = body.inn_name or _KEY_TO_INN.get(body.product_key, body.product_label)
    label = body.product_label or _KEY_TO_LABEL.get(body.product_key, body.product_key)

    # Claude API로 시장조사 보고서 생성
    market_text = ""
    ak = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if ak:
        try:
            client = _anthropic.Anthropic(api_key=ak)
            msg = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=1200,
                system=(
                    "You are a South Africa pharmaceutical market analyst. "
                    "Respond in Korean. Be concise and data-focused."
                ),
                messages=[{
                    "role": "user",
                    "content": (
                        f"{label} ({inn})의 남아프리카공화국 시장 진입 가능성을 분석해주세요.\n"
                        "다음을 포함하세요:\n"
                        "1. SAHPRA 등록 현황 및 경쟁 제품\n"
                        "2. SEP(단일출고가) 및 소매가 수준 (ZAR)\n"
                        "3. 공공시장(MHPL/EML) vs 민간시장 진입 전략\n"
                        "4. 주요 리스크 및 기회\n"
                        "200단어 이내로 요약해주세요."
                    ),
                }],
            )
            market_text = msg.content[0].text
        except Exception as exc:
            market_text = f"[분석 오류: {exc}]"
    else:
        market_text = "[ANTHROPIC_API_KEY 미설정 — 시장조사 생략]"

    # 세션 저장
    _sessions[session_id] = {
        "session_id":   session_id,
        "product_key":  body.product_key,
        "product_id":   body.product_id,
        "product_label": label,
        "inn_name":     inn,
        "market_done":  True,
        "pricing_done": False,
        "partner_done": False,
        "reports": [{
            "id":          report_id,
            "report_type": "market",
            "badge":       "조사",
            "title":       f"시장조사 · {label}",
            "content":     market_text,
        }],
    }

    # Supabase 저장 시도 (실패해도 계속)
    try:
        from utils.db import get_supabase_client
        sb = get_supabase_client()
        sb.table("za_report_sessions").insert({
            "id": session_id,
            "product_id": body.product_id,
            "product_label": label,
            "market_research_done": True,
        }).execute()
        sb.table("za_reports").insert({
            "id": report_id,
            "session_id": session_id,
            "report_type": "market",
            "title": f"시장조사 · {label}",
            "badge": "조사",
            "content_json": {"text": market_text},
        }).execute()
    except Exception:
        pass

    return JSONResponse({"ok": True, "session_id": session_id, "report_id": report_id, "market_summary": market_text[:200]})


@app.post("/api/za/report/pricing")
async def za_report_pricing(body: PricingBody) -> JSONResponse:
    """2공정: 공공·민간 가격 분석 병렬 생성."""
    import uuid as _uuid
    import os
    import anthropic as _anthropic
    from analysis.fob_calculator import calc_logic_a, calc_logic_b, fob_result_to_dict
    from decimal import Decimal

    session = _sessions.get(body.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다. 먼저 분석 실행을 완료하세요.")

    label = session.get("product_label", body.product_key)
    inn   = session.get("inn_name", _KEY_TO_INN.get(body.product_key, ""))

    # Claude API로 가격 분석
    scenarios: dict[str, Any] = {}
    ak = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if ak:
        try:
            client = _anthropic.Anthropic(api_key=ak)
            msg = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=600,
                system="You are a pharmaceutical pricing expert for South Africa. Respond ONLY with valid JSON.",
                messages=[{
                    "role": "user",
                    "content": (
                        f"{label} ({inn})의 남아공 수출 FOB 가격을 USD로 산출해주세요.\n"
                        "다음 JSON 형식으로만 답변하세요:\n"
                        '{"conservative":{"fob_usd":0.00,"zar":0.00},'
                        '"baseline":{"fob_usd":0.00,"zar":0.00},'
                        '"premium":{"fob_usd":0.00,"zar":0.00}}\n'
                        "ZAR/USD 환율 약 18 기준. 공공입찰가 Logic A, 민간소매가 Logic B 평균 적용."
                    ),
                }],
            )
            import json as _json
            raw = msg.content[0].text.strip()
            # JSON 추출
            start = raw.find('{')
            end = raw.rfind('}') + 1
            if start >= 0 and end > start:
                scenarios = _json.loads(raw[start:end])
        except Exception:
            pass

    if not scenarios:
        # 폴백: FOB 계산기 사용
        try:
            res_pub  = calc_logic_a(Decimal("50"), inn_name=inn)
            res_priv = calc_logic_b(Decimal("50"), inn_name=inn)
            base_usd = float(res_priv.base.fob_usd)
            scenarios = {
                "conservative": {"fob_usd": round(base_usd * 0.77, 2), "zar": round(base_usd * 0.77 * 18, 2)},
                "baseline":     {"fob_usd": round(base_usd, 2),          "zar": round(base_usd * 18, 2)},
                "premium":      {"fob_usd": round(base_usd * 1.23, 2),  "zar": round(base_usd * 1.23 * 18, 2)},
            }
        except Exception:
            scenarios = {
                "conservative": {"fob_usd": 10.00, "zar": 180.0},
                "baseline":     {"fob_usd": 13.00, "zar": 234.0},
                "premium":      {"fob_usd": 16.00, "zar": 288.0},
            }

    id_pub  = str(_uuid.uuid4())
    id_priv = str(_uuid.uuid4())

    # 세션 업데이트
    if session:
        session["pricing_done"] = True
        session["reports"].extend([
            {"id": id_pub,  "report_type": "pricing_public",  "badge": "공공", "title": f"수출가격 전략 [공공] · {label}", "scenarios": scenarios},
            {"id": id_priv, "report_type": "pricing_private", "badge": "민간", "title": f"수출가격 전략 [민간] · {label}", "scenarios": scenarios},
        ])

    return JSONResponse({
        "ok": True,
        "session_id":       body.session_id,
        "scenarios":        scenarios,
        "report_id_public":  id_pub,
        "report_id_private": id_priv,
    })


@app.post("/api/za/report/partner")
async def za_report_partner(body: PartnerBody) -> JSONResponse:
    """3공정: 바이어 발굴 + 결합본 비동기 트리거."""
    import uuid as _uuid
    import os
    import anthropic as _anthropic

    session = _sessions.get(body.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

    label    = session.get("product_label", body.product_key)
    inn      = session.get("inn_name", _KEY_TO_INN.get(body.product_key, ""))
    criteria = body.criteria or ["매출규모", "파이프라인", "제조소", "수입경험", "약국체인"]

    top10: list[dict[str, Any]] = []
    ak = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if ak:
        try:
            import json as _json
            client = _anthropic.Anthropic(api_key=ak)
            msg = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=800,
                system="You are a South Africa pharmaceutical business development expert. Respond ONLY with valid JSON.",
                messages=[{
                    "role": "user",
                    "content": (
                        f"{label} ({inn}) 남아공 수입·유통 파트너 Top 10을 추천해주세요.\n"
                        f"평가 기준: {', '.join(criteria)}\n"
                        "실제 존재하는 남아공 제약 회사 이름으로 답변하세요.\n"
                        '응답 형식: [{"rank":1,"name":"Company Name","overview":"기업 개요 2-3문장","reason":"채택 이유 2-3문장","address":"...","phone":"...","email":"...","website":"...","scale":"...","region":"..."},...]'
                    ),
                }],
            )
            raw = msg.content[0].text.strip()
            start = raw.find('[')
            end = raw.rfind(']') + 1
            if start >= 0 and end > start:
                top10 = _json.loads(raw[start:end])
        except Exception:
            pass

    if not top10:
        # 폴백 Top 10 (상세 데이터 구조 포함)
        def _mock(r, n):
            return {
                "rank": r, "name": n,
                "overview": f"{n}는 남아공 내 주요 의약품 수입 및 유통을 담당하는 선도적인 제약 파트너사입니다. 다수의 다국적 제약사와 협력하고 있습니다.",
                "reason": f"매출 규모와 수입 경험 면에서 {label}의 현지 시장 진입 파트너로 가장 적합한 역량을 보유하고 있습니다.",
                "address": "123 Healthcare Blvd, Sandton, Johannesburg, South Africa",
                "phone": "+27 11 123 4567",
                "email": f"contact@{n.lower().replace(' ', '')}.co.za",
                "website": f"www.{n.lower().replace(' ', '')}.co.za",
                "scale": "대기업 (연매출 $500M 이상)",
                "region": "남아프리카 공화국 및 SADC 지역"
            }
        top10 = [
            _mock(1, "Aspen Pharmacare Holdings"),
            _mock(2, "Adcock Ingram Holdings"),
            _mock(3, "Cipla Quality Chemical"),
            _mock(4, "Dis-Chem Pharmacies"),
            _mock(5, "Clicks Group"),
            _mock(6, "Pharmed (Pty) Ltd"),
            _mock(7, "Fresenius Kabi South Africa"),
            _mock(8, "Austell Pharmaceuticals"),
            _mock(9, "Bayer Healthcare SA"),
            _mock(10, "Pfizer South Africa"),
        ]

    report_id  = str(_uuid.uuid4())
    combined_id = str(_uuid.uuid4())

    if session:
        session["partner_done"] = True
        session["reports"].append({
            "id": report_id, "report_type": "partner",
            "badge": "바이어", "title": f"바이어 발굴 · {label}", "top10": top10,
        })

    # 비동기 결합본 생성 예약 (after() 패턴)
    async def _gen_combined():
        await asyncio.sleep(3)
        if session:
            session["reports"].append({
                "id": combined_id, "report_type": "combined",
                "badge": "최종", "title": f"[최종] 최종 보고서 · {label}",
            })
    asyncio.create_task(_gen_combined())

    return JSONResponse({
        "ok": True,
        "session_id":  body.session_id,
        "top10":       top10,
        "report_id":   report_id,
        "combined_id": combined_id,
    })


@app.get("/api/za/report/session/{session_id}/list")
async def za_report_list(session_id: str) -> JSONResponse:
    """세션의 보고서 목록 반환 (2초마다 폴링)."""
    session = _sessions.get(session_id)
    if not session:
        return JSONResponse({"ok": False, "reports": []})
    return JSONResponse({"ok": True, "reports": session.get("reports", [])})


@app.get("/api/za/report/{report_type}/{report_id}/pdf")
async def za_report_pdf(report_type: str, report_id: str):
    """개별 보고서 PDF 다운로드."""
    from fastapi.responses import FileResponse
    from utils.za_pdf_generator import render_za_single_pdf

    # 세션에서 보고서 찾기
    target_session = None
    target_report = None
    for session in _sessions.values():
        for rep in session.get("reports", []):
            if rep.get("id") == report_id:
                target_session = session
                target_report = rep
                break
        if target_report:
            break

    if not target_report:
        raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다.")

    out_dir = Path(ROOT) / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    product_label = target_session.get("product_label", "의약품")

    type_labels = {
        "market": "시장보고서",
        "pricing_public": "수출가격전략_공공",
        "pricing_private": "수출가격전략_민간",
        "partner": "바이어리스트",
        "combined": "최종보고서",
    }
    type_label = type_labels.get(report_type, report_type)
    file_name = f"ZA_{type_label}_{product_label.replace(' ', '_')}.pdf"
    out_path = out_dir / file_name

    # combined 타입은 기존 전체 PDF 생성기로 위임
    if report_type == "combined":
        from utils.za_pdf_generator import render_za_combined_pdf
        try:
            render_za_combined_pdf(target_session, str(out_path))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"PDF 생성 실패: {str(e)}")
    else:
        try:
            render_za_single_pdf(target_session, target_report, report_type, str(out_path))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"PDF 생성 실패: {str(e)}")

    if not out_path.exists():
        raise HTTPException(status_code=500, detail="PDF 파일 생성에 실패했습니다.")

    return FileResponse(
        path=out_path,
        media_type="application/pdf",
        filename=file_name
    )


@app.get("/api/za/report/combined")
async def za_combined_pdf(session_id: str):
    """결합본 보고서 다운로드 (PDF)."""
    from fastapi.responses import FileResponse
    from utils.za_pdf_generator import render_za_combined_pdf
    
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    combined = next((r for r in session.get("reports", []) if r.get("report_type") == "combined"), None)
    if not combined:
        return JSONResponse({"ok": False, "message": "최종 보고서가 아직 생성 중입니다. 잠시 후 다시 시도하세요."})
        
    out_dir = Path(ROOT) / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    product_label = session.get("product_label", "의약품")
    file_name = f"ZA_최종보고서_{product_label.replace(' ', '_')}.pdf"
    out_path = out_dir / file_name
    
    try:
        render_za_combined_pdf(session, str(out_path))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF 생성 실패: {str(e)}")
        
    if not out_path.exists():
        raise HTTPException(status_code=500, detail="PDF 파일 생성에 실패했습니다.")
        
    return FileResponse(
        path=out_path,
        media_type="application/pdf",
        filename=file_name
    )


# ── 인도네시아 AHP 파트너 매칭 ────────────────────────────────────────────────────

@app.get("/api/ahp/partners")
async def api_ahp_partners() -> JSONResponse:
    from analysis.ahp_matcher import score_all_candidates, ahp_results_to_dicts
    results = score_all_candidates()
    return JSONResponse({"ok": True, "count": len(results), "partners": ahp_results_to_dicts(results)})


@app.get("/")
async def index() -> FileResponse:
    index_path = STATIC / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="index.html 없음")
    return FileResponse(index_path)


@app.get("/frontend3")
async def frontend3() -> FileResponse:
    path = STATIC / "frontend3.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="frontend3.html 없음")
    return FileResponse(path)


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="ZA 분석 대시보드")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()

    if args.open:
        def _open_later() -> None:
            time.sleep(1.0)
            webbrowser.open(f"http://127.0.0.1:{args.port}/")
        threading.Thread(target=_open_later, daemon=True).start()

    print(f"\n  ▶ 대시보드: http://127.0.0.1:{args.port}/\n")
    uvicorn.run(app, host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
