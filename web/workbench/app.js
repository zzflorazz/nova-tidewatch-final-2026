"use strict";

const el = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "—").replace(/[&<>"']/g, (char) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char]));
const statusInfo = {
  baseline: ["基线场景", ""], implemented: ["可运行", ""], SUPPORTED_SINGLE: ["证据支持单源", "single"],
  SUPPORTED_LOCAL: ["证据支持本地", "local"], MULTI_CANDIDATE: ["多源候选", "multi"],
  UNRESOLVED: ["来源未定", "unresolved"], loading: ["归因中", ""], error: ["运行失败", "error"]
};
const roles = {independent_source:"独立来源候选", propagation_path:"传播经过", missing_evidence:"缺少证据", local_check:"本地检查项", excluded:"已排除"};
const states = {retained:"保留", excluded:"已排除", propagation:"传播经过", not_excluded:"未排除", needs_review:"待核实"};
let scenarios = [];
let selected = null;
let busy = false;
let activeAnomaly = null;
let versions = [];
let selectedVersion = null;
let baselineCurrentSamples = [];
const backfillRecords = new Map();
const currentStatus = new Map();
const number = (value) => value === null || value === undefined ? "—" : new Intl.NumberFormat("zh-CN", {maximumFractionDigits:3}).format(value);
function date(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", {timeZone:"Asia/Shanghai", hour12:false});
}
function badge(status, showCode = false) {
  const [label, tone] = statusInfo[status] || [status || "未知", ""];
  return `<span class="badge ${tone}">${escapeHtml(label)}${showCode ? ` · ${escapeHtml(status)}` : ""}</span>`;
}
async function request(path, options) {
  const response = await fetch(path, options);
  const text = await response.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = {detail: text || response.statusText}; }
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    throw new Error(`HTTP ${response.status}：${detail}`);
  }
  return data;
}
function feedback(message, error = false) {
  const node = el("feedback");
  node.className = message ? `feedback${error ? " error" : ""}` : "";
  node.setAttribute("role", error ? "alert" : "status");
  node.textContent = message;
}
function renderScenarios() {
  el("scenario-list").innerHTML = scenarios.map((s) => `<button type="button" class="scenario${selected === s.scenario_id ? " selected" : ""}" data-scenario="${escapeHtml(s.scenario_id)}" aria-pressed="${selected === s.scenario_id}" ${busy || !s.target ? "disabled" : ""} aria-disabled="${busy || !s.target}">
    <span class="scenario-top"><span class="scenario-id">${escapeHtml(s.scenario_id)}</span>${s.origin === "self_built_demo" ? '<span class="badge demo-badge">自建对照</span>' : ""}${badge(!s.target ? "baseline" : currentStatus.get(s.scenario_id) || s.status)}</span>
    ${s.origin === "self_built_demo" ? '<div class="scenario-note demo-note">非案例书场景</div>' : ""}
    <div class="scenario-target">${escapeHtml(s.target?.reach_id || "全断面基线")} / ${escapeHtml(s.target?.metric || "无目标异常")}</div>
    <div class="scenario-time">${escapeHtml(date(s.analysis_time))}</div>
    ${!s.target ? '<div class="scenario-note">基线平稳 · 无活动异常目标</div>' : ""}
  </button>`).join("");
}
async function loadScenarios() {
  el("retry-list").hidden = true;
  el("scenario-list").setAttribute("aria-busy", "true");
  try {
    scenarios = await request("/api/scenarios");
    if (!Array.isArray(scenarios)) throw new Error("场景接口未返回列表");
    renderScenarios();
    el("scenario-count").textContent = `${scenarios.length} 个场景`;
    el("connection").textContent = "场景数据已连接";
    if (!scenarios.length) el("scenario-list").textContent = "当前没有可用场景。";
  } catch (error) {
    el("scenario-list").textContent = `场景加载失败：${error.message}`;
    el("connection").textContent = "场景数据连接失败";
    el("retry-list").hidden = false;
  } finally { el("scenario-list").setAttribute("aria-busy", "false"); }
}
function candidateTable(candidates, excluded = false) {
  if (!candidates.length) return '<p class="empty-small">本次没有对应候选。</p>';
  return `<div class="table-scroll"><table><thead><tr><th>来源</th><th>角色</th><th>绝对变化量</th><th>滞后 / 分钟</th><th>规则排序分</th><th>证据强度</th><th>状态</th></tr></thead><tbody>${candidates.map((c) => `<tr>
    <td>${escapeHtml(c.source_id)}</td><td>${escapeHtml(roles[c.role] || c.role)}</td><td>${escapeHtml(number(c.absolute_change))}</td>
    <td>${escapeHtml(number(c.time_lag_minutes))}</td><td>${escapeHtml(number(c.ranking_score))}</td><td>${escapeHtml(c.evidence_strength)}</td><td>${escapeHtml(states[c.status] || c.status)}</td></tr>
    ${excluded ? `<tr class="exclude-basis"><td colspan="7"><ul>${(c.exclude_basis?.length ? c.exclude_basis : ["未提供排除依据"]).map((basis) => `<li>${escapeHtml(basis)}</li>`).join("")}</ul></td></tr>` : ""}`).join("")}</tbody></table></div>`;
}
function renderResult(output, record = null) {
  const all = output.candidates || [];
  const first = versions.find((v) => v.version === 1);
  const latest = versions[versions.length - 1];
  const conclusionChanged = first && latest && first.conclusion !== latest.conclusion;
  const backfill = backfillRecords.get(`${activeAnomaly}:${output.attribution_version}`);
  const health = Object.entries(output.data_health || {});
  const rerank = output.reranking;
  const checks = [
    ["excluded_untouched", "排除项保持不变"], ["missing_evidence_untouched", "缺证据项保持不变"],
    ["status_unchanged", "业务状态保持不变"], ["propagation_path_excluded_from_ranking", "传播经过不参与来源排序"]
  ];
  const order = (values) => values?.length ? values.join(" → ") : "无保留的独立来源";
  el("result-content").innerHTML = `
    <section class="result-block conclusion-block"><div class="conclusion-head"><h3>归因结论</h3>${badge(output.status, true)}</div>
      <p class="conclusion">${escapeHtml(output.conclusion)}</p>
      ${conclusionChanged ? '<p id="conclusion-change" class="conclusion-change">与版本 1 相比结论已变化（对比版本 1 与最新版，仅提示文字差异）</p>' : ""}
      <p class="run-meta">运行 ${escapeHtml(output.run_id)}　归因版本 ${escapeHtml(output.attribution_version)}</p></section>
    <section class="result-block"><h3>断面数据健康</h3><div class="health-grid">${health.map(([reach, value]) => `<div class="health"><div class="health-head"><strong>${escapeHtml(reach)}</strong><span class="${["fresh","stale","missing"].includes(value.status) ? value.status : ""}">${escapeHtml(value.status)}</span></div><small>数据年龄 ${escapeHtml(number(value.age_minutes))} 分钟</small></div>`).join("") || '<p class="empty-small">接口未提供数据健康信息。</p>'}</div></section>
    <section class="result-block"><h3>候选与传播路径 <span class="score-note">相对分，不是概率</span></h3>
      ${backfill ? `<p id="backfill-record" class="backfill-record">本版本补数记录：${escapeHtml(backfill.reach_id)} · ${escapeHtml(backfill.metric)} · 事件时间 ${escapeHtml(backfill.event_time)} · 接收时间 ${escapeHtml(backfill.received_time)}</p>` : ""}${candidateTable(all.filter((c) => c.status !== "excluded"))}</section>
    <section class="result-block excluded-block"><h3>已排除项与具体依据 <span class="score-note">仅限本场景证据范围</span></h3>${candidateTable(all.filter((c) => c.status === "excluded"), true)}</section>
    <section class="result-block"><h3>模型重排序</h3>${rerank ? `
      <div class="order-compare"><div><span>规则排序</span><strong>${escapeHtml(order(rerank.rule_order))}</strong></div><div><span>模型排序</span><strong>${escapeHtml(order(rerank.model_order))}</strong></div></div>
      <p class="model-message">${rerank.applied ? `已应用重排序 · 排序${rerank.order_changed ? "发生变化" : "未变化"}` : escapeHtml(rerank.reason || "本次未应用模型重排序")}</p>
      <div class="checks">${checks.map(([key, label]) => `<div class="check${rerank.constraints?.[key] === true ? "" : " failed"}">${rerank.constraints?.[key] === true ? "✓" : "!"} ${label}：${rerank.constraints?.[key] === true ? "true" : escapeHtml(rerank.constraints?.[key] ?? "未提供")}</div>`).join("")}</div>
      <p class="model-message">${escapeHtml(rerank.note)}</p>` : '<p class="empty-small">接口未返回模型重排序信息。</p>'}</section>
    <section class="result-block"><h3>证据缺口与排查建议</h3><ul class="gap-list">${(output.evidence_gaps || []).map((gap) => `<li>${escapeHtml(gap)}</li>`).join("")}${(output.next_checks || []).map((check) => `<li>优先级 ${escapeHtml(check.priority)} · ${escapeHtml(check.reach_id)}：${escapeHtml(check.action)}</li>`).join("")}</ul></section>`;
}
function setBusy(value) {
  busy = value;
  renderScenarios();
  renderVersions();
  el("results").setAttribute("aria-busy", String(value));
  for (const control of el("backfill-form").elements) control.disabled = value;
}
function renderVersions() {
  el("version-list").innerHTML = versions.map((v) => `<button type="button" class="version-item${v.version === selectedVersion ? " active" : ""}" data-version="${v.version}" aria-pressed="${v.version === selectedVersion}" ${busy ? "disabled" : ""}>
    <span class="version-top"><strong>版本 ${v.version}</strong>${badge(v.status, true)}</span>
    <span class="version-conclusion" title="${escapeHtml(v.conclusion)}">${escapeHtml(v.conclusion.length > 90 ? v.conclusion.slice(0, 90) + "…" : v.conclusion)}</span>
    <span class="version-meta">${escapeHtml(v.run_id)} · ${escapeHtml(date(v.created_at))}</span>
  </button>`).join("");
  el("version-selection").textContent = selectedVersion ? `正在查看版本 ${selectedVersion}` : "";
}
async function showVersion(version) {
  const base = `/api/anomalies/${encodeURIComponent(activeAnomaly)}`;
  const record = await request(`${base}/attributions/${version}`);
  const reranking = await request(`${base}/rerank?version=${version}`);
  const sampleKey = (s) => JSON.stringify([s.reach_id, s.metric, s.value, s.event_time, s.received_time, s.source]);
  const originalKeys = new Set(baselineCurrentSamples.map(sampleKey));
  const added = (record.input_snapshot.current_samples || []).filter((s) => !originalKeys.has(sampleKey(s)));
  if (added.length && !backfillRecords.has(`${activeAnomaly}:${record.version}`)) {
    backfillRecords.set(`${activeAnomaly}:${record.version}`, added[added.length - 1]);
  }
  selectedVersion = record.version;
  renderResult({...record.output_json, run_id:record.run_id, attribution_version:record.version, reranking}, record);
  renderVersions();
}
async function refreshHistory() {
  versions = await request(`/api/anomalies/${encodeURIComponent(activeAnomaly)}/attributions`);
  if (!Array.isArray(versions) || !versions.length) throw new Error("归因成功，但接口未返回历史版本");
  versions.sort((a, b) => a.version - b.version);
  const first = await request(`/api/anomalies/${encodeURIComponent(activeAnomaly)}/attributions/${versions[0].version}`);
  baselineCurrentSamples = first.input_snapshot.current_samples || [];
  el("version-history").hidden = false;
  await showVersion(versions[versions.length - 1].version);
}
function initializeBackfill(s) {
  const form = el("backfill-form");
  form.reset();
  form.elements.namedItem("reach_id").value = s.target.reach_id;
  form.elements.namedItem("metric").value = s.target.metric;
  const cutoff = new Date(s.analysis_time).getTime();
  form.elements.namedItem("event_time").value = new Date(cutoff - 10 * 60000).toISOString();
  form.elements.namedItem("received_time").value = new Date(cutoff - 60000).toISOString();
  el("backfill-feedback").textContent = "";
  el("backfill-section").hidden = false;
}
async function runScenario(id) {
  const s = scenarios.find((item) => item.scenario_id === id);
  if (busy || !s?.target) return;
  selected = id;
  activeAnomaly = null;
  versions = [];
  selectedVersion = null;
  baselineCurrentSamples = [];
  el("version-history").hidden = true;
  el("backfill-section").hidden = true;
  setBusy(true);
  currentStatus.set(id, "loading");
  renderScenarios();
  el("selection-label").textContent = id;
  el("result-content").replaceChildren();
  feedback(`正在运行 ${id} 归因并读取版本历史…`);
  let output;
  try {
    output = await request("/api/attributions/run", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({scenario_id:id, use_rerank:true})});
    currentStatus.set(id, output.status);
    activeAnomaly = `${s.target.reach_id}-${s.target.metric}-${s.target.event_time}`;
    await refreshHistory();
    initializeBackfill(s);
    feedback("");
  } catch (error) {
    if (!output) currentStatus.set(id, "error");
    feedback(`归因或历史读取失败：${error.message}。可重新选择场景重试。`, true);
  } finally { setBusy(false); }
}
async function selectVersion(version) {
  if (busy || !activeAnomaly) return;
  setBusy(true);
  feedback(`正在读取版本 ${version}…`);
  try {
    await showVersion(version);
    feedback("");
  } catch (error) { feedback(`版本读取失败：${error.message}`, true); }
  finally { setBusy(false); }
}
async function submitBackfill(event) {
  event.preventDefault();
  if (busy || !activeAnomaly) return;
  const form = el("backfill-form");
  if (!form.reportValidity()) return;
  const fields = new FormData(form);
  const sample = Object.fromEntries(["reach_id", "metric", "event_time", "received_time", "source"].map((key) => [key, String(fields.get(key)).trim()]));
  sample.value = Number(fields.get("value"));
  const message = el("backfill-feedback");
  if (!Number.isFinite(sample.value)) { message.textContent = "请输入有限的数值"; return; }
  setBusy(true);
  message.textContent = "正在追加模拟样本并重新归因…";
  message.className = "";
  let output;
  try {
    output = await request(`/api/anomalies/${encodeURIComponent(activeAnomaly)}/samples`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({scenario_id:selected, sample})});
    backfillRecords.set(`${activeAnomaly}:${output.attribution_version}`, sample);
    currentStatus.set(selected, output.status);
    await refreshHistory();
    message.textContent = `已追加迟到样本，生成版本 ${output.attribution_version}`;
    feedback("");
  } catch (error) {
    message.className = "form-error";
    message.textContent = output ? `样本已生成版本 ${output.attribution_version}，但读取失败：${error.message}` : error.message;
  } finally { setBusy(false); }
}
el("scenario-list").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-scenario]");
  if (button && !button.disabled) runScenario(button.dataset.scenario);
});
el("version-list").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-version]");
  if (button && !button.disabled) selectVersion(Number(button.dataset.version));
});
el("backfill-form").addEventListener("submit", submitBackfill);
el("retry-list").addEventListener("click", loadScenarios);
loadScenarios();
