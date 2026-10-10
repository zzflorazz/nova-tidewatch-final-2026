// TideWatch Web 监测台主逻辑（D1）
// 数据来源：MQTT tidewatch/+/data（实时） + http://localhost:8000/api/state（初始快照）
// 所有数据更新来自 MQTT 回调，不做轮询

const REACH_IDS = ['reach-a', 'reach-b', 'reach-c'];
const REACH_POS = { 'reach-a': '上游', 'reach-b': '中游', 'reach-c': '下游' };
const STATE_API = 'http://localhost:8000/api/state';

// ===================== D2 优先关注状态 =====================
const anomalyCount = { 'reach-a': 0, 'reach-b': 0, 'reach-c': 0 };        // 连续异常次数
const latestData = { 'reach-a': null, 'reach-b': null, 'reach-c': null }; // 每断面最新 {status, flowLevel}

// ===================== D3 事件状态 =====================
const EVENTS_API = 'http://localhost:8001/api/events';
const EVENT_TOPIC_TEMPLATE = 'tidewatch/events/{reachId}';
const EVENT_STATES = {
  OPEN:      { color: '#f5222d', label: '待处理' },
  HANDLING:  { color: '#faad14', label: '处理中' },
  RECOVERED: { color: '#52c41a', label: '已恢复' },
  CLOSED:    { color: '#6b7280', label: '已关闭' },
};
const events = {};   // eventId -> 事件对象（来自 event_manager 的权威广播）
const lastSeq = {};  // eventId -> 已处理的最大 seq（事件消息去重）

// ===================== 辅助函数 =====================

/** 水流等级 → 中文描述（优先使用 rules.js 的 FLOW_LEVELS） */
function getFlowText(flowLevel) {
  const fallback = { 0: '缓流', 1: '平稳', 2: '湍急', 3: '湍流' };
  const map = (typeof FLOW_LEVELS !== 'undefined') ? FLOW_LEVELS : fallback;
  return map[flowLevel] || '未知';
}

/** 状态 → 颜色（优先使用 rules.js 的 STATUS_COLORS） */
function getStatusColor(status) {
  const fallback = {
    '正常': '#52c41a',
    '高水位警戒': '#faad14',
    '洪水预警': '#f5222d',
    '水质浑浊': '#13c2c2',
  };
  const map = (typeof STATUS_COLORS !== 'undefined') ? STATUS_COLORS : fallback;
  return map[status] || '#6b7280';
}

/** 用统一判级规则计算期望状态（兼容 dict/str 返回） */
function expectedStatus(data) {
  const result = judge(data.waterLevel, data.turbidity);
  return (result && result.status) ? result.status : result;
}

/** 更新卡片在线指示 */
function setOnline(card, online) {
  const dot = card.querySelector('.dot');
  const text = card.querySelector('.online-text');
  if (dot) dot.classList.toggle('off', !online);
  if (text) text.textContent = online ? '在线' : '离线';
}

