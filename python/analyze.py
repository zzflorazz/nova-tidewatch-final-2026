#!/usr/bin/env python3
# TideWatch 离线分析链（数据链 A）
# 断面采集/历史记录 → CSV → pandas 时空统计 / 趋势 → IsolationForest 轻量ML → report/report.html
#
# 用法:
#   python3 python/analyze.py            # 直接运行生成报告
#   python3 python/generate_report.py    # 等价的一键入口

import base64
import os
import sys
from datetime import datetime

import matplotlib
matplotlib.use('Agg')  # 无界面后端，保证终端环境也能出图
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

# 中文字体（macOS 常见中文字体），避免图表出现方框
matplotlib.rcParams['font.sans-serif'] = ['PingFang SC', 'Heiti SC', 'Arial Unicode MS', 'SimHei']
matplotlib.rcParams['axes.unicode_minus'] = False

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rules import judge, STATUS_COLORS

# ===== 路径 =====
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(BASE_DIR, 'data', 'history.csv')
REPORT_DIR = os.path.join(BASE_DIR, 'report')

# ===== 统一约定 =====
REACH_IDS = ['reach-a', 'reach-b', 'reach-c']
REACH_NAMES = {'reach-a': '上游', 'reach-b': '中游', 'reach-c': '下游'}
STATUS_ORDER = ['正常', '高水位警戒', '洪水预警', '水质浑浊']


# ===================== 数据读取 =====================

def resolve_data_path(path=None):
    """解析数据文件路径：显式路径 > data/history.csv > data/history_example.csv（回退）
    回退机制保证克隆仓库后无需手工准备数据即可复现分析链。"""
    if path:
        return path
    if os.path.exists(DATA_FILE):
        return DATA_FILE
    example = os.path.join(BASE_DIR, 'data', 'history_example.csv')
    if os.path.exists(example):
        print("⚠ 未找到 data/history.csv，回退使用 data/history_example.csv")
        return example
    return DATA_FILE  # 两者都不存在，交给 pd.read_csv 抛出清晰错误


def load_data(path=None):
    """读取历史数据 CSV（time 解析为 datetime），按断面与时间排序"""
    path = resolve_data_path(path)
    df = pd.read_csv(path, parse_dates=['time'])
    df = df.sort_values(['reachId', 'time']).reset_index(drop=True)
    return df


# ===================== 时空统计 =====================

def validate_status(df):
    """用统一判级规则对 CSV 中的 status 做二次校验，返回不一致记录列表"""
    mismatches = []
    for _, row in df.iterrows():
        expected = judge(row['waterLevel'], row['turbidity'])['status']
        if expected != row['status']:
            mismatches.append({
                'reachId': row['reachId'], 'time': row['time'],
                'waterLevel': row['waterLevel'], 'turbidity': row['turbidity'],
                'csv_status': row['status'], 'expected': expected,
            })
    return mismatches


def compute_stats(df):
    """时空统计：每断面水位/浊度统计、状态分布、异常记录列表"""
    g = df.groupby('reachId')
    reach_stats = pd.DataFrame({
        'waterLevel_mean': g['waterLevel'].mean().round(2),
        'waterLevel_max': g['waterLevel'].max(),
        'waterLevel_min': g['waterLevel'].min(),
        'turbidity_mean': g['turbidity'].mean().round(1),
        'turbidity_max': g['turbidity'].max(),
        'turbidity_min': g['turbidity'].min(),
        'records': g.size(),
    }).reindex(REACH_IDS)

    status_counts = pd.crosstab(df['reachId'], df['status']).reindex(
        index=REACH_IDS, columns=STATUS_ORDER, fill_value=0)

    return {
        'reach_stats': reach_stats,
        'status_counts': status_counts,
        'abnormal': df[df['status'] != '正常'].copy(),
        'mismatches': validate_status(df),
    }


# ===================== 趋势图 =====================

