/**
 * ZA 수출가격 전략 대시보드 — app.js
 * 팀장 양식 기준: 2블록(수출가격전략 + 바이어발굴) + 보고서탭
 * 팀 공통 8개 제품 UUID 사용
 */

// ── 팀 공통 8개 제품 UUID (절대 변경 금지) ──────────────────────────────────
const PRODUCT_UUID = {
  sereterol_activair: '014fd4d2-dc66-4fc1-8d4f-59695183387f',
  omethyl_cutielet:   'f88b87b8-c0ab-4f6e-ba34-e9330d1d4e18',
  rosumeg_combigel:   '2504d79b-c2ce-4660-9ea7-5576c8bb755f',
  atmeg_combigel:     '859e60f9-8544-43b3-a6a0-f6c7529847eb',
  ciloduo:            'fcae4399-aa80-4318-ad55-89d6401c10a9',
  gastiin_cr:         '24738c3b-3a5b-40a9-9e8e-889ec075b453',
  hydrine:            'bdfc9883-6040-438a-8e7a-df01f1230682',
  gadvoa_inj:         '895f49ae-6ce3-44a3-93bd-bb77e027ba59',
};

const PRODUCT_LABEL = {
  sereterol_activair: 'Sereterol Activair',
  omethyl_cutielet:   'Omethyl Cutielet',
  rosumeg_combigel:   'Rosumeg Combigel',
  atmeg_combigel:     'Atmeg Combigel',
  ciloduo:            'Ciloduo',
  gastiin_cr:         'Gastiin CR',
  hydrine:            'Hydrine',
  gadvoa_inj:         'Gadvoa Inj',
};

// ── 앱 상태 ───────────────────────────────────────────────────────────────────
const state = {
  currentSessionId: null,
  currentProductKey: null,
  currentProductUuid: null,
  marketTab: 'public',
  marketDone: false,
  pricingDone: false,
  partnerDone: false,
  reports: [],
  prices: {
    conservative: { usd: null, zar: null },
    baseline:     { usd: null, zar: null },
    premium:      { usd: null, zar: null },
  },
  editingScenario: null,
  zarUsd: 0.055,
  reportPollTimer: null,
};

// ── 초기화 ────────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  loadExchangeRate();
  startReportPolling();
  updateMarketTabDesc();
});

async function loadExchangeRate() {
  try {
    const r = await fetch('/api/exchange');
    const d = await r.json();
    if (d.zar_usd) state.zarUsd = d.zar_usd;
  } catch (_) {}
}

// ── 품목 선택 → 분석 실행 버튼 활성화 ─────────────────────────────────────────
function onProductChange() {
  const sel = document.getElementById('sel-product');
  const key = sel.value;
  document.getElementById('btn-analyze').disabled = !key;
  state.currentProductKey = key || null;
  state.currentProductUuid = key ? PRODUCT_UUID[key] : null;
}

// ── 1단계: 분석 실행 (시장조사 자동) ────────────────────────────────────────────
async function runAnalyze() {
  const key = state.currentProductKey;
  if (!key) return;

  const label = PRODUCT_LABEL[key] || key;
  const uuid  = PRODUCT_UUID[key];

  setBtnLoading('btn-analyze', true, '▶ 분석 실행');

  try {
    const r = await fetch('/api/za/report/session/init', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_key: key, product_id: uuid, product_label: label }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || '서버 오류');

    state.currentSessionId = d.session_id;
    state.marketDone = true;

    // 완료 배너
    const bannerEl = document.getElementById('banner-market-done');
    const textEl   = document.getElementById('banner-market-text');
    textEl.textContent = `✅ ${label} 분석 완료 — 가격 분석을 진행하세요.`;
    bannerEl.classList.remove('hidden');

    // 드롭다운 업데이트
    const now = new Date().toLocaleString('ko-KR', { month:'numeric', day:'numeric', hour:'numeric', minute:'numeric' });
    const optLabel = `시장조사 보고서 · ${label} · ${now}`;
    addOptionToSelect('sel-pricing-report', d.session_id, optLabel);
    addOptionToSelect('sel-partner-report', d.session_id, optLabel);

    // AI 가격 분석 활성화
    document.getElementById('btn-ai-price').disabled = false;

    // 보고서 탭
    addReport({ id: d.report_id || (d.session_id + '-market'), type: 'market', title: `시장조사 · ${label}` });

  } catch (e) {
    alert('분석 실행 오류: ' + e.message);
  } finally {
    setBtnLoading('btn-analyze', false, '▶ 분석 실행');
  }
}

