-- =============================================================================
-- ZA (남아프리카공화국) 전용 테이블 (Supabase SQL Editor에서 한 번 실행)
-- 팀 공통 6컬럼: id, product_id, market_segment, fob_estimated_usd, confidence, crawled_at
-- =============================================================================

-- 1. ZA 메인 가격·시장 데이터 테이블 (팀 공통 6컬럼 준수)
CREATE TABLE IF NOT EXISTS za (
  -- ⭐ 공통 6컬럼 (절대 변경 금지, 팀 헌법)
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id            UUID NOT NULL,          -- 8개 제품 UUID 중 하나
  market_segment        TEXT NOT NULL CHECK (market_segment IN ('public', 'private', 'default')),
  fob_estimated_usd     DECIMAL,               -- 2공정 결과물 (1공정 때는 NULL)
  confidence            DECIMAL CHECK (confidence >= 0 AND confidence <= 1),
  crawled_at            TIMESTAMPTZ DEFAULT now(),

  -- 🎨 ZA 전용 컬럼
  inn_name              TEXT,
  brand_name            TEXT,
  source_site           TEXT,                   -- clicks | dischem | mhpl | etender | sahpra | mpr
  raw_price_zar         DECIMAL(14,2),
  price_per_unit_zar    DECIMAL(14,4),
  clubcard_price_zar    DECIMAL(14,2),          -- Clicks ClubCard 할인가
  benefit_price_zar     DECIMAL(14,2),          -- Dis-Chem Loyalty 할인가
  sep_zar               DECIMAL(14,2),          -- Single Exit Price (MPR)
  dispensing_fee_zar    DECIMAL(14,2),          -- 조제수수료
  pack_size             TEXT,
  strength_mg           TEXT,
  dosage_form           TEXT,
  manufacturer          TEXT,
  vat_rate              DECIMAL(5,4) DEFAULT 0.15,
  currency              TEXT DEFAULT 'ZAR',
  source_url            TEXT,
  raw_text              TEXT
);

CREATE INDEX IF NOT EXISTS idx_za_product_id  ON za(product_id);
CREATE INDEX IF NOT EXISTS idx_za_segment     ON za(market_segment);
CREATE INDEX IF NOT EXISTS idx_za_source      ON za(source_site);
CREATE INDEX IF NOT EXISTS idx_za_crawled     ON za(crawled_at DESC);

ALTER TABLE za DISABLE ROW LEVEL SECURITY;

-- =============================================================================
-- 2. 리포트 세션 테이블 (보고서 탭용)
-- =============================================================================
CREATE TABLE IF NOT EXISTS za_report_sessions (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id            UUID NOT NULL,
  product_label         TEXT,
  market_research_done  BOOLEAN DEFAULT false,
  pricing_done          BOOLEAN DEFAULT false,
  partner_done          BOOLEAN DEFAULT false,
  created_at            TIMESTAMPTZ DEFAULT now(),
  updated_at            TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE za_report_sessions DISABLE ROW LEVEL SECURITY;

-- =============================================================================
-- 3. 생성된 보고서 목록 테이블
-- =============================================================================
CREATE TABLE IF NOT EXISTS za_reports (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id            UUID REFERENCES za_report_sessions(id) ON DELETE CASCADE,
  report_type           TEXT NOT NULL CHECK (report_type IN ('market', 'pricing_public', 'pricing_private', 'partner', 'combined')),
  title                 TEXT NOT NULL,
  badge                 TEXT,                   -- [최종] | [공공] | [민간] | [가격] | [바이어]
  content_json          JSONB,                  -- 보고서 내용 (구조화)
  pdf_path              TEXT,                   -- 저장된 PDF 경로
  created_at            TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_za_reports_session ON za_reports(session_id);
CREATE INDEX IF NOT EXISTS idx_za_reports_type    ON za_reports(report_type);
CREATE INDEX IF NOT EXISTS idx_za_reports_created ON za_reports(created_at DESC);

ALTER TABLE za_reports DISABLE ROW LEVEL SECURITY;

-- =============================================================================
-- 4. SAHPRA 등록 제품 현황
-- =============================================================================
CREATE TABLE IF NOT EXISTS za_sahpra_products (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  applicant             TEXT NOT NULL,
  product_name          TEXT NOT NULL,
  api                   TEXT,
  registration_number   TEXT UNIQUE,
  application_number    TEXT,
  registration_date     TEXT,
  status                TEXT DEFAULT 'Active',
  source_url            TEXT,
  raw_text              TEXT,
  scraped_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE za_sahpra_products DISABLE ROW LEVEL SECURITY;

-- =============================================================================
-- 5. SEP 레지스트리 (MPR — 단일 출고가 이력)
-- =============================================================================
CREATE TABLE IF NOT EXISTS za_sep_registry (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_name          TEXT NOT NULL,
  api                   TEXT,
  nappi_code            TEXT,
  sep_zar               DECIMAL(14,2) NOT NULL,
  dispensing_fee_zar    DECIMAL(14,2),
  patient_price_zar     DECIMAL(14,2),
  pack_size             TEXT,
  manufacturer          TEXT,
  uploaded_date         TEXT,
  source_url            TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE za_sep_registry DISABLE ROW LEVEL SECURITY;

-- =============================================================================
-- 6. eTender 낙찰 이력
-- =============================================================================
CREATE TABLE IF NOT EXISTS za_tenders (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tender_no             TEXT,
  description           TEXT NOT NULL,
  organ_of_state        TEXT,
  published_date        TEXT,
  closing_date          TEXT,
  status                TEXT DEFAULT 'open',
  attachment_urls       JSONB DEFAULT '[]'::jsonb,
  source_url            TEXT,
  raw_text              TEXT,
  scraped_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE za_tenders DISABLE ROW LEVEL SECURITY;

-- =============================================================================
-- 7. 안전성 경보
-- =============================================================================
CREATE TABLE IF NOT EXISTS za_safety_alerts (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title                 TEXT NOT NULL,
  category              TEXT CHECK (category IN ('recall', 'pharmacovigilance', 'safety_communication')),
  alert_date            TEXT,
  url                   TEXT,
  summary               TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE za_safety_alerts DISABLE ROW LEVEL SECURITY;

-- =============================================================================
-- 8개 제품 UUID (절대 재생성 금지 — 팀 공통)
-- Rosumeg Combigel:      2504d79b-c2ce-4660-9ea7-5576c8bb755f
-- Atmeg Combigel:        859e60f9-8544-43b3-a6a0-f6c7529847eb
-- Ciloduo:               fcae4399-aa80-4318-ad55-89d6401c10a9
-- Gastiin CR:            24738c3b-3a5b-40a9-9e8e-889ec075b453
-- Omethyl Cutielet:      f88b87b8-c0ab-4f6e-ba34-e9330d1d4e18
-- Sereterol Activair:    014fd4d2-dc66-4fc1-8d4f-59695183387f
-- Gadvoa Inj:            895f49ae-6ce3-44a3-93bd-bb77e027ba59
-- Hydrine:               bdfc9883-6040-438a-8e7a-df01f1230682
-- =============================================================================
