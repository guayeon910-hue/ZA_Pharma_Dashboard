-- =============================================================================
-- ZA (남아프리카공화국) 전용 테이블 (Supabase SQL Editor에서 한 번 실행)
-- =============================================================================

-- 1. 소매/공공 통합 가격 테이블
create table if not exists za_pricing (
  id                    uuid primary key default gen_random_uuid(),
  product_id            text,
  market_segment        text check (market_segment in ('private', 'public')),
  inn_name              text not null,
  brand_name            text,
  source_site           text not null,   -- clicks | dischem | mhpl | etender
  raw_price_zar         numeric(14,2),
  price_per_unit_zar    numeric(14,4),
  clubcard_price_zar    numeric(14,2),   -- Clicks ClubCard 할인가
  benefit_price_zar     numeric(14,2),   -- Dis-Chem Loyalty 할인가
  pack_size             text,
  strength_mg           text,
  dosage_form           text,
  manufacturer          text,
  vat_rate              numeric(5,4) default 0.15,
  fob_estimated_usd     numeric(14,6),
  confidence            numeric(4,3) default 0.8,
  source_url            text,
  raw_text              text,
  crawled_at            timestamptz not null default now()
);

-- 2. SAHPRA 등록 제품 현황
create table if not exists za_sahpra_products (
  id                    uuid primary key default gen_random_uuid(),
  applicant             text not null,
  product_name          text not null,
  api                   text,
  registration_number   text unique,
  application_number    text,
  registration_date     text,
  status                text default 'Active',
  source_url            text,
  raw_text              text,
  scraped_at            timestamptz not null default now()
);

-- 3. SEP 레지스트리 (MPR — 단일 출고가 이력)
create table if not exists za_sep_registry (
  id                    uuid primary key default gen_random_uuid(),
  product_name          text not null,
  api                   text,
  nappi_code            text,
  sep_zar               numeric(14,2) not null,
  dispensing_fee_zar    numeric(14,2),
  patient_price_zar     numeric(14,2),
  pack_size             text,
  manufacturer          text,
  uploaded_date         text,
  source_url            text,
  created_at            timestamptz not null default now()
);

-- 4. eTender 낙찰 이력
create table if not exists za_tenders (
  id                    uuid primary key default gen_random_uuid(),
  tender_no             text,
  description           text not null,
  organ_of_state        text,
  published_date        text,
  closing_date          text,
  status                text default 'open',
  attachment_urls       jsonb default '[]'::jsonb,
  source_url            text,
  raw_text              text,
  scraped_at            timestamptz not null default now()
);

-- 5. MHPL No-Bid 기회 추적
create table if not exists za_nobid_opportunities (
  id                    uuid primary key default gen_random_uuid(),
  description           text not null,
  inn_name              text,
  eml_status            text,
  facility_level        text,
  source_url            text,
  detected_at           timestamptz not null default now()
);

-- 6. 품목별 분석 컨텍스트
create table if not exists za_product_context (
  id                    uuid primary key default gen_random_uuid(),
  product_id            text not null unique,
  sahpra_registered     boolean default false,
  competitor_count      int default 0,
  sep_avg_zar           numeric(14,2),
  eml_listed            boolean default false,
  verdict               text check (verdict in ('적합', '조건부', '부적합', '미분석')),
  analysis_text         text default '',
  built_at              timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

-- 7. 안전성 경보
create table if not exists za_safety_alerts (
  id                    uuid primary key default gen_random_uuid(),
  title                 text not null,
  category              text check (category in ('recall', 'pharmacovigilance', 'safety_communication')),
  alert_date            text,
  url                   text,
  summary               text,
  created_at            timestamptz not null default now()
);

-- =============================================================================
-- 인덱스
-- =============================================================================
create index if not exists idx_za_pricing_inn         on za_pricing(inn_name);
create index if not exists idx_za_pricing_site        on za_pricing(source_site);
create index if not exists idx_za_pricing_segment     on za_pricing(market_segment);
create index if not exists idx_za_pricing_crawled     on za_pricing(crawled_at desc);
create index if not exists idx_za_sahpra_api          on za_sahpra_products(api);
create index if not exists idx_za_sep_api             on za_sep_registry(api);
create index if not exists idx_za_tenders_closing     on za_tenders(closing_date);
create index if not exists idx_za_context_pid         on za_product_context(product_id);
