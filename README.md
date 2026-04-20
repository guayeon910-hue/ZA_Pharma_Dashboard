# 남아프리카공화국(ZA) 의약품 수출 적합성 분석 시스템

> **대상 독자:** 코딩을 몰라도 사용할 수 있도록 작성되었습니다.

---

## 이 프로그램은 무엇인가요?

남아프리카공화국(South Africa)에 수출하려는 **8가지 의약품**이 현지 시장에서 팔릴 수 있는지를 자동으로 분석해 주는 도구입니다.

직접 사이트를 돌아다니며 가격을 찾거나, 규제 서류를 읽을 필요 없이 **버튼 한 번**으로 아래 세 가지를 자동으로 처리합니다.

1. **크롤링** — 남아공 약국·규제기관·조달 포털에서 가격과 등재 정보를 자동 수집
2. **AI 분석** — Claude AI가 수집된 데이터를 바탕으로 수출 적합성을 판단
3. **보고서 생성** — 분석 결과를 PDF 보고서로 자동 생성

---

## 분석 대상 품목 (8가지)

| 품목 | 성분 |
|------|------|
| Hydrine | 하이드록시우레아 500mg |
| Gadvoa | 가도부트롤 604mg |
| Sereterol/Activair | 플루티카손+살메테롤 (흡입제) |
| Omethyl/Cutielet | 오메가-3 지방산 2g |
| Rosumeg Combigel | 로수바스타틴+오메가-3 |
| Atmeg Combigel | 아토르바스타틴+오메가-3 |
| Ciloduo | 실로스타졸+로수바스타틴 |
| Gastiin CR | 모사프리드 |

---

## 크롤링 대상 사이트 (남아공)

| 사이트 | 수집 정보 | Tier |
|--------|----------|------|
| **Clicks** (약국 체인 1위) | 소매가 · ClubCard 할인가 · 포장 단위 | Tier 1 |
| **Dis-Chem** (약국 체인 2위) | 소매가 · Loyalty Benefit 할인가 | Tier 1 |
| **SAHPRA** (규제기관) | 등록 의약품 DB · 시설 라이선스 · 안전성 경보 | Tier 2 |
| **MPR** (약가 레지스트리) | Single Exit Price(SEP) + 조제수수료 역산 | Tier 3 |
| **eTender** (국가 조달 포털) | 의약품 입찰공고 · 낙찰가 · 첨부 PDF | Tier 4 |
| **MHPL** (보건부 마스터 목록) | EML 등재 여부 · 병원급 공급가 (VAT 포함) | Tier 4 |

---

## 시작 전 준비사항

### 1. Python 설치 확인

터미널(검은 창)을 열고 아래 명령어를 입력합니다.

```
python3 --version
```

