// TideWatch 多模态交互（E2）
// 全部使用浏览器原生 API：getUserMedia（摄像头）/ SpeechRecognition（ASR）/ speechSynthesis（TTS）
// 数据与事件通过 MQTT 共享链（tidewatch/+/data、tidewatch/events/+），语音指令发布到 tidewatch/events/{reachId}
// 复用 rules.js（判级/优先度）与 mqtt_client.js（TideWatchMQTT）

const REACH_IDS = ['reach-a', 'reach-b', 'reach-c'];
const REACH_POS = { 'reach-a': '上游', 'reach-b': '中游', 'reach-c': '下游' };
const STATE_API = 'http://localhost:8000/api/state';
const EVENTS_API = 'http://localhost:8001/api/events';
const EVENT_TOPIC_TEMPLATE = 'tidewatch/events/{reachId}';
const WATER_COLOR_CSS = {
  '正常': '#1890ff', '高水位警戒': '#faad14', '洪水预警': '#f5222d', '水质浑浊': '#13c2c2',
};

const latestData = { 'reach-a': null, 'reach-b': null, 'reach-c': null };
const anomalyCount = { 'reach-a': 0, 'reach-b': 0, 'reach-c': 0 };
const events = {};
const lastSeq = {};

let selectedReach = null;
let priorityInfo = null;

// ===================== 辅助 =====================

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

function nowStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
         `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

let toastTimer = null;
function showToast(msg) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2000);
}

// ===================== 断面卡片（同桌面端不串线逻辑） =====================

function renderCard(reachId, data) {
  const card = document.getElementById('card-' + reachId);
  if (!card) return null;
  const set = (field, text) => {
    const el = card.querySelector(`[data-field="${field}"]`);
    if (el) el.textContent = text;
  };
  set('waterLevel', data.waterLevel);
  set('turbidity', data.turbidity);
  set('flowLevel', `${data.flowLevel} · ${getFlowText(data.flowLevel)}`);
  set('time', data.time || '—');

  let status = data.status;
  const result = judge(data.waterLevel, data.turbidity);
  const expected = (result && result.status) ? result.status : result;
  if (status !== expected) {
    console.warn(`[multimodal] ${reachId} status 不一致: 消息="${status}"，规则="${expected}"，以规则为准`);
    status = expected;
  }
  const badge = card.querySelector('[data-field="status"]');
  if (badge) {
    badge.textContent = status;
    badge.style.background = getStatusColor(status);
  }
  card.style.setProperty('--c', getStatusColor(status));
  const dot = card.querySelector('.dot');
  const onlineText = card.querySelector('.online-text');
  if (dot) dot.classList.remove('off');
  if (onlineText) onlineText.textContent = '在线';
  return status;
}

function selectReach(reachId) {
  if (!REACH_IDS.includes(reachId)) return false;
  selectedReach = reachId;
  REACH_IDS.forEach((rid) => {
    const card = document.getElementById('card-' + rid);
    if (card) card.classList.toggle('selected', rid === reachId);
  });
  showToast(`已选中 ${reachId}（${REACH_POS[reachId]}）`);
  console.log(`[multimodal] 选中: ${reachId}`);
  return true;
}

// ===================== 优先关注（复用 calcPriority） =====================

function updatePriority() {
  let best = null;
  REACH_IDS.forEach((rid) => {
    const d = latestData[rid];
    if (!d) return;
    const score = calcPriority(d.status, d.flowLevel, anomalyCount[rid]);
    if (best === null || score > best.score) {
      best = { reachId: rid, score: score };
    }
  });
  priorityInfo = best;
}

// ===================== 摄像头（getUserMedia + Canvas 叠加层） =====================

let cameraReady = false;
let overlayInfo = { priority: null, borderColor: '#52c41a', text: '' };
let photoInfo = null;

async function initCamera() {
  const video = document.getElementById('camera');
  const statusEl = document.getElementById('cam-status');
  const placeholder = document.getElementById('cam-placeholder');

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    statusEl.textContent = '当前环境不支持摄像头（getUserMedia 不可用）';
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'user' }, width: { ideal: 640 }, height: { ideal: 360 } },
      audio: false,
    });
    video.srcObject = stream;
    await video.play().catch(() => {});
    cameraReady = true;
    placeholder.classList.add('hidden');
    statusEl.textContent = '✓ 摄像头已启动（画面边框随优先断面状态变色）';
    console.log('[multimodal] 摄像头已启动');
  } catch (e) {
    cameraReady = false;
    statusEl.textContent = `摄像头不可用（${e.name || e.message}），语音/事件功能不受影响`;
    console.warn('[multimodal] 摄像头启动失败:', e);
  }
}

function drawOverlay() {
  const canvas = document.getElementById('overlay');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  const d = priorityInfo ? latestData[priorityInfo.reachId] : null;
  const color = (d && WATER_COLOR_CSS[d.status]) || '#52c41a';

  // 异常时画面边框变色（洪水预警=红）
  ctx.lineWidth = 6;
  ctx.strokeStyle = color;
  ctx.strokeRect(3, 3, W - 6, H - 6);

  // 底部半透明信息条
  const text = d
    ? `🎯 优先关注 ${priorityInfo.reachId}（${REACH_POS[priorityInfo.reachId]}）｜ ${d.status} ｜ 水位 ${d.waterLevel} 米`
    : '等待 MQTT 数据…';
  ctx.fillStyle = 'rgba(10, 22, 40, 0.72)';
  ctx.fillRect(0, H - 48, W, 48);
  ctx.fillStyle = '#ffffff';
  ctx.font = 'bold 19px "PingFang SC", "Microsoft YaHei", sans-serif';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, 14, H - 24);

  // 右上时间
  ctx.font = '14px "PingFang SC", sans-serif';
  ctx.fillStyle = 'rgba(230,241,255,.85)';
  ctx.textAlign = 'right';
  ctx.fillText(nowStr(), W - 14, 20);
  ctx.textAlign = 'left';

  overlayInfo = {
    priority: priorityInfo ? priorityInfo.reachId : null,
    borderColor: color,
    text: text,
  };
}

function startOverlayLoop() {
  const loop = () => {
    drawOverlay();
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}

function capturePhoto() {
  const video = document.getElementById('camera');
  if (!cameraReady || !video.videoWidth) {
    showToast('摄像头未启动，无法拍照');
    return null;
  }
  const c = document.createElement('canvas');
  c.width = video.videoWidth;
  c.height = video.videoHeight;
  const ctx = c.getContext('2d');
  ctx.drawImage(video, 0, 0);

  // 水印：断面 / 时间 / 状态（与当前断面和事件关联）
  const rid = selectedReach || (priorityInfo && priorityInfo.reachId) || 'reach-?';
  const d = latestData[rid];
  const activeEv = currentEvent(['OPEN', 'HANDLING'], rid);
  const stamp = `${rid}（${REACH_POS[rid] || '?'}）· ${d ? d.status : '无数据'} · ${nowStr()}` +
                (activeEv ? ` · 事件 ${activeEv.eventId}` : '');
  ctx.fillStyle = 'rgba(10, 22, 40, 0.72)';
  ctx.fillRect(0, c.height - 46, c.width, 46);
  ctx.fillStyle = '#fff';
  ctx.font = 'bold 22px "PingFang SC", "Microsoft YaHei", sans-serif';
  ctx.textBaseline = 'middle';
  ctx.fillText(stamp, 14, c.height - 24);

  const dataUrl = c.toDataURL('image/png');
  const img = document.getElementById('photo-img');
  const meta = document.getElementById('photo-meta');
  const link = document.getElementById('photo-download');
  img.src = dataUrl;
  meta.textContent = `关联断面 ${rid} ｜ ${nowStr()} ｜ 状态 ${d ? d.status : '—'}` +
                     (activeEv ? ` ｜ 事件 ${activeEv.eventId}` : ' ｜ 无活动事件');
  link.href = dataUrl;
  link.download = `tidewatch_photo_${rid}_${Date.now()}.png`;
  document.getElementById('photo-box').classList.remove('hidden');

  photoInfo = { reachId: rid, eventId: activeEv ? activeEv.eventId : null, time: nowStr() };
  console.log('[multimodal] 拍照记录:', photoInfo);
  showToast('现场照片已保存（含断面/时间水印）');
  return photoInfo;
}

// ===================== TTS（speechSynthesis） =====================

let autoSpeak = true;
const speakLog = [];
const announcedFlood = { 'reach-a': null, 'reach-b': null, 'reach-c': null };

function speak(text) {
  speakLog.push({ text: text, time: nowStr() });
  console.log('[multimodal][TTS]', text);
  const logEl = document.getElementById('tts-log');
  if (logEl) logEl.textContent = '最近播报：' + text;
  try {
    if (window.speechSynthesis) {
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = 'zh-CN';
      u.rate = 1.05;
      window.speechSynthesis.speak(u);
    }
  } catch (e) {
    console.warn('[multimodal] TTS 播报失败:', e);
  }
}

function speakAllStatus() {
  const parts = REACH_IDS.map((rid) => {
    const d = latestData[rid];
    if (!d) return `${rid} 暂无数据`;
    return `${rid} ${REACH_POS[rid]}，${d.status}，水位 ${d.waterLevel} 米，浊度 ${d.turbidity}`;
  });
  speak('当前断面状态：' + parts.join('；'));
}

/** 洪水预警出现（正常→洪水预警 转变）时自动播报 */
function announceIfNeeded(reachId, d) {
  if (!autoSpeak) return;
  const prev = announcedFlood[reachId];
  if (d.status === '洪水预警' && prev !== '洪水预警') {
    speak(`${reachId} ${REACH_POS[reachId]} 洪水预警，水位 ${d.waterLevel} 米，请立即关注`);
    announcedFlood[reachId] = '洪水预警';
  } else if (d.status !== '洪水预警') {
    announcedFlood[reachId] = d.status;
  }
}

// ===================== ASR（SpeechRecognition） =====================

let srSupported = false;
let recognition = null;
let listening = false;

function initASR() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const btn = document.getElementById('asr-btn');
  const statusEl = document.getElementById('asr-status');
  srSupported = !!SR;
  if (!SR) {
    btn.disabled = true;
    statusEl.textContent = '当前浏览器不支持语音识别（建议使用 Chrome / Edge）';
    return;
  }
  recognition = new SR();
  recognition.lang = 'zh-CN';
  recognition.continuous = true;
  recognition.interimResults = true;

  recognition.onresult = (e) => {
    let interim = '', final = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const t = e.results[i][0].transcript;
      if (e.results[i].isFinal) final += t;
      else interim += t;
    }
    const resultEl = document.getElementById('asr-result');
    if (interim) resultEl.textContent = '识别中：' + interim;
    if (final) {
      resultEl.textContent = '识别结果：' + final;
      handleCommand(final, 'asr');
    }
  };
  recognition.onerror = (e) => {
    document.getElementById('asr-status').textContent = `识别错误: ${e.error}（麦克风权限/网络）`;
  };
  recognition.onend = () => {
    if (listening) {
      // continuous 模式下浏览器可能自动结束，自动重启
      setTimeout(() => { if (listening) { try { recognition.start(); } catch (e) {} } }, 300);
    } else {
      document.getElementById('asr-status').textContent = '已停止监听';
    }
  };
  btn.addEventListener('click', toggleListening);
}

function toggleListening() {
  const btn = document.getElementById('asr-btn');
  const statusEl = document.getElementById('asr-status');
  if (!recognition) return;
  if (!listening) {
    try {
      recognition.start();
      listening = true;
      btn.textContent = '停止监听';
      statusEl.textContent = '🎤 监听中… 请说出指令';
    } catch (e) {
      statusEl.textContent = '启动失败: ' + (e.message || e);
    }
  } else {
    listening = false;
    try { recognition.stop(); } catch (e) {}
    btn.textContent = '开始监听';
    statusEl.textContent = '已停止监听';
  }
}

/** 识别文本 → 指令匹配与执行（返回匹配结果，供调试/验证） */
function handleCommand(text, source) {
  const t = String(text || '').replace(/\s+/g, '');
  let matched = null;

  const reachPatterns = [
    { rid: 'reach-a', re: /查看(?:reach-?a|上游)/i },
    { rid: 'reach-b', re: /查看(?:reach-?b|中游)/i },
    { rid: 'reach-c', re: /查看(?:reach-?c|下游)/i },
  ];
  for (const p of reachPatterns) {
    if (p.re.test(t)) {
      selectReach(p.rid);
      matched = { command: '查看断面', detail: p.rid };
      break;
    }
  }
  if (!matched && /播报(?:当前)?(?:状态|结论)|朗读/.test(t)) {
    speakAllStatus();
    matched = { command: '播报状态', detail: null };
  }
  if (!matched && /开始处理|开始干预|处理事件/.test(t)) {
    matched = { command: '开始处理', detail: eventAction('handling') ? '已发送' : '无 OPEN 事件' };
  }
  if (!matched && /验证恢复|确认恢复|恢复验证/.test(t)) {
    matched = { command: '验证恢复', detail: eventAction('recovered') ? '已发送' : '无 HANDLING 事件' };
  }
  if (!matched && /关闭事件|结束事件/.test(t)) {
    matched = { command: '关闭事件', detail: eventAction('closed') ? '已发送' : '无活动事件' };
  }

  const cmdEl = document.getElementById('asr-command');
  if (cmdEl) {
    if (matched) {
      cmdEl.textContent = `✓ 执行指令：${matched.command}` +
                          (matched.detail ? `（${matched.detail}）` : '') +
                          `　[${source || 'debug'}]`;
      cmdEl.classList.add('flash');
      setTimeout(() => cmdEl.classList.remove('flash'), 1500);
    } else if (t) {
      cmdEl.textContent = `未识别到指令：${text}`;
    }
  }
  console.log('[multimodal] 指令处理:', matched ? matched.command : '(未匹配)', '←', text);
  return matched;
}

// ===================== 事件操作（与其它端同一 MQTT 事件链） =====================

/** 取当前活动事件：优先选中断面，其次任意 */
function currentEvent(states, preferReach) {
  const list = Object.values(events).filter((ev) => states.includes(ev.state));
  const prefer = preferReach || selectedReach;
  if (prefer) {
    const hit = list.find((ev) => ev.reachId === prefer);
    if (hit) return hit;
  }
  return list[0] || null;
}

function eventAction(action) {
  const need = action === 'handling' ? ['OPEN']
             : action === 'recovered' ? ['HANDLING']
             : ['OPEN', 'HANDLING'];
  const ev = currentEvent(need);
  const labels = { handling: '开始处理', recovered: '验证恢复', closed: '关闭事件' };
  if (!ev) {
    showToast(`没有可操作的事件（需 ${need.join('/')} 状态）`);
    return false;
  }
  const topic = EVENT_TOPIC_TEMPLATE.replace('{reachId}', ev.reachId);
  TideWatchMQTT.publishTopic(topic, {
    eventId: ev.eventId,
    reachId: ev.reachId,
    action: action,
    operator: 'voice-user',
    time: nowStr(),
    timestamp: Date.now(),
  });
  showToast(`已发送：${labels[action]}（${ev.eventId}）`);
  return true;
}

// ===================== MQTT =====================

function onDataMessage(data) {
  if (!data || !REACH_IDS.includes(data.reachId)) return;
  const status = renderCard(data.reachId, data);
  if (!status) return;
  latestData[data.reachId] = Object.assign({}, data, { status: status });
  if (status !== '正常') {
    anomalyCount[data.reachId] += 1;
  } else {
    anomalyCount[data.reachId] = 0;
  }
  updatePriority();
  announceIfNeeded(data.reachId, latestData[data.reachId]);
}

function onEventMessage(data) {
  if (!data || !data.eventId || !data.reachId) return;
  if (data.seq === undefined || data.state === undefined) return;   // 指令回显忽略
  if (lastSeq[data.eventId] !== undefined && data.seq <= lastSeq[data.eventId]) return;
  lastSeq[data.eventId] = data.seq;
  events[data.eventId] = Object.assign(events[data.eventId] || {}, data);
  console.log(`[multimodal] 事件 ${data.eventId} → ${data.state}`);
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

async function fetchInitialState() {
  try {
    const res = await fetch(STATE_API);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const state = await res.json();
    REACH_IDS.forEach((rid) => {
      if (state[rid]) {
        const s = state[rid];
        latestData[rid] = {
          reachId: rid, waterLevel: s.waterLevel, turbidity: s.turbidity,
          flowLevel: s.flowLevel, status: s.status, time: s.time,
        };
        renderCard(rid, latestData[rid]);
      }
    });
    updatePriority();
  } catch (e) {
    console.warn('[multimodal] 初始状态拉取失败:', e.message);
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
  } catch (e) {
    console.warn('[multimodal] 初始事件拉取失败:', e.message);
  }
}

// ===================== 启动 =====================

document.addEventListener('DOMContentLoaded', () => {
  // 卡片点击选中
  REACH_IDS.forEach((rid) => {
    const card = document.getElementById('card-' + rid);
    if (card) card.addEventListener('click', () => selectReach(rid));
  });

  document.getElementById('cam-btn').addEventListener('click', initCamera);
  document.getElementById('photo-btn').addEventListener('click', capturePhoto);
  document.getElementById('speak-btn').addEventListener('click', speakAllStatus);
  document.getElementById('auto-speak').addEventListener('change', (e) => {
    autoSpeak = e.target.checked;
    showToast(autoSpeak ? '已开启自动播报' : '已关闭自动播报');
  });

  initASR();
  startOverlayLoop();
  initCamera();   // 自动尝试启动（localhost 可直接授权）

  TideWatchMQTT.onConnectionChange(setConnStatus);
  TideWatchMQTT.connect();
  TideWatchMQTT.subscribe(onDataMessage);
  TideWatchMQTT.subscribeTopic('tidewatch/events/+', onEventMessage);
  fetchInitialState();
  fetchInitialEvents();

  // 调试/核验钩子（自动化验证与现场核验用）
  window.__multimodalDebug = {
    handleCommand: (t) => handleCommand(t, 'debug'),
    getSelected: () => selectedReach,
    getPriority: () => priorityInfo ? Object.assign({}, priorityInfo) : null,
    getSpeakLog: () => speakLog.slice(),
    getOverlayInfo: () => Object.assign({}, overlayInfo),
    getCameraState: () => {
      const v = document.getElementById('camera');
      return { ready: cameraReady, videoWidth: v ? v.videoWidth : 0, videoHeight: v ? v.videoHeight : 0 };
    },
    isSrSupported: () => srSupported,
    capturePhoto: () => capturePhoto(),
    getPhotoInfo: () => photoInfo,
  };
});
