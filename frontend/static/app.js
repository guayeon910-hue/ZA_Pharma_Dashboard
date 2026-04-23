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
let map = null;

window.addEventListener('DOMContentLoaded', () => {
  loadExchangeRate();
  startReportPolling();
  updateMarketTabDesc();
  initMap();
});

function goTab(tabId) {
  const tabPreview = document.getElementById('tab-preview');
  const tabMain = document.getElementById('tab-main');
  const navPreview = document.getElementById('nav-preview');
  const navMain = document.getElementById('nav-main');

  if (tabId === 'preview') {
    tabPreview.classList.remove('hidden');
    tabPreview.classList.add('block');
    tabMain.classList.remove('block');
    tabMain.classList.add('hidden');

    navPreview.classList.add('text-blue-900', 'border-blue-900');
    navPreview.classList.remove('text-gray-500', 'border-transparent');
    navMain.classList.remove('text-blue-900', 'border-blue-900');
    navMain.classList.add('text-gray-500', 'border-transparent');
    
    // Invalidate map size so Leaflet redraws it correctly when tab becomes visible
    setTimeout(() => { if (map) map.invalidateSize(); }, 100);
  } else {
    tabMain.classList.remove('hidden');
    tabMain.classList.add('block');
    tabPreview.classList.remove('block');
    tabPreview.classList.add('hidden');

    navMain.classList.add('text-blue-900', 'border-blue-900');
    navMain.classList.remove('text-gray-500', 'border-transparent');
    navPreview.classList.remove('text-blue-900', 'border-blue-900');
    navPreview.classList.add('text-gray-500', 'border-transparent');
  }
}

function initMap() {
  const mapContainer = document.getElementById('sa-map');
  if (!mapContainer) return;
  
  // Center of South Africa roughly
  map = L.map('sa-map').setView([-30.5595, 22.9375], 5);
  
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18,
    attribution: '© OpenStreetMap contributors'
  }).addTo(map);

  L.marker([-25.7479, 28.2293]).addTo(map)
    .bindPopup('<b>South Africa</b>')
    .openPopup();
}

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

  const bannerEl = document.getElementById('banner-market-done');
  if (bannerEl) bannerEl.classList.add('hidden');
  
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

// ── 가격 카드 편집 (Advanced) ──────────────────────────────────────────────────
function getDefaultEditOptions() {
  return [
    { id: 'opt_1', name: '에이전트 수수료', type: 'deduct', value: 15, checked: true },
    { id: 'opt_2', name: '물류/통관비', type: 'deduct', value: 5, checked: true },
    { id: 'opt_3', name: '조달청 입찰 수수료', type: 'deduct', value: 3, checked: true },
    { id: 'opt_4', name: '환율 리스크 헤지', type: 'deduct', value: 2, checked: true },
  ];
}

function editCard(scenario) {
  state.editingScenario = scenario;
  const names = { conservative: '저가 진입', baseline: '기준가', premium: '프리미엄' };
  const marketName = state.marketTab === 'public' ? '공공 시장' : '민간 시장';
  document.getElementById('edit-modal-title').textContent = `${names[scenario]} — 역산·옵션 편집 [${marketName}]`;
  
  const usd = state.prices[scenario]?.usd || 0;
  const zar = state.prices[scenario]?.zar || 0;
  document.getElementById('edit-modal-report-price').textContent = `${zar.toFixed(2)} ZAR ≈ ${usd.toFixed(2)} USD`;
  
  document.getElementById('edit-usd').value = usd.toFixed(2);
  
  if (!state.editOptions) {
    state.editOptions = getDefaultEditOptions();
  }
  renderEditOptions();
  recalcEdit();
  document.getElementById('edit-modal').classList.remove('hidden');
}

function resetEditOptions() {
  state.editOptions = getDefaultEditOptions();
  renderEditOptions();
  recalcEdit();
}

function renderEditOptions() {
  const container = document.getElementById('edit-options-list');
  container.innerHTML = state.editOptions.map(opt => `
    <div class="flex items-center justify-between text-sm py-1 border-b border-gray-50 last:border-0">
      <div class="flex items-center gap-2">
        <input type="checkbox" ${opt.checked ? 'checked' : ''} onchange="toggleEditOption('${opt.id}')" class="w-3.5 h-3.5 rounded border-gray-300 text-blue-600">
        <span class="${opt.checked ? 'text-gray-700' : 'text-gray-400'}">${opt.name}</span>
      </div>
      <div class="flex items-center gap-2">
        <span class="text-xs text-gray-500 w-12 text-right">${opt.type === 'deduct' ? '% 차감' : '× 배수'}</span>
        <span class="font-mono text-gray-700 bg-white border border-gray-200 px-2 py-0.5 rounded w-12 text-right">${opt.value}</span>
        <button onclick="removeEditOption('${opt.id}')" class="text-red-400 hover:text-red-600 text-xs px-1">✕</button>
      </div>
    </div>
  `).join('');
}

function toggleEditOption(id) {
  const opt = state.editOptions.find(o => o.id === id);
  if (opt) opt.checked = !opt.checked;
  renderEditOptions();
  recalcEdit();
}

function removeEditOption(id) {
  state.editOptions = state.editOptions.filter(o => o.id !== id);
  renderEditOptions();
  recalcEdit();
}

function addEditOption() {
  const name = document.getElementById('new-opt-name').value.trim();
  const type = document.getElementById('new-opt-type').value;
  const val = parseFloat(document.getElementById('new-opt-val').value);
  if (!name || isNaN(val)) { alert('옵션명과 값을 정확히 입력하세요.'); return; }
  
  state.editOptions.push({
    id: 'opt_' + Date.now(),
    name, type, value: val, checked: true
  });
  
  document.getElementById('new-opt-name').value = '';
  document.getElementById('new-opt-val').value = '';
  
  renderEditOptions();
  recalcEdit();
}