def _plot_trend(df, value_col, ylabel, title, out_path):
    """三断面某指标的时间序列折线图"""
    fig, ax = plt.subplots(figsize=(10, 5))
    for rid in REACH_IDS:
        sub = df[df['reachId'] == rid]
        ax.plot(sub['time'], sub[value_col], marker='o', markersize=4,
                linewidth=1.5, label=f"{rid}（{REACH_NAMES[rid]}）")
    ax.set_title(title, fontsize=14)
    ax.set_xlabel('时间')
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path


def plot_water_level_trend(df, out_path):
    t0 = df['time'].min().strftime('%Y-%m-%d')
    t1 = df['time'].max().strftime('%Y-%m-%d')
    return _plot_trend(df, 'waterLevel', '水位（米）',
                       f'三断面水位时间序列（{t0} ~ {t1}）', out_path)


def plot_turbidity_trend(df, out_path):
    t0 = df['time'].min().strftime('%Y-%m-%d')
    t1 = df['time'].max().strftime('%Y-%m-%d')
    return _plot_trend(df, 'turbidity', '浊度（NTU）',
                       f'三断面浊度时间序列（{t0} ~ {t1}）', out_path)


def plot_status_distribution(df, out_path):
    """各状态出现次数分布柱状图（按断面分组）"""
    ct = pd.crosstab(df['reachId'], df['status']).reindex(
        index=REACH_IDS, columns=STATUS_ORDER, fill_value=0)
    x = np.arange(len(REACH_IDS))
    width = 0.2
    fig, ax = plt.subplots(figsize=(9, 5))
    for i, st in enumerate(STATUS_ORDER):
        bars = ax.bar(x + (i - 1.5) * width, ct[st].values, width,
                      label=st, color=STATUS_COLORS[st])
        ax.bar_label(bars, fontsize=9)
    ax.set_title('三断面状态分布', fontsize=14)
    ax.set_xlabel('断面')
    ax.set_ylabel('记录条数')
    ax.set_xticks(x)
    ax.set_xticklabels([f"{rid}\n（{REACH_NAMES[rid]}）" for rid in REACH_IDS])
    ax.legend(title='状态')
    ax.grid(True, axis='y', alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path


# ===================== 轻量ML：IsolationForest =====================

def train_isolation_forest(df, contamination=0.1, random_state=42):
    """基于历史 waterLevel / turbidity 训练 IsolationForest"""
    X = df[['waterLevel', 'turbidity']].to_numpy(dtype=float)
    model = IsolationForest(n_estimators=100, contamination=contamination,
                            random_state=random_state)
    model.fit(X)
    return model


def score_anomalies(model, df):
    """对每条数据输出 anomaly_score（越小越异常）和 is_anomaly"""
    X = df[['waterLevel', 'turbidity']].to_numpy(dtype=float)
    out = df.copy()
    out['anomaly_score'] = model.decision_function(X).round(4)
    out['is_anomaly'] = model.predict(X) == -1
    return out


# ===================== report.html =====================

def _img_tag(path, alt):
    with open(path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('ascii')
    return f'<img src="data:image/png;base64,{b64}" alt="{alt}">'


def _badge(status):
    return f'<span class="badge" style="background:{STATUS_COLORS[status]}">{status}</span>'


def _table(headers, rows):
    th = ''.join(f'<th>{h}</th>' for h in headers)
    trs = ''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in r) + '</tr>' for r in rows)
    return f'<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'


