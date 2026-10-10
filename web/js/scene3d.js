// TideWatch 3D 数字空间（E1）
// Three.js r128（CDN，非模块）+ OrbitControls
// 3 个断面各占河道一段：水面颜色/高度随 status/waterLevel 变化，洪水预警闪烁+波动，
// 优先关注红色脉冲光圈（复用 calcPriority），事件状态（OPEN/HANDLING/RECOVERED）以状态牌体现
// 所有数据来自 MQTT tidewatch/+/data 与 tidewatch/events/+，不做轮询

// ===================== 统一约定 =====================
const REACH_DEFS = [
  { id: 'reach-a', name: '上游', x: -22, phase: 0.0 },
  { id: 'reach-b', name: '中游', x: 0,   phase: 1.7 },
  { id: 'reach-c', name: '下游', x: 22,  phase: 3.4 },
];
const REACH_POS = { 'reach-a': '上游', 'reach-b': '中游', 'reach-c': '下游' };

// 水面/标记颜色（E1 规格：正常→蓝）
const WATER_COLORS = {
  '正常': 0x1890ff,
  '高水位警戒': 0xfaad14,
  '洪水预警': 0xf5222d,
  '水质浑浊': 0x13c2c2,
};
const WATER_COLOR_CSS = {
  '正常': '#1890ff', '高水位警戒': '#faad14', '洪水预警': '#f5222d', '水质浑浊': '#13c2c2',
};
const UNKNOWN_COLOR = 0x3a5f8a;

const STATE_API = 'http://localhost:8000/api/state';
const EVENTS_API = 'http://localhost:8001/api/events';

// ===================== 数据状态 =====================
const latestData = { 'reach-a': null, 'reach-b': null, 'reach-c': null };
const latestEvent = { 'reach-a': null, 'reach-b': null, 'reach-c': null }; // 每断面最新事件
const anomalyCount = { 'reach-a': 0, 'reach-b': 0, 'reach-c': 0 };
const events = {};
const lastSeq = {};

let selectedReach = null;
let priorityInfo = null;

// ===================== 辅助 =====================
const clamp01 = (v) => Math.min(1, Math.max(0, v));
const bendZ = (x) => 3.5 * Math.sin(x * 0.12);   // 河道中心线弯曲

function getFlowText(flowLevel) {
  const fallback = { 0: '缓流', 1: '平稳', 2: '湍急', 3: '湍流' };
  const map = (typeof FLOW_LEVELS !== 'undefined') ? FLOW_LEVELS : fallback;
  return map[flowLevel] || '未知';
}

// ===================== Three.js 场景 =====================
let scene, camera, renderer, controls;
const markers = {};      // reachId -> 标记对象集合
const markerList = [];   // 供拾取

const raycaster = (typeof THREE !== 'undefined') ? new THREE.Raycaster() : null;
const pointer = (typeof THREE !== 'undefined') ? new THREE.Vector2() : null;

function initScene() {
  const container = document.getElementById('container');

  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  container.appendChild(renderer.domElement);

  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0a1628);
  scene.fog = new THREE.Fog(0x0a1628, 75, 160);

  camera = new THREE.PerspectiveCamera(50, window.innerWidth / window.innerHeight, 0.1, 500);
  camera.position.set(0, 17, 36);

  if (typeof THREE.OrbitControls === 'function') {
    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 1, 0);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.minDistance = 12;
    controls.maxDistance = 90;
    controls.maxPolarAngle = Math.PI * 0.47;
  }

  // 光照
  scene.add(new THREE.AmbientLight(0xffffff, 0.55));
  const sun = new THREE.DirectionalLight(0xffffff, 0.9);
  sun.position.set(30, 45, 22);
  scene.add(sun);

  // 地面
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(160, 90),
    new THREE.MeshPhongMaterial({ color: 0x14352a })
  );
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -0.08;
  scene.add(ground);

  // 河道（3 段，每段对应一个断面）+ 断面标记
  REACH_DEFS.forEach((def) => {
    markers[def.id] = buildReach(def);
  });

  // 水闸（空间对象装饰）
  buildGate(11, bendZ(11));

  window.addEventListener('resize', onResize);
  animate();
  console.log('[3d] 场景初始化完成');
}

