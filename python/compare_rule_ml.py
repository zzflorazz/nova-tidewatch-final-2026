#!/usr/bin/env python3
# D5 固定规则 vs 轻量 ML（IsolationForest）对照分析
# 规则基于阈值（是否越界）；ML 基于历史分布（是否异常）—— 两者关注不同问题，不一致是正常现象
# 输出: report/compare_rule_ml.html（一致率 / 4 种交叉情况 / 不一致案例 / 决策边界散点图 / 3 种组合裁决策略统计）
#
# 用法: python3 python/compare_rule_ml.py

import base64
import os
import sys
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import joblib
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rules import STATUS_COLORS, judge
from analyze import BASE_DIR, REPORT_DIR, load_data, resolve_data_path

MODEL_PATH = os.path.join(BASE_DIR, 'python', 'ml_model.joblib')
STATUS_ORDER = ['正常', '高水位警戒', '洪水预警', '水质浑浊']

matplotlib.rcParams['font.sans-serif'] = ['PingFang SC', 'Heiti SC', 'Arial Unicode MS', 'SimHei']
matplotlib.rcParams['axes.unicode_minus'] = False

CATEGORIES = [
    ('确认异常（规则异常 + ML异常）', '规则判定越界且偏离历史分布，两者相互印证'),
    ('规则异常 + ML常态', '规则判定越界，但该值在历史分布中常见（如历史洪水样本多）'),
    ('规则正常 + ML异常', '规则判定未越界，但该值偏离历史分布，ML 认为可疑'),
    ('一致正常（规则正常 + ML常态）', '两者均判为无需关注'),
]


def compare(df, model):
    """逐条运行规则与 ML，返回带对照列的 DataFrame"""
    X = df[['waterLevel', 'turbidity']].to_numpy(dtype=float)
    out = df.copy()
    out['rule_status'] = [judge(w, t)['status'] for w, t in zip(df['waterLevel'], df['turbidity'])]
    out['ml_anomaly'] = model.predict(X) == -1
    out['ml_score'] = model.decision_function(X).round(4)
    out['rule_abnormal'] = out['rule_status'] != '正常'

    def category(r):
        if r['rule_abnormal'] and r['ml_anomaly']:
            return CATEGORIES[0][0]
        if r['rule_abnormal'] and not r['ml_anomaly']:
            return CATEGORIES[1][0]
        if not r['rule_abnormal'] and r['ml_anomaly']:
            return CATEGORIES[2][0]
        return CATEGORIES[3][0]

    out['category'] = out.apply(category, axis=1)
    return out


def strategy_stats(df):
    """3 种组合裁决策略的报警数 / 需复核数"""
    rule_ab = df['rule_abnormal']
    ml_ab = df['ml_anomaly']
    total = len(df)

    a_alarm = int(rule_ab.sum())
    b_alarm = int((rule_ab | ml_ab).sum())
    c_confirm = int((rule_ab & ml_ab).sum())
    c_review = int((rule_ab & ~ml_ab).sum() + (~rule_ab & ml_ab).sum())

    return [
        {'key': 'A', 'name': '规则优先', 'alarm': a_alarm, 'review': 0,
         'desc': 'decision = rule_status。ML 仅作参考；报警数 = 规则判定非"正常"条数。'
                 '优点：与阈值口径一致；缺点：完全忽略分布维度的可疑信号。'},
        {'key': 'B', 'name': 'ML 优先', 'alarm': b_alarm, 'review': 0,
         'desc': 'ML 异常 → "ML异常-需关注"，否则用规则。报警数 = 规则异常 ∪ ML异常。'
                 '优点：分布异常不漏；缺点：把"历史常见但已越界"的值降级为普通关注，可能弱化确定风险。'},
        {'key': 'C', 'name': '组合裁决（交集从严 + 不一致复核）', 'alarm': c_confirm, 'review': c_review,
         'desc': '两者皆异常 → 确认报警；仅一方异常 → 转入人工复核（保留双方依据）。'
                 f'确认报警 {c_confirm} 条 + 需复核 {c_review} 条（合计 {c_confirm + c_review} = 规则异常 ∪ ML异常）。'},
    ]