def build_html(df, stats, ml_df, chart_paths, data_path):
    gen_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    t0 = df['time'].min().strftime('%Y-%m-%d %H:%M')
    t1 = df['time'].max().strftime('%Y-%m-%d %H:%M')

    # --- 断面统计表 ---
    rs_rows = []
    for rid in REACH_IDS:
        r = stats['reach_stats'].loc[rid]
        rs_rows.append([f"{rid}（{REACH_NAMES[rid]}）",
                        r['waterLevel_mean'], r['waterLevel_max'], r['waterLevel_min'],
                        r['turbidity_mean'], r['turbidity_max'], r['turbidity_min'],
                        int(r['records'])])
    rs_table = _table(
        ['断面', '水位均值(米)', '水位最高', '水位最低', '浊度均值(NTU)', '浊度最高', '浊度最低', '记录数'],
        rs_rows)

    # --- 状态分布表 ---
    sc_rows = []
    for rid in REACH_IDS:
        row = stats['status_counts'].loc[rid]
        sc_rows.append([f"{rid}（{REACH_NAMES[rid]}）"] + [int(row[st]) for st in STATUS_ORDER])
    sc_table = _table(['断面'] + STATUS_ORDER, sc_rows)

    # --- 异常记录时间线 ---
    ab = stats['abnormal'].sort_values('time')
    ab_rows = []
    for _, r in ab.iterrows():
        advice = judge(r['waterLevel'], r['turbidity'])['advice']
        ab_rows.append([r['reachId'], r['time'].strftime('%Y-%m-%d %H:%M'),
                        r['waterLevel'], r['turbidity'], _badge(r['status']), advice])
    ab_table = _table(['断面', '时间', '水位(米)', '浊度(NTU)', '状态', '处置建议'], ab_rows) \
        if ab_rows else '<p class="meta">无异常记录</p>'

    # --- ML 异常检测摘要 ---
    anomalies = ml_df[ml_df['is_anomaly']].sort_values('anomaly_score')
    ml_rows = []
    for _, r in anomalies.iterrows():
        ml_rows.append([r['reachId'], r['time'].strftime('%Y-%m-%d %H:%M'),
                        r['waterLevel'], r['turbidity'], _badge(r['status']),
                        f"{r['anomaly_score']:.4f}"])
    ml_table = _table(
        ['断面', '时间', '水位(米)', '浊度(NTU)', '固定规则状态', 'anomaly_score'],
        ml_rows) if ml_rows else '<p class="meta">ML 未标记异常</p>'

    # --- 判级二次校验 ---
    n_mis = len(stats['mismatches'])
    if n_mis == 0:
        check_html = f'<p class="ok">✓ 已用统一判级规则对全部 {len(df)} 条记录二次校验：一致 {len(df)} 条，不一致 0 条</p>'
    else:
        mis_rows = [[m['reachId'], m['time'], m['waterLevel'], m['turbidity'],
                     m['csv_status'], m['expected']] for m in stats['mismatches']]
        check_html = f'<p style="color:#dc2626">⚠ 判级二次校验发现 {n_mis} 条不一致：</p>' + \
            _table(['断面', '时间', '水位', '浊度', 'CSV状态', '规则计算'], mis_rows)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>TideWatch 离线分析报告</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         background: #f5f7fa; color: #1f2937; margin: 0; padding: 24px; }}
  .wrap {{ max-width: 1080px; margin: 0 auto; }}
  h1 {{ font-size: 24px; margin: 0 0 6px; }}
  .card {{ background: #fff; border-radius: 12px; padding: 20px 24px;
           margin-bottom: 20px; box-shadow: 0 1px 4px rgba(0,0,0,.06); }}
  h2 {{ font-size: 18px; border-left: 4px solid #2563eb; padding-left: 10px; margin: 0 0 14px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th, td {{ border-bottom: 1px solid #e5e7eb; padding: 8px 10px; text-align: center; }}
  th {{ background: #f8fafc; color: #374151; }}
  tr:hover td {{ background: #f9fafb; }}
  .badge {{ padding: 2px 10px; border-radius: 999px; color: #fff; font-size: 12px; white-space: nowrap; }}
  img {{ width: 100%; border-radius: 8px; border: 1px solid #e5e7eb; }}
  .meta {{ color: #6b7280; font-size: 13px; }}
  .note {{ background: #eff6ff; border-left: 4px solid #2563eb; padding: 10px 14px;
           border-radius: 6px; font-size: 13px; margin-top: 14px; }}
  .ok {{ color: #16a34a; font-weight: 600; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h1>TideWatch 离线分析报告</h1>
    <p class="meta">
      数据链 A：断面历史记录 → CSV → Python（pandas / matplotlib / IsolationForest）→ report.html<br>
      数据源：{os.path.relpath(data_path, BASE_DIR)}　|　记录数：{len(df)}　|　时间范围：{t0} ~ {t1}　|　生成时间：{gen_time}
    </p>
  </div>

  <div class="card">
    <h2>1. 时空统计摘要</h2>
    {rs_table}
    <div class="note">{check_html}</div>
  </div>

  <div class="card">
    <h2>2. 各断面状态分布</h2>
    {sc_table}
  </div>

  <div class="card">
    <h2>3. 趋势图</h2>
    {_img_tag(chart_paths['water'], '三断面水位时间序列')}
    <p class="meta">图 1　三断面水位时间序列</p>
    {_img_tag(chart_paths['turbidity'], '三断面浊度时间序列')}
    <p class="meta">图 2　三断面浊度时间序列</p>
    {_img_tag(chart_paths['status'], '三断面状态分布')}
    <p class="meta">图 3　三断面状态分布</p>
  </div>

  <div class="card">
    <h2>4. 异常记录时间线（status ≠ 正常）</h2>
    <p class="meta">共 {len(ab)} 条异常记录</p>
    {ab_table}
  </div>

  <div class="card">
    <h2>5. ML 异常检测摘要（IsolationForest）</h2>
    <p class="meta">
      特征：waterLevel + turbidity　|　n_estimators=100　|　contamination=0.1　|　random_state=42<br>
      ML 标记异常：{int(ml_df['is_anomaly'].sum())} / {len(ml_df)} 条（anomaly_score 越小越异常）<br>
      说明：ML 判断“与历史分布是否不同”，与固定阈值规则的依据不同，两者并列展示。
    </p>
    {ml_table}
  </div>

  <div class="card">
    <h2>6. 报告说明</h2>
    <p class="meta">
      本报告由 <code>python/analyze.py</code> 自动生成，所有数字来自程序计算，无手工修改。<br>
      更换 <code>data/history.csv</code> 后重新运行 <code>python3 python/generate_report.py</code> 即可重新生成。
    </p>
  </div>
</div>
</body>
</html>
"""


# ===================== 主流程 =====================

def generate_report(data_path=None, report_dir=None):
    """一键：读取 CSV → 统计分析 → 出图 → ML → report.html"""
    data_path = resolve_data_path(data_path)
    report_dir = report_dir or REPORT_DIR
    os.makedirs(report_dir, exist_ok=True)

    print(f"[1/5] 读取数据: {os.path.relpath(data_path, BASE_DIR)}")
    df = load_data(data_path)
    print(f"      {len(df)} 条记录，{df['reachId'].nunique()} 个断面")

    print("[2/5] 时空统计 + 判级二次校验 ...")
    stats = compute_stats(df)
    print(f"      异常记录 {len(stats['abnormal'])} 条；"
          f"判级二次校验不一致 {len(stats['mismatches'])} 条")

    print("[3/5] 生成趋势图 ...")
    chart_paths = {
        'water': plot_water_level_trend(df, os.path.join(report_dir, 'water_level_trend.png')),
        'turbidity': plot_turbidity_trend(df, os.path.join(report_dir, 'turbidity_trend.png')),
        'status': plot_status_distribution(df, os.path.join(report_dir, 'status_distribution.png')),
    }

    print("[4/5] 训练 IsolationForest 并检测异常 ...")
    model = train_isolation_forest(df)
    ml_df = score_anomalies(model, df)
    print(f"      ML 标记异常 {int(ml_df['is_anomaly'].sum())}/{len(ml_df)} 条")

    print("[5/5] 生成 report.html ...")
    html = build_html(df, stats, ml_df, chart_paths, data_path)
    out_path = os.path.join(report_dir, 'report.html')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return out_path


if __name__ == '__main__':
    out = generate_report()
    print(f"报告已生成: {os.path.relpath(out, BASE_DIR)}")