/** 单段河道水面（弯曲 PlaneGeometry，顶点可波动） */
function buildRiverSegment(def) {
  const geo = new THREE.PlaneGeometry(22, 10, 44, 12);
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const wx = pos.getX(i) + def.x;
    pos.setY(i, pos.getY(i) - bendZ(wx));   // local y → world -z，形成弯曲
  }
  geo.computeVertexNormals();

  const mat = new THREE.MeshPhongMaterial({
    color: UNKNOWN_COLOR, transparent: true, opacity: 0.78,
    shininess: 80, side: THREE.DoubleSide,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.rotation.x = -Math.PI / 2;
  mesh.position.set(def.x, 0.25, 0);
  scene.add(mesh);

  return {
    mesh, geo, mat, def, phase: def.phase,
    curHeight: 0.25, targetHeight: 0.25,   // 水面整体高度（随 waterLevel）
    curAmp: 0.07, targetAmp: 0.07,          // 波动幅度（洪水预警加大）
  };
}

/** CanvasTexture 文字精灵 */
function makeSprite(canvasW, canvasH, scaleX, scaleY) {
  const canvas = document.createElement('canvas');
  canvas.width = canvasW;
  canvas.height = canvasH;
  const texture = new THREE.CanvasTexture(canvas);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
    map: texture, transparent: true, depthTest: false,
  }));
  sprite.scale.set(scaleX, scaleY, 1);
  sprite.renderOrder = 20;
  return { canvas, texture, sprite };
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function drawLabel(labelObj, def, d) {
  const ctx = labelObj.canvas.getContext('2d');
  const W = 512, H = 192;
  ctx.clearRect(0, 0, W, H);
  const color = (d && WATER_COLOR_CSS[d.status]) || '#3a5f8a';

  roundRect(ctx, 8, 8, W - 16, H - 16, 26);
  ctx.fillStyle = 'rgba(19, 34, 56, 0.92)';
  ctx.fill();
  ctx.lineWidth = 4;
  ctx.strokeStyle = color;
  ctx.stroke();

  ctx.textBaseline = 'middle';
  ctx.fillStyle = '#e6f1ff';
  ctx.font = 'bold 44px "PingFang SC", "Microsoft YaHei", sans-serif';
  ctx.fillText(`${def.id} · ${def.name}`, 36, 60);

  ctx.fillStyle = color;
  ctx.font = 'bold 40px "PingFang SC", "Microsoft YaHei", sans-serif';
  ctx.fillText(d ? d.status : '暂无数据', 36, 116);

  ctx.fillStyle = '#8ca6c8';
  ctx.font = '30px "PingFang SC", "Microsoft YaHei", sans-serif';
  ctx.fillText(d ? `水位 ${d.waterLevel}m · 水流 ${d.flowLevel} ${getFlowText(d.flowLevel)}` : '等待数据…', 36, 160);

  labelObj.texture.needsUpdate = true;
}

function drawEventSprite(obj, state) {
  const ctx = obj.canvas.getContext('2d');
  const W = 512, H = 96;
  ctx.clearRect(0, 0, W, H);
  if (!state || state === 'CLOSED') {
    obj.sprite.visible = false;
    return;
  }
  const styles = {
    OPEN:      { bg: 'rgba(245,34,45,.92)',  text: '事件 OPEN · 待处理' },
    HANDLING:  { bg: 'rgba(250,173,20,.95)', text: '事件 HANDLING · 处理中' },
    RECOVERED: { bg: 'rgba(82,196,26,.92)',  text: '✓ 事件已恢复' },
  };
  const st = styles[state] || styles.OPEN;
  roundRect(ctx, 6, 8, W - 12, H - 16, 36);
  ctx.fillStyle = st.bg;
  ctx.fill();
  ctx.fillStyle = '#fff';
  ctx.font = 'bold 38px "PingFang SC", "Microsoft YaHei", sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(st.text, W / 2, H / 2);
  ctx.textAlign = 'left';
  obj.sprite.visible = true;
  obj.texture.needsUpdate = true;
}

