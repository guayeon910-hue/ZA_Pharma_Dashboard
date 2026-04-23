"""
남아프리카공화국 최종 보고서 PDF 생성기.

싱가포르 DOCX 양식을 ReportLab으로 완벽히 복제하여
표지 → 시장보고서 → 수출가격전략 → 바이어리스트 순서로 PDF를 생성한다.
"""
import re
from pathlib import Path
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

ROOT = Path(__file__).resolve().parent.parent

# ── 한글 폰트 등록 ─────────────────────────────────────────────────────────────
_FONT_CACHE = None

def _register_korean_font():
    global _FONT_CACHE
    if _FONT_CACHE is not None:
        return _FONT_CACHE
    candidates = [
        ("NanumGothic", str(ROOT / "fonts" / "NanumGothic.ttf")),
        ("AppleGothic", "/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
        ("MalgunGothic", "C:/Windows/Fonts/malgun.ttf"),
    ]
    for name, path in candidates:
        if Path(path).is_file():
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                pdfmetrics.registerFont(TTFont(f"{name}-Bold", path))
                _FONT_CACHE = name
                return name
            except Exception:
                continue
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
        _FONT_CACHE = "HYSMyeongJo-Medium"
        return "HYSMyeongJo-Medium"
    except Exception:
        pass
    _FONT_CACHE = "Helvetica"
    return "Helvetica"


def _rx(t):
    """XML escape."""
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _clean_prose(text: str) -> str:
    """AI 생성 텍스트의 불릿/줄바꿈 정리."""
    s = (text or "").strip()
    if not s:
        return s
    lines = s.splitlines()
    cleaned = []
    for line in lines:
        line = line.strip()
        line = re.sub(r'^[\-\•\*\·]\s+', '', line)
        line = re.sub(r'^\d+[\.\)]\s+', '', line)
        if line:
            cleaned.append(line)
    return ' '.join(cleaned)


def _split_market_sections(text: str) -> dict:
    """시장 분석 텍스트를 섹션별로 분리.
    
    Claude가 생성한 마크다운 형태의 텍스트에서
    ## 또는 **숫자.** 패턴으로 구분하여 섹션별 딕셔너리를 반환.
    """
    sections = {}
    current_key = "개요"
    current_lines = []

    for line in (text or "").splitlines():
        stripped = line.strip()
        # "## 1. 제목" 또는 "**1. 제목**" 패턴 탐지
        m = re.match(r'^(?:#{1,3}\s*)?(?:\*\*)?(\d+[\.\)]?\s*.+?)(?:\*\*)?$', stripped)
        if m and len(stripped) < 80:
            if current_lines:
                sections[current_key] = '\n'.join(current_lines).strip()
            current_key = m.group(1).strip().rstrip('*').strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections[current_key] = '\n'.join(current_lines).strip()

    return sections


# ── 메인 PDF 렌더링 함수 ───────────────────────────────────────────────────────

def render_za_combined_pdf(session: dict, out_path: str):
    """세션 데이터를 기반으로 남아공 최종 보고서 PDF를 생성한다."""

    W, H = A4
    MARGIN = 20 * mm
    CONTENT_W = W - 2 * MARGIN

    base_font = _register_korean_font()
    bold_font = f"{base_font}-Bold" if base_font != "HYSMyeongJo-Medium" else base_font

    # ── 색상 ───────────────────────────────────────────────────────────────────
    C_NAVY   = colors.HexColor("#1B2A4A")
    C_BODY   = colors.HexColor("#1A1A1A")
    C_BORDER = colors.HexColor("#D0D7E3")
    C_ALT    = colors.HexColor("#F4F6F9")
    C_GRAY   = colors.HexColor("#444444")
    C_LGRAY  = colors.HexColor("#888888")

    # ── 스타일 ─────────────────────────────────────────────────────────────────
    def ps(name, **kw):
        return ParagraphStyle(name, **kw)

    # 표지용
    s_cover_title = ps("CoverTitle", fontName=bold_font, fontSize=28, leading=36,
                       alignment=TA_CENTER, textColor=C_BODY)
    s_cover_sub   = ps("CoverSub", fontName=base_font, fontSize=18, leading=24,
                       alignment=TA_CENTER, textColor=C_GRAY)
    s_cover_date  = ps("CoverDate", fontName=base_font, fontSize=12, leading=16,
                       alignment=TA_CENTER, textColor=C_LGRAY)
    s_cover_foot  = ps("CoverFoot", fontName=bold_font, fontSize=14, leading=20,
                       alignment=TA_CENTER, textColor=C_BODY)

    # 본문용
    s_page_title = ps("PageTitle", fontName=bold_font, fontSize=16, leading=22,
                      textColor=C_BODY, spaceAfter=4)
    s_page_sub   = ps("PageSub", fontName=base_font, fontSize=10, leading=14,
                      textColor=C_LGRAY, spaceAfter=12)
    s_h1 = ps("H1", fontName=bold_font, fontSize=13, leading=18,
              textColor=C_BODY, spaceBefore=14, spaceAfter=6)
    s_h2 = ps("H2", fontName=bold_font, fontSize=11, leading=16,
              textColor=C_BODY, spaceBefore=10, spaceAfter=4)
    s_body = ps("Body", fontName=base_font, fontSize=10, leading=16,
                textColor=C_BODY, alignment=TA_JUSTIFY, spaceAfter=6)
    s_body_sm = ps("BodySm", fontName=base_font, fontSize=9, leading=14,
                   textColor=C_BODY, spaceAfter=4)
    s_note = ps("Note", fontName=base_font, fontSize=9, leading=13,
                textColor=C_LGRAY, spaceAfter=10)

    # 표 셀용
    s_cell_h = ps("CellH", fontName=bold_font, fontSize=9.5, textColor=C_BODY, leading=14, wordWrap="CJK")
    s_cell   = ps("Cell", fontName=base_font, fontSize=9.5, textColor=C_BODY, leading=14, wordWrap="CJK")
    s_cell_sm = ps("CellSm", fontName=base_font, fontSize=8, textColor=C_LGRAY, leading=11, wordWrap="CJK")
    s_hdr = ps("HdrWhite", fontName=bold_font, fontSize=9, textColor=colors.white, leading=13, wordWrap="CJK")

    COL_L = CONTENT_W * 0.22
    COL_R = CONTENT_W * 0.78

    def _base_tbl_style(extra=None):
        cmds = [
            ("GRID",          (0, 0), (-1, -1), 0.5, C_BORDER),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ]
        if extra:
            cmds.extend(extra)
        return TableStyle(cmds)

    def _kv_table(rows, shade_header=True):
        """2-column key-value table."""
        pdata = [
            [Paragraph(_rx(k), s_cell_h), Paragraph(_rx(v), s_cell)]
            for k, v in rows
        ]
        extras = []
        if shade_header:
            for i in range(len(rows)):
                extras.append(("BACKGROUND", (0, i), (0, i), C_ALT))
        t = Table(pdata, colWidths=[COL_L, COL_R])
        t.setStyle(_base_tbl_style(extras))
        return t

    # ── 데이터 추출 ────────────────────────────────────────────────────────────
    reports      = session.get("reports", [])
    product_label = session.get("product_label", "의약품")
    today_str     = datetime.now().strftime("%Y-%m-%d")

    market_report  = next((r for r in reports if r.get("report_type") == "market"), None)
    pricing_report = next((r for r in reports if r.get("report_type") in ("pricing_public", "pricing_private")), None)
    partner_report = next((r for r in reports if r.get("report_type") == "partner"), None)

    # ── 문서 생성 ──────────────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title=f"남아프리카공화국 진출 전략 보고서 — {product_label}",
    )
    story = []

    # ═══════════════════════════════════════════════════════════════════════════
    # 표지 (Cover Page)
    # ═══════════════════════════════════════════════════════════════════════════
    story.append(Spacer(1, H * 0.28))
    story.append(Paragraph("남아프리카공화국 진출 전략 보고서", s_cover_title))
    story.append(Spacer(1, 20))
    story.append(Paragraph("한국유나이티드제약", s_cover_sub))
    story.append(Spacer(1, 12))
    story.append(Paragraph(today_str, s_cover_date))
    story.append(Spacer(1, 60))
    story.append(Paragraph("수출가격 전략 – 바이어 후보 리스트 – 시장분석", s_cover_foot))
    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════════
    # 제2장: 수출 가격 전략 보고서
    # ═══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph(f"남아프리카공화국 수출 가격 전략 보고서 — {product_label}", s_page_title))
    story.append(Paragraph(f"{product_label}  |  {today_str}", s_page_sub))

    if pricing_report:
        scenarios = pricing_report.get("scenarios", {})

        # 1. 거시 시장 개요 (간략)
        story.append(Paragraph("1. 남아프리카공화국 거시 시장", s_h1))
        macro_text = (
            "남아프리카공화국은 인구 약 6,040만 명, GDP 약 USD 4,050억(IMF 2023)으로 아프리카 대륙 최대 의약품 시장입니다. "
            "의약품 시장 규모는 약 USD 3.2B(2024 추산)이며, 공공입찰(SAHPRA·국영병원 조달)과 민간소매(Dis-Chem·Clicks 약국체인) "
            "이중 채널 구조를 갖추고 있습니다. 공공 부문은 국가 필수의약품 목록(EML) 기반의 입찰 가격 경쟁이 치열하며, "
            "민간 부문은 Single Exit Price(SEP) 제도에 의해 제조사 출고 단일가가 규정됩니다."
        )
        story.append(Paragraph(_rx(macro_text), s_body))
        story.append(Spacer(1, 6))

        # 2. 단가 (시장 기준가)
        story.append(Paragraph(f"2. {product_label} 단가 (시장 기준가)", s_h1))
        baseline_sc = scenarios.get("baseline", {})
        base_usd = baseline_sc.get("fob_usd", 0)
        base_zar = baseline_sc.get("zar", 0)
        story.append(_kv_table([
            ["기준 가격", f"USD {base_usd} / ZAR {base_zar}"],
            ["산정 방식", "AI 분석 (Claude) — 남아공 공공입찰가·민간 SEP 가격 범위 및 레퍼런스 제품 대비 상대가 포지셔닝"],
            ["시장 구분", "공공 / 민간"],
        ]))
        story.append(Spacer(1, 8))

        # 3. 가격 시나리오
        story.append(Paragraph("3. 가격 시나리오", s_h1))

        scenario_labels = {
            "conservative": ("저가 진입", "공공 조달 채널 진입을 위한 경쟁 가격. 국영 병원·클리닉 대량 납품 시 초기 시장 점유율 확보에 적합한 포지셔닝."),
            "baseline":     ("기준가",   "공공·민간 시장 균형 포지셔닝. 국내 원가 마진과 현지 유통비를 균형 있게 커버하는 최적 진입가."),
            "premium":      ("프리미엄", "브랜드 가치·품질 차별화 전략. 민간 약국체인(Dis-Chem, Clicks) 및 상급 의료기관 대상 프리미엄 포지셔닝."),
        }

        for key in ["conservative", "baseline", "premium"]:
            sc = scenarios.get(key, {})
            label_ko, rationale = scenario_labels.get(key, (key, ""))
            fob_usd = sc.get("fob_usd", 0)
            zar_val = sc.get("zar", 0)

            story.append(Paragraph(f"[{label_ko}]  USD {fob_usd} / ZAR {zar_val}", s_h2))
            story.append(Paragraph(f"<b>근거</b>  {_rx(rationale)}", s_body))
            # FOB 역산식
            fob_formula = (
                f"ZAR {zar_val} ÷ (1+VAT 15%) ÷ (1+유통마진 20%) ÷ (1+수입비용 15%) "
                f"× 환율(ZAR/USD ≈ 18) ≈ USD {fob_usd}"
            )
            story.append(Paragraph(f"<b>FOB 수출가 역산식</b>  {_rx(fob_formula)}", s_body_sm))
            story.append(Spacer(1, 4))

        story.append(Paragraph(
            "※ 본 산출 결과는 AI 분석에 기반한 추정치이므로, 최종 의사결정 전 반드시 담당자의 검토 및 확인이 필요합니다.",
            s_note
        ))
    else:
        story.append(Paragraph("가격 분석이 아직 완료되지 않았습니다.", s_body))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════════
    # 제3장: 바이어 후보 리스트
    # ═══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph(f"남아프리카공화국 바이어 후보 리스트 — {product_label}", s_page_title))
    story.append(Paragraph(f"{product_label}  |  {today_str}", s_page_sub))
    story.append(Paragraph(
        "※ 아래 바이어 후보는 AI 분석 및 기업 데이터베이스를 통해 도출되었으며, 개별 기업의 현지 진출 현황은 추가 실사가 필요합니다.",
        s_note
    ))

    if partner_report:
        top10 = partner_report.get("top10", [])

        # 1. 요약 리스트
        story.append(Paragraph("1. 남아프리카공화국 현지 바이어 후보 리스트", s_h1))
        for buyer in top10:
            rank = buyer.get("rank", "")
            name = buyer.get("name", "")
            email = buyer.get("email", "—")
            story.append(Paragraph(
                f"<b>{rank})  {_rx(name)}</b>  |  {_rx(email)}",
                s_body
            ))
        story.append(Spacer(1, 10))

        # 2. 상세 정보
        story.append(Paragraph("2. 우선 접촉 바이어 상세 정보 (상위 10개사)", s_h1))
        story.append(Paragraph(
            f"※ 하기 기업들은 {product_label}의 남아공 시장 진입 파트너로서의 적합성을 종합 평가하여 선정하였습니다.",
            s_note
        ))

        for buyer in top10:
            rank    = buyer.get("rank", "")
            name    = buyer.get("name", "")
            overview = buyer.get("overview", "상세 정보가 제공되지 않았습니다.")
            reason  = buyer.get("reason", "")
            addr    = buyer.get("address", "—")
            phone   = buyer.get("phone", "—")
            email   = buyer.get("email", "—")
            web     = buyer.get("website", "—")
            scale   = buyer.get("scale", "—")
            region  = buyer.get("region", "—")

            story.append(Paragraph(f"{rank}. {_rx(name)}", s_h2))

            story.append(Paragraph("<b>기업 개요</b>", s_body_sm))
            story.append(Paragraph(_rx(overview), s_body))

            story.append(Paragraph("<b>추천 이유</b>", s_body_sm))
            story.append(Paragraph(_rx(reason), s_body))

            # 연락처 테이블
            contact_rows = [
                ["주소", addr],
                ["전화", phone],
                ["이메일", email],
                ["홈페이지", web],
                ["기업 규모", scale],
                ["사업 지역", region],
            ]
            ct = _kv_table(contact_rows)
            story.append(ct)
            story.append(Spacer(1, 12))
    else:
        story.append(Paragraph("바이어 발굴이 아직 완료되지 않았습니다.", s_body))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════════════
    # 제1장: 시장 보고서 (맨 뒤에 배치 — 양식 순서: 가격전략 → 바이어 → 시장)
    # ═══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph(f"남아프리카공화국 시장보고서 — {product_label}", s_page_title))
    story.append(Paragraph(f"{product_label}  |  {today_str}", s_page_sub))

    if market_report:
        market_text = market_report.get("content", "")
        if isinstance(market_text, str) and market_text.strip():
            # 섹션 분리 시도
            sections = _split_market_sections(market_text)

            if len(sections) > 1:
                for sec_title, sec_body in sections.items():
                    story.append(Paragraph(_rx(sec_title), s_h1))
                    # 긴 텍스트를 문단으로 분리
                    paragraphs = [p.strip() for p in sec_body.split('\n\n') if p.strip()]
                    if not paragraphs:
                        paragraphs = [sec_body]
                    for para in paragraphs:
                        cleaned = _clean_prose(para)
                        if cleaned:
                            story.append(Paragraph(_rx(cleaned), s_body))
            else:
                # 섹션 분리가 안 되면 전체 텍스트를 그냥 출력
                full_text = _clean_prose(market_text)
                # 2000자 단위로 나누어 Paragraph 생성 (ReportLab 안정성)
                chunk_size = 2000
                for i in range(0, len(full_text), chunk_size):
                    chunk = full_text[i:i + chunk_size]
                    story.append(Paragraph(_rx(chunk), s_body))
        else:
            story.append(Paragraph("시장 조사 데이터가 없습니다.", s_body))
    else:
        story.append(Paragraph("시장 조사가 아직 완료되지 않았습니다.", s_body))

    # ── PDF 빌드 ───────────────────────────────────────────────────────────────
    doc.build(story)