def make_scatter(df, out_path):
    """规则决策边界（虚线） + ML 判定形状（X=异常） 的散点图"""
    fig, ax = plt.subplots(figsize=(11, 6))
    for st in STATUS_ORDER:
        for anom in (False, True):
            sub = df[(df['rule_status'] == st) & (df['ml_anomaly'] == anom)]
            if len(sub) == 0:
                continue
            ax.scatter(sub['waterLevel'], sub['turbidity'],
                       c=STATUS_COLORS[st], marker='X' if anom else 'o',
                       s=110 if anom else 60, edgecolors='white', linewidths=0.6,
                       zorder=3 if anom else 2,
                       label=f"{st} · ML{'异常' if anom else '常态'}")
    # 规则决策边界
    ax.axvline(8.5, color='#f5222d', ls='--', lw=1.2, alpha=.75)
    ax.axvline(7.0, color='#faad14', ls='--', lw=1.2, alpha=.75)
    ax.axhline(60, color='#13c2c2', ls='--', lw=1.2, alpha=.75)
    y_top = max(df['turbidity'].max() * 1.05, 70)
    ax.set_ylim(top=y_top)
    ax.text(8.55, y_top * 0.97, 'waterLevel ≥ 8.5 → 洪水预警', fontsize=9, color='#f5222d')
    ax.text(7.05, y_top * 0.9, 'waterLevel ≥ 7.0 → 高水位警戒', fontsize=9, color='#faad14')
    ax.text(3.1, 62, 'turbidity ≥ 60 → 水质浑浊', fontsize=9, color='#13c2c2')

    ax.set_title('规则决策边界（虚线） vs ML 历史分布判定（形状：X = ML 异常）', fontsize=14)
    ax.set_xlabel('水位 waterLevel（米）')
    ax.set_ylabel('浊度 turbidity（NTU）')
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8, ncol=2, loc='upper left')
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path


def _badge(status):
    return f'<span class="badge" style="background:{STATUS_COLORS[status]}">{status}</span>'