/** 断面标记：监测桩 + 状态信标 + 水位柱 + 标签 + 事件牌 + 光圈 */
function buildReach(def) {
  const seg = buildRiverSegment(def);

  const group = new THREE.Group();
  group.position.set(def.x, 0, bendZ(def.x));
  group.userData.reachId = def.id;

  // 监测桩
  const pole = new THREE.Mesh(
    new THREE.CylinderGeometry(0.22, 0.32, 3.0, 12),
    new THREE.MeshPhongMaterial({ color: 0x7c8fa8 })
  );
  pole.position.y = 1.5;
  pole.userData.reachId = def.id;
  group.add(pole);

  // 状态信标（颜色 = status）
  const beaconMat = new THREE.MeshPhongMaterial({
    color: UNKNOWN_COLOR, emissive: UNKNOWN_COLOR, emissiveIntensity: 0.35,
  });
  const beacon = new THREE.Mesh(new THREE.CylinderGeometry(0.68, 0.68, 0.9, 16), beaconMat);
  beacon.position.y = 3.45;
  beacon.userData.reachId = def.id;
  group.add(beacon);

  // 水位指示柱（高度 = waterLevel 归一化）
  const gaugeGeo = new THREE.CylinderGeometry(0.42, 0.42, 1, 12);
  gaugeGeo.translate(0, 0.5, 0);
  const gauge = new THREE.Mesh(
    gaugeGeo,
    new THREE.MeshPhongMaterial({ color: UNKNOWN_COLOR, transparent: true, opacity: 0.55 })
  );
  gauge.position.set(1.35, 0, 0);
  gauge.scale.y = 0.2;
  gauge.userData.reachId = def.id;
  group.add(gauge);

  // 大号隐形点击热区（方便拾取）
  const hitArea = new THREE.Mesh(
    new THREE.CylinderGeometry(1.6, 1.6, 7, 10),
    new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false })
  );
  hitArea.position.y = 3.5;
  hitArea.userData.reachId = def.id;
  group.add(hitArea);

  // 标签（CanvasTexture sprite）
  const label = makeSprite(512, 192, 5.2, 1.95);
  label.sprite.position.set(0, 4.9, 0);
  drawLabel(label, def, null);
  group.add(label.sprite);

  // 事件状态牌
  const evSprite = makeSprite(512, 96, 4.6, 0.86);
  evSprite.sprite.position.set(0, 6.4, 0);
  evSprite.sprite.visible = false;
  group.add(evSprite.sprite);

  // 优先关注脉冲光圈（红）
  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(2.3, 0.14, 10, 56),
    new THREE.MeshBasicMaterial({ color: 0xf5222d, transparent: true, opacity: 0.85 })
  );
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = 0.12;
  ring.visible = false;
  group.add(ring);

  // 选中光圈（白）
  const selRing = new THREE.Mesh(
    new THREE.TorusGeometry(1.75, 0.06, 8, 48),
    new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.85 })
  );
  selRing.rotation.x = -Math.PI / 2;
  selRing.position.y = 0.1;
  selRing.visible = false;
  group.add(selRing);

  scene.add(group);

  // 水流箭头（flowLevel 越高越快）
  const arrowMesh = new THREE.Mesh(
    new THREE.ConeGeometry(0.32, 1.0, 8),
    new THREE.MeshPhongMaterial({ color: 0xbfe3ff, emissive: 0x21507a, emissiveIntensity: 0.5 })
  );
  arrowMesh.rotation.z = -Math.PI / 2;   // 指向 +x（下游方向）
  scene.add(arrowMesh);
  const arrow = { mesh: arrowMesh, x: def.x - 10 + Math.random() * 8, reachId: def.id };

  const obj = {
    def, seg, group, pole, beacon, gauge, hitArea, label, evSprite, ring, selRing, arrow,
    gaugeTarget: 0.2,
  };
  markerList.push(obj);
  return obj;
}

/** 水闸（空间对象装饰） */
function buildGate(x, z) {
  const gate = new THREE.Group();
  gate.position.set(x, 0, z);
  const base = new THREE.Mesh(
    new THREE.BoxGeometry(3.4, 0.6, 1.2),
    new THREE.MeshPhongMaterial({ color: 0x5b6c80 })
  );
  base.position.y = 1.6;
  gate.add(base);
  [-1.5, 1.5].forEach((dx) => {
    const pillar = new THREE.Mesh(
      new THREE.CylinderGeometry(0.3, 0.3, 3.4, 10),
      new THREE.MeshPhongMaterial({ color: 0x40536a })
    );
    pillar.position.set(dx, 1.7, 0);
    gate.add(pillar);
  });
  scene.add(gate);
}

// ===================== 状态 → 视觉映射 =====================