# ── 개별 보고서 PDF 렌더링 ─────────────────────────────────────────────────────

def render_za_single_pdf(session: dict, report: dict, report_type: str, out_path: str):
    """개별 보고서 PDF를 생성한다 (시장조사, 공공가격, 민간가격, 바이어)."""

    W, H = A4
    MARGIN = 20 * mm
    CONTENT_W = W - 2 * MARGIN

    base_font = _register_korean_font()
    bold_font = f"{base_font}-Bold" if base_font != "HYSMyeongJo-Medium" else base_font

    C_BODY   = colors.HexColor("#1A1A1A")
    C_BORDER = colors.HexColor("#D0D7E3")
    C_ALT    = colors.HexColor("#F4F6F9")
    C_LGRAY  = colors.HexColor("#888888")

    def ps(name, **kw):
        return ParagraphStyle(name, **kw)

    s_title = ps("STitle", fontName=bold_font, fontSize=16, leading=22, textColor=C_BODY, spaceAfter=4)
    s_sub   = ps("SSub", fontName=base_font, fontSize=10, leading=14, textColor=C_LGRAY, spaceAfter=12)
    s_h1    = ps("SH1", fontName=bold_font, fontSize=13, leading=18, textColor=C_BODY, spaceBefore=14, spaceAfter=6)
    s_h2    = ps("SH2", fontName=bold_font, fontSize=11, leading=16, textColor=C_BODY, spaceBefore=10, spaceAfter=4)
    s_body  = ps("SBody", fontName=base_font, fontSize=10, leading=16, textColor=C_BODY, spaceAfter=6)
    s_body_sm = ps("SBodySm", fontName=base_font, fontSize=9, leading=14, textColor=C_BODY, spaceAfter=4)
    s_note  = ps("SNote", fontName=base_font, fontSize=9, leading=13, textColor=C_LGRAY, spaceAfter=10)
    s_cell_h = ps("SCellH", fontName=bold_font, fontSize=9.5, textColor=C_BODY, leading=14, wordWrap="CJK")
    s_cell   = ps("SCell", fontName=base_font, fontSize=9.5, textColor=C_BODY, leading=14, wordWrap="CJK")

    COL_L = CONTENT_W * 0.22
    COL_R = CONTENT_W * 0.78

    def _base_tbl_style(extra=None):
        cmds = [
            ("GRID", (0, 0), (-1, -1), 0.5, C_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]
        if extra:
            cmds.extend(extra)
        return TableStyle(cmds)

    def _kv_table(rows):
        pdata = [[Paragraph(_rx(k), s_cell_h), Paragraph(_rx(v), s_cell)] for k, v in rows]
        extras = [("BACKGROUND", (0, i), (0, i), C_ALT) for i in range(len(rows))]
        t = Table(pdata, colWidths=[COL_L, COL_R])
        t.setStyle(_base_tbl_style(extras))
        return t

    product_label = session.get("product_label", "의약품")
    today_str = datetime.now().strftime("%Y-%m-%d")

    type_titles = {
        "market": f"남아프리카공화국 시장보고서 — {product_label}",
        "pricing_public": f"남아프리카공화국 수출 가격 전략 [공공] — {product_label}",
        "pricing_private": f"남아프리카공화국 수출 가격 전략 [민간] — {product_label}",
        "partner": f"남아프리카공화국 바이어 후보 리스트 — {product_label}",
    }

    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title=type_titles.get(report_type, "보고서"),
    )
    story = []

    story.append(Paragraph(type_titles.get(report_type, "보고서"), s_title))
    story.append(Paragraph(f"{product_label}  |  {today_str}", s_sub))

    # ── 시장 보고서 ────────────────────────────────────────────────────────────
    if report_type == "market":
        market_text = report.get("content", "")
        if isinstance(market_text, str) and market_text.strip():
            sections = _split_market_sections(market_text)
            if len(sections) > 1:
                for sec_title, sec_body in sections.items():
                    story.append(Paragraph(_rx(sec_title), s_h1))
                    for para in [p.strip() for p in sec_body.split('\n\n') if p.strip()]:
                        cleaned = _clean_prose(para)
                        if cleaned:
                            story.append(Paragraph(_rx(cleaned), s_body))
            else:
                full_text = _clean_prose(market_text)
                for i in range(0, len(full_text), 2000):
                    story.append(Paragraph(_rx(full_text[i:i+2000]), s_body))
        else:
            story.append(Paragraph("시장 조사 데이터가 없습니다.", s_body))

    # ── 가격 전략 보고서 ───────────────────────────────────────────────────────
    elif report_type in ("pricing_public", "pricing_private"):
        scenarios = report.get("scenarios", {})
        seg_label = "공공" if report_type == "pricing_public" else "민간"

        story.append(Paragraph(f"1. {product_label} 단가 ({seg_label} 시장 기준가)", s_h1))
        baseline_sc = scenarios.get("baseline", {})
        story.append(_kv_table([
            ["기준 가격", f"USD {baseline_sc.get('fob_usd', 0)} / ZAR {baseline_sc.get('zar', 0)}"],
            ["산정 방식", "AI 분석 (Claude) — 남아공 공공입찰가·민간 SEP 가격 범위 참고"],
            ["시장 구분", seg_label],
        ]))
        story.append(Spacer(1, 8))

        story.append(Paragraph("2. 가격 시나리오", s_h1))
        scenario_labels = {
            "conservative": ("저가 진입", "공공 조달 채널 진입을 위한 경쟁 가격."),
            "baseline":     ("기준가",   "공공·민간 시장 균형 포지셔닝."),
            "premium":      ("프리미엄", "브랜드 가치·품질 차별화 전략."),
        }
        for key in ["conservative", "baseline", "premium"]:
            sc = scenarios.get(key, {})
            label_ko, rationale = scenario_labels.get(key, (key, ""))
            fob_usd = sc.get("fob_usd", 0)
            zar_val = sc.get("zar", 0)
            story.append(Paragraph(f"[{label_ko}]  USD {fob_usd} / ZAR {zar_val}", s_h2))
            story.append(Paragraph(f"<b>근거</b>  {_rx(rationale)}", s_body))
            fob_formula = f"ZAR {zar_val} ÷ (1+VAT 15%) ÷ (1+유통마진 20%) ÷ (1+수입비용 15%) × 환율 ≈ USD {fob_usd}"
            story.append(Paragraph(f"<b>FOB 수출가 역산식</b>  {_rx(fob_formula)}", s_body_sm))
            story.append(Spacer(1, 4))

        story.append(Paragraph("※ 본 산출 결과는 AI 분석에 기반한 추정치입니다.", s_note))

    # ── 바이어 리스트 ──────────────────────────────────────────────────────────
    elif report_type == "partner":
        top10 = report.get("top10", [])
        story.append(Paragraph("※ 아래 바이어 후보는 AI 분석을 통해 도출되었으며 추가 실사가 필요합니다.", s_note))

        if top10:
            story.append(Paragraph("1. 바이어 후보 상세 정보", s_h1))
            for buyer in top10:
                rank = buyer.get("rank", "")
                name = buyer.get("name", "")
                overview = buyer.get("overview", "—")
                reason = buyer.get("reason", "—")

                story.append(Paragraph(f"{rank}. {_rx(name)}", s_h2))
                story.append(Paragraph(f"<b>기업 개요</b>: {_rx(overview)}", s_body))
                story.append(Paragraph(f"<b>추천 이유</b>: {_rx(reason)}", s_body))

                ct = _kv_table([
                    ["주소", buyer.get("address", "—")],
                    ["전화", buyer.get("phone", "—")],
                    ["이메일", buyer.get("email", "—")],
                    ["홈페이지", buyer.get("website", "—")],
                    ["기업 규모", buyer.get("scale", "—")],
                    ["사업 지역", buyer.get("region", "—")],
                ])
                story.append(ct)
                story.append(Spacer(1, 10))
        else:
            story.append(Paragraph("바이어 데이터가 없습니다.", s_body))

    doc.build(story)