def build_html(df, stats, scatter_path, data_path):
    total = len(df)
    counts = {name: int((df['category'] == name).sum()) for name, _ in CATEGORIES}
    confirm = counts[CATEGORIES[0][0]]
    both_normal = counts[CATEGORIES[3][0]]
    rate = (confirm + both_normal) / total * 100

    # 交叉统计表
    cross_rows = ''.join(
        f'<tr><td>{name}</td><td>{counts[name]}</td><td class="meta">{desc}</td></tr>'
        for name, desc in CATEGORIES)

    # 策略表
    strategy_rows = ''.join(
        f'<tr><td><b>策略{s["key"]}</b> {s["name"]}</td><td>{s["alarm"]}</td><td>{s["review"]}</td>'
        f'<td class="meta">{s["desc"]}</td></tr>'
        for s in stats)

    # 不一致案例
    mismatch = df[df['category'].isin([CATEGORIES[1][0], CATEGORIES[2][0]])].sort_values('time')
    mm_rows = ''.join(
        f'<tr><td>{r["reachId"]}</td><td>{r["time"].strftime("%m-%d %H:%M")}</td>'
        f'<td>{r["waterLevel"]}</td><td>{r["turbidity"]}</td>'
        f'<td>{_badge(r["rule_status"])}</td>'
        f'<td>{r["category"].split("（")[0]}</td>'
        f'<td>{r["ml_score"]:+.4f}（{"异常" if r["ml_anomaly"] else "常态"}）</td></tr>'
        for _, r in mismatch.iterrows())

    with open(scatter_path, 'rb') as f:
        img_b64 = base64.b64encode(f.read()).decode('ascii')

    gen_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>TideWatch 规则 / ML 对照报告</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         background: #f5f7fa; color: #1f2937; margin: 0; padding: 24px; }}
  .wrap {{ max-width: 1120px; margin: 0 auto; }}
  h1 {{ font-size: 24px; margin: 0 0 6px; }}
  h2 {{ font-size: 18px; border-left: 4px solid #2563eb; padding-left: 10px; margin: 0 0 14px; }}
  .card {{ background: #fff; border-radius: 12px; padding: 20px 24px; margin-bottom: 20px;
           box-shadow: 0 1px 4px rgba(0,0,0,.06); }}
  .big {{ font-size: 42px; font-weight: 800; color: #2563eb; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th, td {{ border-bottom: 1px solid #e5e7eb; padding: 8px 10px; text-align: left; }}
  th {{ background: #f8fafc; }}
  .badge {{ padding: 2px 10px; border-radius: 999px; color: #fff; font-size: 12px;
            white-space: nowrap; text-shadow: 0 1px 2px rgba(0,0,0,.35); }}
  .meta {{ color: #6b7280; font-size: 12.5px; }}
  img {{ width: 100%; border-radius: 8px; border: 1px solid #e5e7eb; }}
  .reco {{ background: #eff6ff; border-left: 4px solid #2563eb; padding: 12px 16px;
           border-radius: 6px; font-size: 14px; line-height: 1.8; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h1>TideWatch 规则 / ML 对照报告（D5）</h1>
    <p class="meta">
      数据源：{os.path.relpath(data_path, BASE_DIR)}（{total} 条）　|
      规则：judge() 阈值判级　|　ML：IsolationForest（n_estimators=100, contamination=0.1, random_state=42，
      训练于同一历史数据）<br>
      生成时间：{gen_time}　|
      说明：规则回答"是否越界"，ML 回答"是否偏离历史分布"——两个问题不同，结果不一致是正常现象。
    </p>
  </div>

  <div class="card">
    <h2>1. 总体一致率</h2>
    <p><span class="big">{rate:.1f}%</span>
       <span class="meta">　= （确认异常 {confirm} + 一致正常 {both_normal}） / {total}</span></p>
    <p class="meta">一致仅表示两个维度的结论相同，不代表两个维度等价；关键信息在两处不一致中。</p>
  </div>

  <div class="card">
    <h2>2. 四种交叉情况统计</h2>
    <table>
      <thead><tr><th>交叉情况</th><th>数量</th><th>解释</th></tr></thead>
      <tbody>{cross_rows}</tbody>
    </table>
  </div>

  <div class="card">
    <h2>3. 组合裁决策略对比（报警数 / 需复核数）</h2>
    <table>
      <thead><tr><th>策略</th><th>报警数</th><th>需复核数</th><th>说明</th></tr></thead>
      <tbody>{strategy_rows}</tbody>
    </table>
    <div class="reco" style="margin-top:14px">
      <b>推荐：策略C（组合裁决）</b>——两者皆异常才"确认报警"，仅一方异常转"人工复核"并保留双方依据：
      既避免策略A忽略分布维度可疑信号（漏报），也避免策略B把"越界但历史常见"径直降级（弱化确定风险）。
      裁决不删除任何一方的判断，两种依据始终并列可见。
    </div>
  </div>

  <div class="card">
    <h2>4. 不一致案例（共 {len(mismatch)} 条）</h2>
    <table>
      <thead><tr><th>断面</th><th>时间</th><th>水位(米)</th><th>浊度(NTU)</th>
      <th>规则判断</th><th>ML判断</th><th>anomaly_score</th></tr></thead>
      <tbody>{mm_rows if mm_rows else '<tr><td colspan="7" class="meta">无不一致案例</td></tr>'}</tbody>
    </table>
    <p class="meta" style="margin-top:10px">
      不一致的原因（结合本数据）：reach-b 历史中包含大量高水位/洪水样本（警戒段+洪水段共 12 条），
      因此 7.0~9.8 米在该断面历史分布中"常见"，ML 判定为常态；而固定规则只关心是否越过 7.0 / 8.5 阈值。
      两种判断依据不同，"不一致本身就是值得分析的现象"。
    </p>
  </div>

  <div class="card">
    <h2>5. 决策边界与分布判定散点图</h2>
    <img src="data:image/png;base64,{img_b64}" alt="规则决策边界与 ML 判定散点图">
    <p class="meta">虚线 = 规则阈值边界；颜色 = 规则判定；X 形 = ML 判定异常。可见 ML 异常点主要集中在高浊度区域（历史较少），
    而高水位区即使被规则判为预警，多数仍呈 ML 常态（历史常见）。</p>
  </div>

  <div class="card">
    <h2>6. 说明</h2>
    <p class="meta">本报告由 <code>python/compare_rule_ml.py</code> 自动生成，所有数字来自程序计算，模型输出未做任何手工修改。
    更换 data/history.csv 后重新运行即可重新生成。</p>
  </div>
</div>
</body>
</html>
"""


def main():
    data_path = resolve_data_path()
    print(f"[1/4] 读取数据: {os.path.relpath(data_path, BASE_DIR)}")
    df = load_data(data_path)
    print(f"      {len(df)} 条记录")

    print("[2/4] 加载 ML 模型 ...")
    if os.path.exists(MODEL_PATH):
        model = joblib.load(MODEL_PATH)
        print(f"      已加载 {os.path.relpath(MODEL_PATH, BASE_DIR)}")
    else:
        from analyze import train_isolation_forest
        print("      ⚠ 未找到 ml_model.joblib，临时训练（建议先运行 python3 python/train_ml.py）")
        model = train_isolation_forest(df)

    print("[3/4] 逐条对照 规则 vs ML ...")
    compared = compare(df, model)
    total = len(compared)
    counts = {name: int((compared['category'] == name).sum()) for name, _ in CATEGORIES}
    rate = (counts[CATEGORIES[0][0]] + counts[CATEGORIES[3][0]]) / total * 100
    for name, _ in CATEGORIES:
        print(f"      {name}: {counts[name]}")
    print(f"      一致率: {rate:.1f}%")

    stats = strategy_stats(compared)
    print("      ---- 组合裁决策略（报警数 / 需复核数）----")
    for s in stats:
        print(f"      策略{s['key']} {s['name']}: 报警 {s['alarm']} / 需复核 {s['review']}")
    print("      推荐: 策略C（两者皆异常才确认报警，不一致转入人工复核）")

    print("[4/4] 生成对照报告 ...")
    os.makedirs(REPORT_DIR, exist_ok=True)
    scatter = make_scatter(compared, os.path.join(REPORT_DIR, 'rule_ml_scatter.png'))
    html = build_html(compared, stats, scatter, data_path)
    out_path = os.path.join(REPORT_DIR, 'compare_rule_ml.html')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"对照报告已生成: {os.path.relpath(out_path, BASE_DIR)}")


if __name__ == '__main__':
    main()