`Python 3.10` 이상이 나오면 됩니다. 없으면 [python.org](https://www.python.org/downloads/)에서 다운로드하세요.

---

### 2. 프로그램 설치 (최초 1회만)

터미널에서 이 프로그램 폴더로 이동한 뒤 아래를 순서대로 실행합니다.

```bash
# 가상환경 만들기
python3 -m venv .venv

# 필요한 패키지 설치
.venv/bin/pip install -r requirements.txt

# AI 분석 기능 사용 시 추가 설치
.venv/bin/pip install anthropic

# 브라우저 자동화(실크롤) 사용 시 추가 설치
.venv/bin/pip install playwright
.venv/bin/playwright install chromium
```

---

### 3. API 키 설정 (필수)

루트 폴더(이 README가 있는 폴더)에 `.env` 파일을 만들고 아래처럼 입력합니다.

```
# AI 분석
ANTHROPIC_API_KEY=여기에_Claude_API_키_입력
PERPLEXITY_API_KEY=여기에_Perplexity_API_키_입력

# DB
SUPABASE_URL=여기에_Supabase_URL_입력
SUPABASE_KEY=여기에_Supabase_키_입력

# 실크롤 활성화 (1로 설정 시 실제 브라우저/HTTP 요청 실행)
PLAYWRIGHT_LIVE=1

# 크롤 우회 에스컬레이션 (선택)
JINA_API_KEY=
```

> **중요:** API 키가 없으면 PDF 보고서에 "API 키 미설정" 메시지만 출력됩니다.
> 실제 수출 적합성 분석은 `ANTHROPIC_API_KEY`가 반드시 필요합니다.

---

## 실행 방법

### Render 배포 (권장)

1. 이 저장소를 GitHub에 push 합니다.
2. Render 대시보드에서 **New + → Blueprint** 를 선택하고 저장소를 연결합니다.
3. 루트의 `render.yaml`을 자동 인식하면 서비스가 생성됩니다.
4. Render 환경변수에 아래 키를 입력합니다 (`sync: false` 항목):
   - `SUPABASE_URL`
   - `SUPABASE_KEY`
   - `ANTHROPIC_API_KEY`
   - `PERPLEXITY_API_KEY`
5. 배포 완료 후 `GET /api/health` 가 `{"ok": true, "service": "za-analysis-dashboard"}`를 반환하면 정상입니다.

### 로컬 실행 (가장 쉬운 방법)

```bash
sh run_dashboard.sh
```

자동으로 서버가 켜지고 브라우저에 대시보드가 열립니다.
브라우저가 안 열리면 주소창에 직접 입력하세요:

```
http://127.0.0.1:8765/
```

---

## 대시보드 사용법

### 1단계: 크롤링 시작

왼쪽 상단 **"크롤링 시작"** 버튼을 클릭합니다.

- 오른쪽 로그 창에 실시간으로 진행 상황이 표시됩니다.
- Clicks/Dis-Chem 소매가, SAHPRA 등록 여부, MPR SEP 가격, eTender 입찰 정보 등이 수집됩니다.
- 완료까지 보통 **2~5분** 소요됩니다.

### 2단계: AI 분석 실행

**"AI 분석 실행"** 버튼을 클릭합니다.

- Claude AI가 남아공 시장 관점에서 품목별 수출 적합성을 실시간 분석합니다.
- 공공(MHPL/EML 등재) vs 민간(SEP 소매가) 이중 시장 구조를 기준으로 판정합니다.
- 완료 후 품목별 판정(적합/부적합/조건부)과 근거 문단이 표시됩니다.

### 3단계: 보고서 생성

**"보고서 생성"** 버튼을 클릭합니다.

- `reports/` 폴더에 PDF 파일이 저장됩니다.

---

## 폴더 구조

```
NAMAGONG/
├── run_dashboard.sh            ← 로컬 실행 스크립트
├── .env                        ← API 키 설정 파일
├── requirements.txt            ← 필요한 패키지 목록
├── render.yaml                 ← Render 배포 설정
│
├── frontend/
│   ├── server.py               ← FastAPI 웹 서버
│   └── static/                 ← 대시보드 프론트엔드
│
├── utils/
│   ├── za_clicks_crawler.py    ← Clicks 소매가 크롤러
│   ├── za_dischem_crawler.py   ← Dis-Chem 소매가 크롤러
│   ├── za_sahpra_crawler.py    ← SAHPRA 규제 데이터 크롤러
│   ├── za_mpr_crawler.py       ← MPR SEP 약가 크롤러
│   ├── za_mhpl_crawler.py      ← MHPL 보건부 마스터 목록 크롤러
│   ├── za_etender_crawler.py   ← eTender 공공 조달 크롤러
│   ├── za_macro.py             ← ZAR/KRW/USD 환율 & 거시지표
│   ├── za_parser.py            ← ZAR 가격 파서 & 이상치 탐지
│   └── db.py                   ← Supabase DB (ZA 전용 격리)
│
├── analysis/
│   └── za_export_analyzer.py   ← Claude AI 수출 적합성 분석 엔진
│
├── supabase/
│   └── schema_za_tables.sql    ← ZA 전용 DB 스키마
│
└── reports/                    ← 생성된 PDF 보고서 저장 위치
```

---

## 자주 묻는 질문

**Q. 버튼을 눌러도 아무것도 안 돼요.**  
A. 터미널에서 `sh run_dashboard.sh`가 실행 중인지 확인하세요.

**Q. AI 분석 버튼을 눌렀는데 "API 키 미설정"이 나와요.**  
A. `.env` 파일에 `ANTHROPIC_API_KEY`가 올바르게 입력되었는지 확인하세요.

**Q. Clicks/Dis-Chem 크롤링이 "차단됨"으로 나와요.**  
A. 해당 사이트는 봇 차단 정책이 있습니다. Jina AI 폴백이 자동으로 실행됩니다.  
`JINA_API_KEY`를 설정하면 더 안정적으로 수집할 수 있습니다.

**Q. SEP 가격이 안 나와요.**  
A. MPR(mpr.gov.za) 서버가 간헐적으로 다운될 수 있습니다. 캐시된 데이터가 사용됩니다.

**Q. 다른 팀원의 DB 데이터가 덮어써질 수 있나요?**  
A. 아닙니다. `za_` 테이블 접두어와 `country="ZA"` 격리 가드로 ZA 전용 데이터만 읽고 씁니다.