// ── 신약 직접 분석 ────────────────────────────────────────────────────────────
function toggleCustomDrug() {
  document.getElementById('custom-drug-form').classList.toggle('hidden');
}

async function runCustomAnalyze() {
  const trade = document.getElementById('custom-trade').value.trim();
  const inn   = document.getElementById('custom-inn').value.trim();
  if (!trade || !inn) { alert('제품명과 INN을 입력하세요.'); return; }

  try {
    const r = await fetch('/api/za/report/session/init', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_key: 'custom', product_id: null, product_label: trade, inn_name: inn }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || '서버 오류');

    state.currentSessionId = d.session_id;
    state.marketDone = true;

    const bannerEl = document.getElementById('banner-market-done');
    const textEl   = document.getElementById('banner-market-text');
    textEl.textContent = `✅ ${trade} 분석 완료 — 가격 분석을 진행하세요.`;
    bannerEl.classList.remove('hidden');

    const now = new Date().toLocaleString('ko-KR', { month:'numeric', day:'numeric', hour:'numeric', minute:'numeric' });
    const optLabel = `시장조사 보고서 · ${trade} · ${now}`;
    addOptionToSelect('sel-pricing-report', d.session_id, optLabel);
    addOptionToSelect('sel-partner-report', d.session_id, optLabel);
    document.getElementById('btn-ai-price').disabled = false;
    addReport({ id: d.report_id || (d.session_id + '-market'), type: 'market', title: `시장조사 · ${trade}` });
    document.getElementById('custom-drug-form').classList.add('hidden');
  } catch (e) {
    alert('오류: ' + e.message);
  }
}

// ── 공공/민간 탭 ───────────────────────────────────────────────────────────────
function setMarketTab(tab) {
  state.marketTab = tab;
  const pub  = document.getElementById('tab-public');
  const priv = document.getElementById('tab-private');
  if (tab === 'public') {
    pub.className  = 'tab-active px-4 py-1.5 rounded-lg text-sm font-semibold transition-all';
    priv.className = 'tab-inactive px-4 py-1.5 rounded-lg text-sm font-semibold transition-all';
  } else {
    pub.className  = 'tab-inactive px-4 py-1.5 rounded-lg text-sm font-semibold transition-all';
    priv.className = 'tab-active px-4 py-1.5 rounded-lg text-sm font-semibold transition-all';
  }
  updateMarketTabDesc();
}

function updateMarketTabDesc() {
  const el = document.getElementById('market-tab-desc');
  if (!el) return;
  el.textContent = state.marketTab === 'public'
    ? '공공 시장: MHPL·EML 등재 채널 · NDoH 조달 기준'
    : '민간 시장: 병원·약국·체인 채널 중심 유통 구조 기준';
}

