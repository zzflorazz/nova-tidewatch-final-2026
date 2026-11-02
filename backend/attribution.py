"""Deterministic, offline source screening for a complete fixed snapshot.

Reuses baseline.analyze_scenario; there is no alternate baseline algorithm.
Windows are inclusive event-time minutes. Scores sum configured weights times:
window midpoint fit (0..1), absolute change / largest observed absolute change,
1 / topology distance, and presence of local evidence. Scores are relative
ordering scores, never probabilities. Only independent, eligible sources rank.
A checked path can support medium evidence, never a proven causal source.
"""

import argparse
import json
import math
from datetime import datetime
from pathlib import Path

from .baseline import analyze_scenario


def _time(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Event times require a timezone')
    return result


def _lag(first, second):
    return (_time(second['event_time']) - _time(first['event_time'])).total_seconds() / 60


def _window(scenario, source, target):
    values = scenario['propagation_windows'][f'{source}->{target}']
    if len(values) != 2 or not all(math.isfinite(v) for v in values):
        raise ValueError('Propagation windows must contain two finite minutes')
    low, high = values
    if not 0 < low < high:
        raise ValueError('Propagation windows require 0 < min < max')
    return {'min_minutes': low, 'max_minutes': high}


def _within(lag, window):
    return window['min_minutes'] <= lag <= window['max_minutes']


def rank_candidates(candidates):
    """Canonical display order; only independent sources receive a source rank."""
    ordered = sorted((dict(c) for c in candidates), key=lambda c: (
        0 if c['role'] == 'independent_source' and c['status'] == 'retained' else 1,
        -c['ranking_score'], c['source_id']))
    rank = 0
    for item in ordered:
        item['rank'] = None
        if item['role'] == 'independent_source' and item['status'] == 'retained':
            rank += 1
            item['rank'] = rank
    return ordered


def analyze_attribution(scenario):
    """Screen a complete chain snapshot without scenario-id shortcuts or state.

    Multiple observations for one reach/metric are rejected in this phase;
    selecting episodes, missing-data recovery and late samples are out of scope.
    Local records block a no-independent-evidence exclusion; this phase does
    not resolve competing local sources.
    """
    report = analyze_scenario(scenario)
    target_spec = scenario['target']
    metric = target_spec['metric']
    topology = scenario['topology']
    if len(topology) != len(set(topology)):
        raise ValueError('Topology must be a chain of unique reaches')
    target_index = topology.index(target_spec['reach_id'])
    chain = topology[:target_index + 1]
    observations = {}
    for result in report['results']:
        if result['metric'] == metric and result['reach_id'] in chain:
            if result['reach_id'] in observations:
                raise ValueError('Expected one current observation per reach/metric')
            observations[result['reach_id']] = result
    if set(observations) != set(chain):
        raise ValueError('This phase requires a complete chain snapshot')
    target = observations[chain[-1]]
    if _time(target['event_time']) != _time(target_spec['event_time']) or not target['is_anomaly']:
        raise ValueError('Target must identify a computed active anomaly')
    weights = scenario['attribution_config']['ranking_weights']
    expected = {'time', 'absolute_change', 'topology', 'local_evidence'}
    if set(weights) != expected or any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
        for v in weights.values()
    ) or sum(weights.values()) == 0:
        raise ValueError('Ranking weights must be finite nonnegative values with positive sum')
    local_records = scenario['local_records']
    for record in local_records:
        if _time(record['event_time']) > _time(scenario['analysis_time']):
            raise ValueError('Local evidence is outside the analysis snapshot')

    def evidence(reach):
        return sorted((dict(r) for r in local_records if r['reach_id'] == reach),
                      key=lambda r: json.dumps(r, sort_keys=True, ensure_ascii=False))

    largest_change = max(r['absolute_change'] for r in observations.values())
    candidates = []
    for source_index in range(target_index - 1, -1, -1):
        reach = chain[source_index]
        observation = observations[reach]
        lag = _lag(observation, target)
        window = _window(scenario, reach, target['reach_id'])
        distance = target_index - source_index
        local = evidence(reach)
        basis = []
        if not observation['is_anomaly']:
            basis.append('该断面按自身中位数与 IQR 计算无活动异常。')
        if not _within(lag, window):
            basis.append(f'事件时间差 {lag:g} 分钟不在传播窗口 '
                         f"{window['min_minutes']}–{window['max_minutes']} 分钟内。")
        if observation['deviation'] * target['deviation'] <= 0:
            basis.append('上游与目标相对自身基线的变化方向不一致。')
        midpoint = (window['min_minutes'] + window['max_minutes']) / 2
        halfwidth = (window['max_minutes'] - window['min_minutes']) / 2
        components = {
            'time': max(0.0, 1 - abs(lag - midpoint) / halfwidth),
            'absolute_change': observation['absolute_change'] / largest_change,
            'topology': 1 / distance,
            'local_evidence': float(bool(local)),
        }
        candidates.append({
            'source_id': reach, 'source_type': 'upstream',
            'event_time': observation['event_time'], 'time_lag_minutes': lag,
            'propagation_window': window, 'absolute_change': observation['absolute_change'],
            'topology_distance': distance, 'local_evidence': local,
            'ranking_score': 0.0 if basis else round(sum(weights[k] * components[k] for k in weights), 6),
            'score_components': components,
            'evidence_strength': '不支持' if basis else '弱',
            'status': 'excluded' if basis else 'retained',
            'role': 'excluded' if basis else 'independent_source',
            'exclude_basis': basis, 'evidence_gaps': [],
        })
    # Find complete paths by topology, never by input or score order.
    upstream = {c['source_id']: c for c in candidates}
    paths = []
    for origin_index, origin_reach in enumerate(chain[:-1]):
        origin = upstream[origin_reach]
        if origin['status'] != 'retained':
            continue
        segment = chain[origin_index:]
        if any(upstream[r]['status'] == 'excluded' for r in segment[:-1]):
            continue
        valid = True
        for left, right in zip(segment, segment[1:]):
            a, b = observations[left], observations[right]
            if (not _within(_lag(a, b), _window(scenario, left, right))
                    or a['absolute_change'] < b['absolute_change']
                    or a['deviation'] * b['deviation'] <= 0):
                valid = False
        if valid and not any(evidence(r) for r in segment[1:]):
            paths.append(segment)
    # The earliest topological origin of a checked path absorbs intermediate reaches.
    path = paths[0] if paths else []
    if path:
        upstream[path[0]]['evidence_strength'] = '中等'
        for reach in path[1:-1]:
            item = upstream[reach]
            item.update(role='propagation_path', status='propagation', ranking_score=0.0,
                        evidence_strength='中等',
                        explanation=f'证据支持 {reach} 为 {path[0]} 异常向下游传播的经过断面，'
                                    '不作为第二个独立来源；这不是因果证明。')
    # Local checks cover the target and intermediate reaches, without duplicating
    # the retained upstream origin as a separate local-source hypothesis.
    for index in range(1, len(chain)):
        reach = chain[index]
        local = evidence(reach)
        supported = reach in path[1:] and not local
        basis = []
        if supported:
            predecessor = chain[index - 1]
            lag = _lag(observations[predecessor], observations[reach])
            basis = [f'{predecessor}→{reach} 拓扑成立，事件时间差 {lag:g} 分钟在配置窗口内；'
                     f"绝对变化量 {observations[predecessor]['absolute_change']:g} → "
                     f"{observations[reach]['absolute_change']:g} 未放大。",
                     '本次固定模拟输入的 local_records 中无该断面独立本地记录；'
                     '仅在本场景证据范围内排除，不代表现实中不存在本地源。']
        candidates.append({
            'source_id': f'{reach}:local', 'source_type': 'local',
            'event_time': observations[reach]['event_time'], 'time_lag_minutes': None,
            'propagation_window': None, 'absolute_change': observations[reach]['absolute_change'],
            'topology_distance': target_index - index, 'local_evidence': local,
            'ranking_score': 0.0, 'evidence_strength': '不支持' if supported else '待核实',
            'status': 'excluded' if supported else 'needs_review', 'role': 'local_check',
            'exclude_basis': basis,
            'evidence_gaps': [] if supported else ['缺少足够的路径支持或存在独立本地证据，本阶段不裁定本地来源。'],
        })
    candidates = rank_candidates(candidates)
    sources = [c for c in candidates if c['rank'] is not None]
    gaps = ['时间先后与幅度衰减仅支持关联，缺少独立采样及水动力验证，不能证明因果来源。']
    complete = bool(path) and len(sources) == 1 and sources[0]['source_id'] == path[0]
    if not complete:
        gaps.append('传播路径或本地排除证据不完整，不能沿用完整路径下的单源结论。')
    conclusion = (f"证据支持单源{sources[0]['source_id']}（中等证据；非因果证明）" if complete
                  else '当前证据不足以给出单源结论')
    return {
        'scenario_id': report['scenario_id'], 'seed': report['seed'],
        'analysis_time': report['analysis_time'], 'data_classification': report['data_classification'],
        'target_anomaly': target, 'candidates': candidates,
        'independent_sources': [c['source_id'] for c in sources],
        'propagation_path': [c for c in candidates if c['role'] == 'propagation_path'],
        'excluded': [c for c in candidates if c['status'] == 'excluded'],
        'conclusion': conclusion, 'evidence_gaps': gaps,
        'ranking_weights': dict(weights), 'ranking_score_meaning': '相对排序分，不是概率',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenarios', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'data/trace_scenarios.json')
    parser.add_argument('--scenario', default='S2')
    args = parser.parse_args()
    scenarios = json.loads(args.scenarios.read_text(encoding='utf-8'))['scenarios']
    scenario = next((s for s in scenarios if s['scenario_id'] == args.scenario), None)
    if scenario is None:
        parser.error(f'Unknown scenario: {args.scenario}')
    try:
        result = analyze_attribution(scenario)
    except (ValueError, KeyError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