function applyReachVisual(reachId) {
  const m = markers[reachId];
  const d = latestData[reachId];
  if (!m) return;

  const status = d ? d.status : null;
  const color = WATER_COLORS[status] || UNKNOWN_COLOR;

  m.seg.mat.color.setHex(color);
  m.beacon.material.color.setHex(color);
  m.beacon.material.emissive.setHex(color);
  m.gauge.material.color.setHex(color);

  // 水面高度归一化：3.0~10.0 → 0~1（规格公式）
  const norm = d ? clamp01((d.waterLevel - 3.0) / 7.0) : 0;
  m.seg.targetHeight = 0.25 + norm * 1.3;
  m.gaugeTarget = Math.max(0.15, norm * 2.2);
  m.seg.targetAmp = (status === '洪水预警') ? 0.38 : 0.07;

  drawLabel(m.label, m.def, d);
}

function applyEventVisual(reachId) {
  const m = markers[reachId];
  const ev = latestEvent[reachId];
  if (!m) return;
  drawEventSprite(m.evSprite, ev ? ev.state : null);
}

function updatePriority() {
  let best = null;
  REACH_DEFS.forEach((def) => {
    const d = latestData[def.id];
    if (!d) return;
    const score = calcPriority(d.status, d.flowLevel, anomalyCount[def.id]);
    if (best === null || score > best.score) {
      best = { reachId: def.id, score: score };
    }
  });
  priorityInfo = best;
  REACH_DEFS.forEach((def) => {
    const m = markers[def.id];
    if (m) m.ring.visible = !!(best && best.reachId === def.id && best.score > 0);
  });
  const el = document.getElementById('panel-priority');
  if (el) el.textContent = best ? `${best.reachId}（优先度 ${best.score}）` : '—';
}

function updatePanel() {
  const placeholder = document.getElementById('panel-placeholder');
  const body = document.getElementById('panel-body');
  if (!placeholder || !body) return;

  const d = selectedReach ? latestData[selectedReach] : null;
  if (!selectedReach || !d) {
    placeholder.classList.remove('hidden');
    body.classList.add('hidden');
    if (selectedReach) placeholder.textContent = `${selectedReach} 等待数据…`;
    return;
  }
  placeholder.classList.add('hidden');
  body.classList.remove('hidden');

  const set = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
  set('p-reach', `${selectedReach}（${REACH_POS[selectedReach]}）`);
  set('p-water', `${d.waterLevel} 米`);
  set('p-turbidity', `${d.turbidity} NTU`);
  set('p-flow', `${d.flowLevel} · ${getFlowText(d.flowLevel)}`);
  set('p-time', d.time || '—');

  const stEl = document.getElementById('p-status');
  const color = WATER_COLOR_CSS[d.status] || '#6b7280';
  stEl.innerHTML = `<span class="chip" style="background:${color}">${d.status}</span>`;

  const evEl = document.getElementById('p-event');
  const ev = latestEvent[selectedReach];
  if (!ev || ev.state === 'CLOSED') {
    evEl.textContent = '无';
  } else {
    const colors = { OPEN: '#f5222d', HANDLING: '#faad14', RECOVERED: '#52c41a' };
    const labels = { OPEN: 'OPEN 待处理', HANDLING: 'HANDLING 处理中', RECOVERED: '已恢复' };
    evEl.innerHTML = `<span class="chip" style="background:${colors[ev.state]}">${labels[ev.state]}</span>`;
  }
}

function selectReach(reachId) {
  if (!markers[reachId]) return;
  selectedReach = reachId;
  REACH_DEFS.forEach((def) => {
    const m = markers[def.id];
    if (m) m.selRing.visible = (def.id === reachId);
  });
  updatePanel();
  console.log(`[3d] 选中断面: ${reachId}`);
}

// ===================== 交互（Raycaster） =====================

function updatePointer(e) {
  pointer.x = (e.clientX / window.innerWidth) * 2 - 1;
  pointer.y = -(e.clientY / window.innerHeight) * 2 + 1;
}

function pickReach(e) {
  if (!raycaster || !camera) return null;
  updatePointer(e);
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects(markerList.map((m) => m.group), true);
  for (const h of hits) {
    if (h.object.userData.reachId) return h.object.userData.reachId;
  }
  return null;
}