// ── 2단계: AI 가격 분석 실행 ─────────────────────────────────────────────────
async function runAiPricing() {
  if (!state.currentSessionId) { alert('먼저 분석 실행을 완료하세요.'); return; }

  const btn = document.getElementById('btn-ai-price');
  btn.disabled = true;
  btn.textContent = '분석 중...';

  try {
    const r = await fetch('/api/za/report/pricing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: state.currentSessionId, product_key: state.currentProductKey }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || '서버 오류');

    fillPriceCards(d.scenarios);
    document.getElementById('price-cards').classList.remove('hidden');
    document.getElementById('banner-price-done').classList.remove('hidden');
    state.pricingDone = true;

    const label = PRODUCT_LABEL[state.currentProductKey] || '제품';
    addReport({ id: d.report_id_public  || (state.currentSessionId + '-pub'),  type: 'pricing_public',  title: `수출가격 전략 [공공] · ${label}` });
    addReport({ id: d.report_id_private || (state.currentSessionId + '-priv'), type: 'pricing_private', title: `수출가격 전략 [민간] · ${label}` });

    document.getElementById('btn-partner').disabled = false;

  } catch (e) {
    alert('가격 분석 오류: ' + e.message);
  } finally {
    btn.textContent = '▶ AI 가격 분석 실행';
    btn.disabled = false;
  }
}

function fillPriceCards(scenarios) {
  if (!scenarios) return;
  const keys = ['conservative', 'baseline', 'premium'];
  keys.forEach(k => {
    const s = scenarios[k];
    if (!s) return;
    const usd = parseFloat(s.fob_usd ?? s.usd ?? 0);
    const zar = parseFloat(s.zar ?? (usd / state.zarUsd));
    document.getElementById(`price-${k}-usd`).textContent = usd.toFixed(2);
    document.getElementById(`price-${k}-zar`).textContent = `R ${zar.toFixed(2)}`;
    state.prices[k] = { usd, zar };
  });
}

// ── 가격 카드 편집 ────────────────────────────────────────────────────────────
function editCard(scenario) {
  state.editingScenario = scenario;
  const names = { conservative: '저가 진입', baseline: '기준가', premium: '프리미엄' };
  document.getElementById('edit-modal-title').textContent = `${names[scenario]} 수동 편집`;
  document.getElementById('edit-usd').value = state.prices[scenario]?.usd || '';
  document.getElementById('edit-zar').value = state.prices[scenario]?.zar || '';
  document.getElementById('edit-modal').classList.remove('hidden');
}

function saveEdit() {
  const s = state.editingScenario;
  if (!s) return;
  const usd = parseFloat(document.getElementById('edit-usd').value) || 0;
  const zar = parseFloat(document.getElementById('edit-zar').value) || (usd / state.zarUsd);
  state.prices[s] = { usd, zar };
  document.getElementById(`price-${s}-usd`).textContent = usd.toFixed(2);
  document.getElementById(`price-${s}-zar`).textContent = `R ${zar.toFixed(2)}`;
  closeEdit();
}

function closeEdit() {
  document.getElementById('edit-modal').classList.add('hidden');
  state.editingScenario = null;
}

// ── 3단계: 바이어 발굴 실행 ──────────────────────────────────────────────────
async function runPartner() {
  if (!state.currentSessionId) { alert('먼저 가격 분석을 완료하세요.'); return; }

  setBtnLoading('btn-partner', true, '▶ 바이어 발굴 실행');

  const criteria = [];
  if (document.getElementById('crit-1').checked) criteria.push('매출규모');
  if (document.getElementById('crit-2').checked) criteria.push('파이프라인');
  if (document.getElementById('crit-3').checked) criteria.push('제조소');
  if (document.getElementById('crit-4').checked) criteria.push('수입경험');
  if (document.getElementById('crit-5').checked) criteria.push('약국체인');

  try {
    const r = await fetch('/api/za/report/partner', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: state.currentSessionId,
        product_key: state.currentProductKey,
        criteria,
      }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || '서버 오류');

    renderTop10(d.top10 || []);
    state.partnerDone = true;

    const label = PRODUCT_LABEL[state.currentProductKey] || '제품';
    addReport({ id: d.report_id || (state.currentSessionId + '-partner'), type: 'partner', title: `바이어 발굴 · ${label}` });

    document.getElementById('btn-download-final').disabled = false;

    // 3초 후 최종 보고서 자동 등장 (after() 비동기)
    setTimeout(() => {
      addReport({ id: (d.combined_id || state.currentSessionId + '-final'), type: 'combined', title: `[최종] 최종 보고서 · ${label}` });
    }, 3000);

  } catch (e) {
    alert('바이어 발굴 오류: ' + e.message);
  } finally {
    setBtnLoading('btn-partner', false, '▶ 바이어 발굴 실행');
  }
}

function renderTop10(list) {
  const container = document.getElementById('top10-list');
  if (!list.length) {
    container.innerHTML = '<div class="text-sm text-gray-400 text-center py-4">결과가 없습니다.</div>';
    return;
  }
  container.innerHTML = list.map((item, i) => `
    <div class="flex items-center gap-3 px-3 py-2.5 rounded-lg border border-gray-100 hover:bg-gray-50 transition-colors">
      <span class="text-sm font-bold text-blue-900 w-5 text-center flex-shrink-0">${i + 1}</span>
      <span class="text-sm text-gray-800">${item.name || item}</span>
    </div>
  `).join('');
}

// ── 최종 보고서 다운로드 ──────────────────────────────────────────────────────
function downloadFinal() {
  if (!state.currentSessionId) return;
  window.open(`/api/za/report/combined?session_id=${state.currentSessionId}`, '_blank');
}