function recalcEdit() {
  let baseUsd = parseFloat(document.getElementById('edit-usd').value) || 0;
  
  let finalUsd = baseUsd;
  let deductPct = 0;
  
  state.editOptions.forEach(opt => {
    if (!opt.checked) return;
    if (opt.type === 'deduct') {
      deductPct += opt.value;
    } else if (opt.type === 'multiply') {
      finalUsd *= opt.value;
    }
  });
  
  if (deductPct > 0) {
    finalUsd = finalUsd * (1 - deductPct / 100);
  }
  
  const finalZar = finalUsd * state.zarUsd;
  document.getElementById('edit-result').textContent = `${finalUsd.toFixed(2)} USD · ${finalZar.toFixed(2)} ZAR`;
  return { finalUsd, finalZar };
}

function saveEdit() {
  const s = state.editingScenario;
  if (!s) return;
  const { finalUsd, finalZar } = recalcEdit();
  
  state.prices[s] = { usd: finalUsd, zar: finalZar };
  document.getElementById(`price-${s}-usd`).textContent = finalUsd.toFixed(2);
  document.getElementById(`price-${s}-zar`).textContent = `R ${finalZar.toFixed(2)}`;
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
  state.top10 = list;
  const container = document.getElementById('top10-list');
  if (!list.length) {
    container.innerHTML = '<div class="text-sm text-gray-400 text-center py-4">결과가 없습니다.</div>';
    return;
  }
  container.innerHTML = list.map((item, i) => `
    <div onclick="openBuyerModal(${i})" class="flex items-center gap-3 px-3 py-2.5 rounded-lg border border-gray-100 hover:bg-gray-50 transition-colors cursor-pointer">
      <span class="text-sm font-bold text-blue-900 w-5 text-center flex-shrink-0">${item.rank || (i + 1)}</span>
      <span class="text-sm font-medium text-gray-800">${item.name || item}</span>
    </div>
  `).join('');
}

function openBuyerModal(i) {
  const buyer = state.top10[i];
  if (!buyer) return;
  document.getElementById('buyer-modal-rank').textContent = buyer.rank || (i + 1);
  document.getElementById('buyer-modal-name').textContent = buyer.name || '';
  document.getElementById('buyer-modal-overview').textContent = buyer.overview || '상세 정보가 제공되지 않았습니다.';
  document.getElementById('buyer-modal-reason').textContent = buyer.reason || '채택 사유가 제공되지 않았습니다.';
  document.getElementById('buyer-modal-address').textContent = buyer.address || '-';
  document.getElementById('buyer-modal-phone').textContent = buyer.phone || '-';
  document.getElementById('buyer-modal-email').textContent = buyer.email || '-';
  document.getElementById('buyer-modal-website').textContent = buyer.website || '-';
  document.getElementById('buyer-modal-booth').textContent = buyer.booth || '-';
  document.getElementById('buyer-modal-scale').textContent = buyer.scale || '-';
  document.getElementById('buyer-modal-region').textContent = buyer.region || '-';
  document.getElementById('buyer-modal').classList.remove('hidden');
}

function closeBuyerModal() {
  document.getElementById('buyer-modal').classList.add('hidden');
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
  if (btnId === 'btn-analyze') {
    const loadingEl = document.getElementById('market-loading');
    if (loading) {
      btn.innerHTML = '⏳ 시장 조사';
      btn.classList.add('bg-slate-400', 'hover:bg-slate-400');
      btn.classList.remove('bg-blue-900', 'hover:bg-blue-800');
      if (loadingEl) loadingEl.classList.remove('hidden');
    } else {
      btn.innerHTML = defaultText;
      btn.classList.remove('bg-slate-400', 'hover:bg-slate-400');
      btn.classList.add('bg-blue-900', 'hover:bg-blue-800');
      if (loadingEl) loadingEl.classList.add('hidden');
    }
  } else if (btnId === 'btn-partner') {
    const loadingEl = document.getElementById('buyer-loading-indicator');
    const spacerEl = document.getElementById('buyer-loading-spacer');
    const listEl = document.getElementById('top10-list');
    
    if (loading) {
      btn.innerHTML = '... 바이어 발굴';
      btn.classList.add('bg-slate-400', 'hover:bg-slate-400', 'cursor-not-allowed');
      btn.classList.remove('bg-blue-900', 'hover:bg-blue-800');
      if (loadingEl) loadingEl.classList.remove('hidden');
      if (spacerEl) spacerEl.classList.add('hidden');
      
      // Render 8 skeleton rows
      if (listEl) {
        listEl.innerHTML = Array(8).fill(0).map((_, i) => `
          <div class="flex items-center gap-3 px-3 py-2.5 rounded-lg border border-gray-100 mb-1 bg-white">
            <span class="text-sm font-bold text-gray-200 w-5 text-center flex-shrink-0">${i + 1}</span>
            <div class="h-4 bg-slate-200 rounded animate-pulse" style="width: ${90 - Math.random() * 30}%"></div>
          </div>
        `).join('');
      }
    } else {
      btn.innerHTML = defaultText;
      btn.classList.remove('bg-slate-400', 'hover:bg-slate-400', 'cursor-not-allowed');
      btn.classList.add('bg-blue-900', 'hover:bg-blue-800');
      if (loadingEl) loadingEl.classList.add('hidden');
      if (spacerEl) spacerEl.classList.remove('hidden');
    }
  } else {
    if (!loading) btn.textContent = defaultText;
  }
}