function initInteraction() {
  const tt = document.getElementById('tooltip');
  const el = renderer.domElement;

  el.addEventListener('pointermove', (e) => {
    const rid = pickReach(e);
    if (rid) {
      const d = latestData[rid];
      tt.innerHTML = d
        ? `<b>${rid}（${REACH_POS[rid]}）</b><br>${d.status} · 水位 ${d.waterLevel} 米 · 浊度 ${d.turbidity} NTU`
        : `<b>${rid}（${REACH_POS[rid]}）</b><br>等待数据…`;
      tt.classList.remove('hidden');
      tt.style.left = Math.min(e.clientX + 16, window.innerWidth - 260) + 'px';
      tt.style.top = (e.clientY + 12) + 'px';
      el.style.cursor = 'pointer';
    } else {
      tt.classList.add('hidden');
      el.style.cursor = 'grab';
    }
  });

  // 点击 = 按下/抬起位移小（与 OrbitControls 拖拽区分）
  let downPos = null;
  el.addEventListener('pointerdown', (e) => { downPos = { x: e.clientX, y: e.clientY }; });
  el.addEventListener('pointerup', (e) => {
    if (!downPos) return;
    const moved = Math.hypot(e.clientX - downPos.x, e.clientY - downPos.y);
    downPos = null;
    if (moved > 6) return;   // 拖拽旋转视角，不算点击
    const rid = pickReach(e);
    if (rid) selectReach(rid);
  });
}

// ===================== 动画循环 =====================

const clock = new THREE.Clock();

function animate() {
  requestAnimationFrame(animate);
  const dt = Math.min(clock.getDelta(), 0.1);
  const t = clock.elapsedTime;

  REACH_DEFS.forEach((def) => {
    const m = markers[def.id];
    const d = latestData[def.id];

    // 水面整体高度与波动幅度平滑过渡
    m.seg.curHeight += (m.seg.targetHeight - m.seg.curHeight) * Math.min(1, dt * 3);
    m.seg.curAmp += (m.seg.targetAmp - m.seg.curAmp) * Math.min(1, dt * 3);
    m.seg.mesh.position.y = m.seg.curHeight;

    // 顶点波动（洪水预警时幅度加大）
    const posAttr = m.seg.geo.attributes.position;
    for (let i = 0; i < posAttr.count; i++) {
      const wx = posAttr.getX(i) + def.x;
      const wy = posAttr.getY(i);
      const wave = m.seg.curAmp * (Math.sin(wx * 0.9 + t * 2.2 + m.seg.phase) * 0.6 +
                                   Math.sin(wy * 1.4 + t * 1.6) * 0.4);
      posAttr.setZ(i, wave);
    }
    posAttr.needsUpdate = true;

    // 水位柱高度平滑
    m.gauge.scale.y += (m.gaugeTarget - m.gauge.scale.y) * Math.min(1, dt * 3);

    // 洪水预警：信标闪烁
    if (d && d.status === '洪水预警') {
      const k = 0.5 + 0.5 * Math.sin(t * 7);
      m.beacon.material.emissiveIntensity = 0.4 + 0.6 * k;
      const s = 1 + 0.08 * k;
      m.beacon.scale.set(s, 1, s);
    } else {
      m.beacon.material.emissiveIntensity = 0.35;
      m.beacon.scale.set(1, 1, 1);
    }

    // 光圈浮在水面波动之上
    m.ring.position.y = m.seg.curHeight + m.seg.curAmp + 0.06;
    m.selRing.position.y = m.seg.curHeight + m.seg.curAmp + 0.04;

    // 优先关注光圈脉冲
    if (m.ring.visible) {
      const p = 1 + 0.12 * Math.sin(t * 4);
      m.ring.scale.set(p, p, 1);
      m.ring.material.opacity = 0.6 + 0.3 * Math.sin(t * 4);
    }

    // 选中光圈旋转
    if (m.selRing.visible) m.selRing.rotation.z = t * 0.8;

    // 水流箭头（flowLevel 越高越快）
    const flow = d ? (d.flowLevel || 0) : 0;
    m.arrow.x += dt * (2.0 + flow * 2.0);
    if (m.arrow.x > def.x + 10) m.arrow.x = def.x - 10;
    m.arrow.mesh.position.set(m.arrow.x, m.seg.curHeight + 0.28, bendZ(m.arrow.x));
  });

  if (controls) controls.update();
  renderer.render(scene, camera);
}

function onResize() {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}

// ===================== MQTT =====================