// ── 평가 기준 전체 해제 ───────────────────────────────────────────────────────
function clearCriteria() {
  ['crit-1','crit-2','crit-3','crit-4','crit-5'].forEach(id => {
    document.getElementById(id).checked = false;
  });
}

// ── 보고서 탭 패널 ────────────────────────────────────────────────────────────
function toggleReportPanel() {
  document.getElementById('report-panel').classList.toggle('hidden');
}

function addReport({ id, type, title }) {
  if (state.reports.find(r => r.id === id)) return;
  const now = new Date().toLocaleString('ko-KR', { month:'numeric', day:'numeric', hour:'numeric', minute:'numeric' });
  state.reports.unshift({ id, type, title, createdAt: now });
  renderReportList();
  updateReportBadge();
}

const BADGE_CLASS = {
  combined:        'report-badge-final',
  pricing_public:  'report-badge-public',
  pricing_private: 'report-badge-private',
  market:          'report-badge-market',
  partner:         'report-badge-partner',
};
const BADGE_LABEL = {
  combined: '최종', pricing_public: '공공', pricing_private: '민간', market: '조사', partner: '바이어',
};

function renderReportList() {
  const container = document.getElementById('report-list');
  if (!state.reports.length) {
    container.innerHTML = `<div class="text-sm text-gray-400 text-center py-6">아직 생성된 보고서가 없습니다.<br>만들어진 보고서는 자동으로 등록됩니다.</div>`;
    return;
  }
  container.innerHTML = state.reports.map(r => `
    <div class="flex items-center gap-2 p-2 rounded-lg border border-gray-100 hover:bg-gray-50">
      <span class="text-xs px-1.5 py-0.5 rounded font-bold flex-shrink-0 ${BADGE_CLASS[r.type] || 'report-badge-market'}">
        ${BADGE_LABEL[r.type] || '보고서'}
      </span>
      <div class="flex-1 min-w-0">
        <div class="text-xs font-medium text-gray-800 truncate">${r.title}</div>
        <div class="text-xs text-gray-400">${r.createdAt}</div>
      </div>
      <div class="flex items-center gap-1 flex-shrink-0">
        <button onclick="downloadReport('${r.id}','${r.type}')" class="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded hover:bg-gray-200">PDF</button>
        <button onclick="removeReport('${r.id}')" class="text-gray-300 hover:text-red-500 ml-1">✕</button>
      </div>
    </div>
  `).join('');
}

function updateReportBadge() {
  const badge = document.getElementById('report-count-badge');
  const n = state.reports.length;
  badge.textContent = n;
  n > 0 ? badge.classList.remove('hidden') : badge.classList.add('hidden');
}

function removeReport(id) {
  state.reports = state.reports.filter(r => r.id !== id);
  renderReportList();
  updateReportBadge();
}

function clearAllReports() {
  if (!state.reports.length) return;
  if (!confirm('모든 보고서를 목록에서 제거할까요?')) return;
  state.reports = [];
  renderReportList();
  updateReportBadge();
}

function downloadReport(id, type) {
  window.open(`/api/za/report/${type}/${id}/pdf`, '_blank');
}

// ── 보고서 폴링 (2초마다) ─────────────────────────────────────────────────────
function startReportPolling() {
  if (state.reportPollTimer) clearInterval(state.reportPollTimer);
  state.reportPollTimer = setInterval(async () => {
    if (!state.currentSessionId) return;
    try {
      const r = await fetch(`/api/za/report/session/${state.currentSessionId}/list`);
      if (!r.ok) return;
      const d = await r.json();
      (d.reports || []).forEach(rep => addReport({ id: rep.id, type: rep.report_type, title: rep.title }));
    } catch (_) {}
  }, 2000);
}

// ── 유틸 ─────────────────────────────────────────────────────────────────────
function addOptionToSelect(selId, value, label) {
  const sel = document.getElementById(selId);
  if (!sel) return;
  const opt = document.createElement('option');
  opt.value = value;
  opt.textContent = label;
  sel.appendChild(opt);
  sel.value = value;
}

function setBtnLoading(btnId, loading, defaultText) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  btn.disabled = loading;
  if (!loading) btn.textContent = defaultText;
}
