// TideWatch 移动端 H5 主逻辑（E3）
// 复用 TideWatchMQTT（MQTT 共享实时链）与 rules.js（统一判级 / 优先度计算）
// 数据来源：MQTT tidewatch/+/data + tidewatch/events/+ ｜ 初始快照：/api/state、/api/events
// 与桌面端通过同一 MQTT 事件链天然同步，不需要额外同步逻辑

const REACH_IDS = ['reach-a', 'reach-b', 'reach-c'];
const REACH_POS = { 'reach-a': '上游', 'reach-b': '中游', 'reach-c': '下游' };
const STATE_API = 'http://localhost:8000/api/state';
const EVENTS_API = 'http://localhost:8001/api/events';
const EVENT_TOPIC_TEMPLATE = 'tidewatch/events/{reachId}';
const EVENT_STATES = {
  OPEN:      { color: '#f5222d', label: '待处理' },
  HANDLING:  { color: '#faad14', label: '处理中' },
  RECOVERED: { color: '#52c41a', label: '已恢复' },
  CLOSED:    { color: '#6b7280', label: '已关闭' },
};

const events = {};   // eventId -> 事件对象（event_manager 权威广播）
const lastSeq = {};  // eventId -> 已处理的最大 seq（事件消息去重）
const anomalyCount = { 'reach-a': 0, 'reach-b': 0, 'reach-c': 0 };
const latestData = { 'reach-a': null, 'reach-b': null, 'reach-c': null };

const fmt = (v) => (v === undefined || v === null) ? '—' : v;

// ===================== 辅助函数（与 rules.js 保持一致） =====================

function getFlowText(flowLevel) {
  const fallback = { 0: '缓流', 1: '平稳', 2: '湍急', 3: '湍流' };
  const map = (typeof FLOW_LEVELS !== 'undefined') ? FLOW_LEVELS : fallback;
  return map[flowLevel] || '未知';
}

function getStatusColor(status) {
  const fallback = {
    '正常': '#52c41a', '高水位警戒': '#faad14', '洪水预警': '#f5222d', '水质浑浊': '#13c2c2',
  };
  const map = (typeof STATUS_COLORS !== 'undefined') ? STATUS_COLORS : fallback;
  return map[status] || '#6b7280';
}

function expectedStatus(data) {
  const result = judge(data.waterLevel, data.turbidity);
  return (result && result.status) ? result.status : result;
}

function nowStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
         `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function setOnline(card, online) {
  const dot = card.querySelector('.dot');
  const text = card.querySelector('.online-text');
  if (dot) dot.classList.toggle('off', !online);
  if (text) text.textContent = online ? '在线' : '离线';
}

// ===================== 断面卡片渲染（同桌面端的不串线逻辑） =====================

function renderCard(reachId, data, opts) {
  opts = opts || {};
  if (!REACH_IDS.includes(reachId)) {
    console.warn('[mobile] 忽略未知断面:', reachId);
    return null;
  }
  const card = document.getElementById('card-' + reachId);
  if (!card) {
    console.warn('[mobile] 未找到卡片: card-' + reachId);
    return null;
  }

  const set = (field, text) => {
    const el = card.querySelector(`[data-field="${field}"]`);
    if (el) el.textContent = text;
  };

  set('waterLevel', data.waterLevel);
  set('turbidity', data.turbidity);
  set('flowLevel', getFlowText(data.flowLevel));
  set('time', data.time || '—');

  let status = data.status;
  const expected = expectedStatus(data);
  if (status !== expected) {
    console.warn(`[mobile] ${reachId} status 不一致: 消息="${status}"，规则="${expected}"，以规则为准`);
    status = expected;
  }
  const badge = card.querySelector('[data-field="status"]');
  if (badge) {
    badge.textContent = status;
    badge.style.background = getStatusColor(status);
  }
  card.style.setProperty('--c', getStatusColor(status));

  setOnline(card, opts.online === undefined ? true : !!opts.online);
  return status;
}

// ===================== 优先关注（复用 calcPriority） =====================

function handleReachData(reachId, data, opts) {
  const status = renderCard(reachId, data, opts);
  if (!status) return null;
  latestData[reachId] = { status: status, flowLevel: data.flowLevel };
  if (status !== '正常') {
    anomalyCount[reachId] += 1;
  } else {
    anomalyCount[reachId] = 0;
  }
  return status;
}

function getPriorityReason(status, flowLevel, anomalyCountValue, score) {
  const weight = (typeof STATUS_WEIGHTS !== 'undefined' && STATUS_WEIGHTS[status]) || 0;
  const parts = [`环境:${status}(${weight * 2})`];
  if (flowLevel) parts.push(`水流:${getFlowText(flowLevel)}(${flowLevel})`);
  if (anomalyCountValue) parts.push(`连续异常:${anomalyCountValue}次(${anomalyCountValue * 0.5})`);
  return parts.join(' + ') + ` = ${score}`;
}

function updatePriority() {
  let best = null;
  REACH_IDS.forEach((rid) => {
    const d = latestData[rid];
    if (!d) return;
    const score = calcPriority(d.status, d.flowLevel, anomalyCount[rid]);
    if (best === null || score > best.score) {
      best = { reachId: rid, status: d.status, flowLevel: d.flowLevel, count: anomalyCount[rid], score: score };
    }
  });
  if (!best) return;

  const reachEl = document.getElementById('priority-reach');
  const reasonEl = document.getElementById('priority-reason');
  const scoreEl = document.getElementById('priority-score');
  if (reachEl) reachEl.textContent = `${best.reachId}（${REACH_POS[best.reachId]}）`;
  if (reasonEl) reasonEl.textContent = getPriorityReason(best.status, best.flowLevel, best.count, best.score);
  if (scoreEl) scoreEl.textContent = best.score;

  REACH_IDS.forEach((rid) => {
    const card = document.getElementById('card-' + rid);
    if (card) card.classList.toggle('priority', rid === best.reachId);
  });
}

// ===================== 事件列表（移动端只看活动事件） =====================

function onEventMessage(data) {
  if (!data || !data.eventId || !data.reachId) {
    console.warn('[mobile] 非法事件消息:', data);
    return;
  }
  if (data.seq === undefined || data.state === undefined) {
    console.log('[mobile] 忽略事件指令回显:', data.action, data.eventId);
    return;
  }
  if (lastSeq[data.eventId] !== undefined && data.seq <= lastSeq[data.eventId]) {
    console.warn(`[mobile] 丢弃过期/重复事件广播 ${data.eventId} seq=${data.seq}`);
    return;
  }
  lastSeq[data.eventId] = data.seq;
  events[data.eventId] = Object.assign(events[data.eventId] || {}, data);
  renderEventList();
  console.log(`[mobile] 事件更新 ${data.eventId} → ${data.state} (seq=${data.seq})`);
}

function sendEventAction(ev, action) {
  const topic = EVENT_TOPIC_TEMPLATE.replace('{reachId}', ev.reachId);
  const msg = {
    eventId: ev.eventId,
    reachId: ev.reachId,
    action: action,
    operator: 'mobile-user',
    time: nowStr(),
    timestamp: Date.now(),
  };
  const actionLabels = { handling: '开始处理', recovered: '验证恢复', closed: '关闭事件' };
  if (TideWatchMQTT.publishTopic(topic, msg)) {
    showToast(`指令已发送：${actionLabels[action] || action}`);
  }
}

function eventCardHtml(ev) {
  const meta = EVENT_STATES[ev.state] || EVENT_STATES.CLOSED;
  const times = [`创建 ${ev.open_time || '—'}`];
  if (ev.handling_time) times.push(`处理 ${ev.handling_time}`);

  let buttons = '';
  if (ev.state === 'OPEN') {
    buttons = '<button class="ev-btn handle" data-action="handling">开始处理</button>';
  } else if (ev.state === 'HANDLING') {
    buttons = '<button class="ev-btn recover" data-action="recovered">验证恢复</button>' +
              '<button class="ev-btn close" data-action="closed">关闭事件</button>';
  }

  return `
    <div class="m-event" id="ev-card-${ev.eventId}" data-state="${ev.state}">
      <div class="m-event-head">
        <span class="event-id">${ev.eventId}</span>
        <span class="event-badge" style="background:${meta.color}">${ev.state} · ${meta.label}</span>
      </div>
      <div class="event-data">${ev.reachId}（${REACH_POS[ev.reachId] || '未知'}）· 水位 ${fmt(ev.waterLevel)} 米 · 浊度 ${fmt(ev.turbidity)} NTU · ${ev.status || '—'}</div>
      <div class="event-timeline">${times.join(' ｜ ')}</div>
      ${buttons ? `<div class="m-event-actions">${buttons}</div>` : ''}
    </div>`;
}

/** 移动端只显示 OPEN/HANDLING 活动事件 */
function renderEventList() {
  const listEl = document.getElementById('event-list');
  if (!listEl) return;
  const actives = Object.values(events)
    .filter((ev) => ev.state === 'OPEN' || ev.state === 'HANDLING')
    .sort((a, b) => (b.open_time || '').localeCompare(a.open_time || ''));
  if (!actives.length) {
    listEl.innerHTML = '<div class="event-empty">当前无待处理事件</div>';
    return;
  }
  listEl.innerHTML = actives.map(eventCardHtml).join('');
  actives.forEach((ev) => {
    const card = document.getElementById('ev-card-' + ev.eventId);
    if (!card) return;
    card.querySelectorAll('button[data-action]').forEach((btn) => {
      btn.addEventListener('click', () => {
        sendEventAction(ev, btn.dataset.action);
        btn.disabled = true;
        setTimeout(() => { btn.disabled = false; }, 2000);
      });
    });
  });
}

// ===================== MQTT 数据回调与连接状态 =====================

function onDataMessage(data) {
  if (!data || !REACH_IDS.includes(data.reachId)) {
    console.warn('[mobile] 忽略未知断面消息:', data && data.reachId);
    return;
  }
  handleReachData(data.reachId, data, { online: true });
  updatePriority();
  console.log(`[mobile] ${data.reachId} 卡片已更新:`, data);
}

function setConnStatus(connected) {
  const dot = document.getElementById('conn-dot');
  const text = document.getElementById('conn-text');
  const banner = document.getElementById('disconnect-banner');
  if (dot) {
    dot.classList.remove('connecting');
    dot.classList.toggle('off', !connected);
  }
  if (text) text.textContent = connected ? 'MQTT 已连接' : 'MQTT 未连接';
  if (banner) banner.classList.toggle('hidden', connected);
}

// ===================== Toast =====================

let toastTimer = null;
function showToast(msg) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2000);
}

// ===================== 初始快照 =====================

async function fetchInitialState() {
  try {
    const res = await fetch(STATE_API);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const state = await res.json();
    let n = 0;
    REACH_IDS.forEach((rid) => {
      if (state[rid]) {
        handleReachData(rid, state[rid], { online: !!state[rid].online });
        n++;
      }
    });
    if (n > 0) updatePriority();
    console.log(`[mobile] 初始状态已加载（${n}/${REACH_IDS.length} 个断面）`);
  } catch (e) {
    console.warn('[mobile] 初始状态拉取失败（shared_state 未启动？）：', e.message);
  }
}

async function fetchInitialEvents() {
  try {
    const res = await fetch(EVENTS_API);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    Object.values(data.events || {}).forEach((ev) => {
      events[ev.eventId] = ev;
      if (typeof ev.seq === 'number') {
        lastSeq[ev.eventId] = Math.max(lastSeq[ev.eventId] || 0, ev.seq);
      }
    });
    renderEventList();
    console.log(`[mobile] 初始事件已加载（${Object.keys(events).length} 个）`);
  } catch (e) {
    console.warn('[mobile] 初始事件拉取失败（event_manager 未启动？）：', e.message);
  }
}

// ===================== 启动 =====================

document.addEventListener('DOMContentLoaded', () => {
  TideWatchMQTT.onConnectionChange(setConnStatus);
  TideWatchMQTT.connect();
  TideWatchMQTT.subscribe(onDataMessage);
  TideWatchMQTT.subscribeTopic('tidewatch/events/+', onEventMessage);
  fetchInitialState();
  fetchInitialEvents();
});