function onDataMessage(data) {
  if (!data || !markers[data.reachId]) {
    console.warn('[3d] 忽略未知断面消息:', data && data.reachId);
    return;
  }
  const result = judge(data.waterLevel, data.turbidity);
  const status = (result && result.status) ? result.status : result;
  if (status !== data.status) {
    console.warn(`[3d] ${data.reachId} status 不一致: 消息="${data.status}"，规则="${status}"，以规则为准`);
  }
  latestData[data.reachId] = Object.assign({}, data, { status: status });

  if (status !== '正常') {
    anomalyCount[data.reachId] += 1;
  } else {
    anomalyCount[data.reachId] = 0;
  }

  applyReachVisual(data.reachId);
  updatePriority();
  updatePanel();
  console.log(`[3d] ${data.reachId} 更新: ${status} 水位=${data.waterLevel} 水流=${data.flowLevel}`);
}

function onEventMessage(data) {
  if (!data || !data.eventId || !markers[data.reachId]) return;
  if (data.seq === undefined || data.state === undefined) return;   // 指令回显忽略
  if (lastSeq[data.eventId] !== undefined && data.seq <= lastSeq[data.eventId]) return;
  lastSeq[data.eventId] = data.seq;
  events[data.eventId] = Object.assign(events[data.eventId] || {}, data);

  const cur = latestEvent[data.reachId];
  if (!cur || data.eventId === cur.eventId || (data.open_time || '') > (cur.open_time || '')) {
    latestEvent[data.reachId] = data;
  }
  applyEventVisual(data.reachId);
  updatePanel();
  console.log(`[3d] 事件 ${data.eventId} → ${data.state}（${data.reachId}）`);
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
    REACH_DEFS.forEach((def) => {
      if (state[def.id]) {
        const s = state[def.id];
        latestData[def.id] = {
          reachId: def.id, waterLevel: s.waterLevel, turbidity: s.turbidity,
          flowLevel: s.flowLevel, status: s.status, time: s.time,
        };
        applyReachVisual(def.id);
      }
    });
    updatePriority();
    updatePanel();
  } catch (e) {
    console.warn('[3d] 初始状态拉取失败（shared_state 未启动？）：', e.message);
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
      const cur = latestEvent[ev.reachId];
      if (!cur || (ev.open_time || '') > (cur.open_time || '')) {
        latestEvent[ev.reachId] = ev;
      }
    });
    REACH_DEFS.forEach((def) => applyEventVisual(def.id));
    updatePanel();
  } catch (e) {
    console.warn('[3d] 初始事件拉取失败（event_manager 未启动？）：', e.message);
  }
}

// ===================== 启动 =====================

function init() {
  try {
    initScene();
  } catch (e) {
    console.error('[3d] 场景初始化失败（WebGL 不可用？）:', e);
    document.getElementById('container').innerHTML =
      '<div style="color:#8ca6c8;padding:40px;font-size:14px">当前环境不支持 WebGL，无法显示 3D 场景</div>';
    return;
  }
  initInteraction();

  TideWatchMQTT.onConnectionChange(setConnStatus);
  TideWatchMQTT.connect();
  TideWatchMQTT.subscribe(onDataMessage);
  TideWatchMQTT.subscribeTopic('tidewatch/events/+', onEventMessage);
  fetchInitialState();
  fetchInitialEvents();

  // 调试/核验钩子
  window.__scene3dDebug = {
    getStates: () => JSON.parse(JSON.stringify(latestData)),
    getPriority: () => priorityInfo ? Object.assign({}, priorityInfo) : null,
    getSelected: () => selectedReach,
    getWaterColor: (rid) => '#' + markers[rid].seg.mat.color.getHexString(),
    getWaterHeight: (rid) => Number(markers[rid].seg.curHeight.toFixed(3)),
    getGaugeHeight: (rid) => Number(markers[rid].gauge.scale.y.toFixed(3)),
    getEventSpriteVisible: (rid) => markers[rid].evSprite.sprite.visible,
    selectReach: (rid) => selectReach(rid),
    projectReach: (rid) => {
      const v = new THREE.Vector3();
      markers[rid].beacon.getWorldPosition(v);
      v.project(camera);
      return {
        x: (v.x * 0.5 + 0.5) * window.innerWidth,
        y: (-v.y * 0.5 + 0.5) * window.innerHeight,
      };
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