/** 渲染单个断面卡片（严格按 reachId 定位 DOM，保证不串线） */
function renderCard(reachId, data, opts) {
  opts = opts || {};
  if (!REACH_IDS.includes(reachId)) {
    console.warn('[app] 忽略未知断面:', reachId);
    return;
  }
  const card = document.getElementById('card-' + reachId);
  if (!card) {
    console.warn('[app] 未找到卡片元素: card-' + reachId);
    return;
  }

  const set = (field, text) => {
    const el = card.querySelector(`[data-field="${field}"]`);
    if (el) el.textContent = text;
  };

  set('waterLevel', data.waterLevel);
  set('turbidity', data.turbidity);
  set('flowLevel', `${data.flowLevel} · ${getFlowText(data.flowLevel)}`);
  set('time', data.time || '—');

  // status 用统一规则二次校验；不一致时以规则结果为准
  let status = data.status;
  const expected = expectedStatus(data);
  if (status !== expected) {
    console.warn(`[app] ${reachId} status 不一致: 消息="${status}"，规则="${expected}"，以规则结果为准`);
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

/** 顶部"最后更新"时间（本地时间，反映链路活性） */
function updateLastUpdate() {
  const el = document.getElementById('last-update');
  if (el) el.textContent = new Date().toLocaleTimeString('zh-CN', { hour12: false });
}

// ===================== D2 优先关注 =====================

/** 处理一条断面数据：渲染卡片 + 更新连续异常计数（返回校验后的 status） */
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

/** 生成优先关注理由文本，如：环境:洪水预警(8) + 水流:湍急(2) + 连续异常:3次(1.5) = 11.5 */
function getPriorityReason(status, flowLevel, anomalyCountValue, score) {
  const weight = (typeof STATUS_WEIGHTS !== 'undefined' && STATUS_WEIGHTS[status]) || 0;
  const parts = [`环境:${status}(${weight * 2})`];
  if (flowLevel) parts.push(`水流:${getFlowText(flowLevel)}(${flowLevel})`);
  if (anomalyCountValue) parts.push(`连续异常:${anomalyCountValue}次(${anomalyCountValue * 0.5})`);
  return parts.join(' + ') + ` = ${score}`;
}

/** 重算优先度最高的断面，更新面板与卡片高亮（同分取 REACH_IDS 中靠前者；不写死任何断面） */
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

// ===================== MQTT =====================

function onMessage(data) {
  if (!data || !REACH_IDS.includes(data.reachId)) {
    console.warn('[app] 忽略未知断面消息:', data && data.reachId);
    return;
  }
  handleReachData(data.reachId, data, { online: true });
  updateLastUpdate();
  updatePriority();
  console.log(`[app] ${data.reachId} 卡片已更新:`, data);
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

// ===================== 初始状态快照 =====================

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
    if (n > 0) {
      updateLastUpdate();
      updatePriority();
    }
    console.log(`[app] 初始状态已加载（${n}/${REACH_IDS.length} 个断面）`);
  } catch (e) {
    console.warn('[app] 初始状态拉取失败（shared_state 未启动？），等待 MQTT 实时数据：', e.message);
  }
}

// ===================== D3 事件管理 =====================

const fmt = (v) => (v === undefined || v === null) ? '—' : v;

/** 当前时间字符串（与后端格式一致） */
function nowStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
         `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** 收到事件广播（只有 event_manager 的权威广播带 seq+state；指令回显忽略） */
function onEventMessage(data) {
  if (!data || !data.eventId || !data.reachId) {
    console.warn('[app] 非法事件消息:', data);
    return;
  }
  if (data.seq === undefined || data.state === undefined) {
    console.log('[app] 忽略事件指令回显:', data.action, data.eventId);
    return;
  }
  if (lastSeq[data.eventId] !== undefined && data.seq <= lastSeq[data.eventId]) {
    console.warn(`[app] 丢弃过期/重复事件广播 ${data.eventId} seq=${data.seq} <= ${lastSeq[data.eventId]}`);
    return;
  }
  lastSeq[data.eventId] = data.seq;
  events[data.eventId] = Object.assign(events[data.eventId] || {}, data);
  renderEventList();
  console.log(`[app] 事件更新 ${data.eventId} → ${data.state} (seq=${data.seq})`);
}

/** 通过 MQTT 发布事件操作指令（状态转移由 event_manager 权威执行） */
function sendEventAction(ev, action) {
  const topic = EVENT_TOPIC_TEMPLATE.replace('{reachId}', ev.reachId);
  const msg = {
    eventId: ev.eventId,
    reachId: ev.reachId,
    action: action,
    operator: 'web-user',
    time: nowStr(),
    timestamp: Date.now(),
  };
  TideWatchMQTT.publishTopic(topic, msg);
}

/** 单个事件卡片 HTML（按状态显示不同按钮） */
function eventCardHtml(ev) {
  const meta = EVENT_STATES[ev.state] || EVENT_STATES.CLOSED;
  const times = [`创建 ${ev.open_time || '—'}`];
  if (ev.handling_time) times.push(`处理 ${ev.handling_time}`);
  if (ev.recovered_time) times.push(`恢复 ${ev.recovered_time}`);
  if (ev.closed_time) times.push(`关闭 ${ev.closed_time}`);

  let buttons = '';
  if (ev.state === 'OPEN') {
    buttons = '<button class="ev-btn handle" data-action="handling">开始处理</button>';
  } else if (ev.state === 'HANDLING') {
    buttons = '<button class="ev-btn recover" data-action="recovered">验证恢复</button>' +
              '<button class="ev-btn close" data-action="closed">关闭事件</button>';
  }

  const flow = (ev.flowLevel === undefined || ev.flowLevel === null)
    ? '—' : `${ev.flowLevel} ${getFlowText(ev.flowLevel)}`;

  return `
    <div class="event-card" id="ev-card-${ev.eventId}" data-state="${ev.state}">
      <div class="event-head">
        <span class="event-id">${ev.eventId}</span>
        <span class="event-reach">${ev.reachId}（${REACH_POS[ev.reachId] || '未知'}）</span>
        <span class="event-badge" style="background:${meta.color}">${ev.state} · ${meta.label}</span>
      </div>
      <div class="event-data">水位 ${fmt(ev.waterLevel)} 米 · 浊度 ${fmt(ev.turbidity)} NTU · 水流 ${flow} · ${ev.status || '—'}</div>
      <div class="event-timeline">${times.join(' ｜ ')}</div>
      ${buttons ? `<div class="event-actions">${buttons}</div>` : ''}
    </div>`;
}

/** 渲染事件列表：活动事件在前，其余按创建时间倒序 */
function renderEventList() {
  const listEl = document.getElementById('event-list');
  if (!listEl) return;
  const rank = (ev) => (ev.state === 'OPEN' || ev.state === 'HANDLING') ? 0 : 1;
  const list = Object.values(events).sort((a, b) => {
    if (rank(a) !== rank(b)) return rank(a) - rank(b);
    return (b.open_time || '').localeCompare(a.open_time || '');
  });
  if (!list.length) {
    listEl.innerHTML = '<div class="event-empty">暂无事件</div>';
    return;
  }
  listEl.innerHTML = list.map(eventCardHtml).join('');
  list.forEach((ev) => {
    const card = document.getElementById('ev-card-' + ev.eventId);
    if (!card) return;
    card.querySelectorAll('button[data-action]').forEach((btn) => {
      btn.addEventListener('click', () => {
        sendEventAction(ev, btn.dataset.action);
        btn.disabled = true;  // 防重复点击，等待权威广播刷新
        setTimeout(() => { btn.disabled = false; }, 2000);
      });
    });
  });
}

/** 页面加载时从 event_manager API 拉一次初始事件列表 */
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
    console.log(`[app] 初始事件已加载（${Object.keys(events).length} 个）`);
  } catch (e) {
    console.warn('[app] 初始事件拉取失败（event_manager 未启动？）：', e.message);
  }
}

// ===================== 启动 =====================

document.addEventListener('DOMContentLoaded', () => {
  TideWatchMQTT.onConnectionChange(setConnStatus);
  TideWatchMQTT.connect();
  TideWatchMQTT.subscribe(onMessage);
  TideWatchMQTT.subscribeTopic('tidewatch/events/+', onEventMessage);
  fetchInitialState();
  fetchInitialEvents();
});
