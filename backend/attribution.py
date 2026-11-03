"""Deterministic, offline source screening for fixed simulated snapshots.

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
    """Screen supplied evidence without scenario-id shortcuts or global state.

    Missing/stale upstream observations remain unexcluded; manual local records
    remain pending verification. No history store or late-data replay is used.
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
    if chain[-1] not in observations:
        raise ValueError('Target observation is required')
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
    settings = scenario['attribution_config']
    cutoff = _time(scenario['analysis_time'])
    max_age = settings.get('max_sample_age_minutes')
    local_window = settings.get('local_evidence_window_minutes', 35)
    for name, value in [('max_sample_age_minutes', max_age),
                        ('local_evidence_window_minutes', local_window)]:
        if name == 'local_evidence_window_minutes' and value is None:
            raise ValueError(f'{name} must be a finite positive number')
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or value <= 0):
            raise ValueError(f'{name} must be a finite positive number')
    health = {}
    for reach in chain:
        observation = observations.get(reach)
        age = ((cutoff - _time(observation['event_time'])).total_seconds() / 60
               if observation else None)
        health[reach] = {'status': 'missing' if observation is None else
                         'stale' if max_age is not None and age > max_age else 'fresh',
                         'age_minutes': age}
    if health[chain[-1]]['status'] != 'fresh':
        raise ValueError('Target observation is stale')
    missing = [r for r in chain[:-1] if health[r]['status'] != 'fresh']
    local_records = scenario['local_records']
    for record in local_records:
        if _time(record['event_time']) > cutoff:
            raise ValueError('Local evidence is outside the analysis snapshot')
        if record['source'] == 'manual' and not record.get('entered_by'):
            raise ValueError('Manual local evidence requires entered_by')

    def evidence(reach):
        found = []
        for record in local_records:
            lag = (_time(target['event_time']) - _time(record['event_time'])).total_seconds() / 60
            if (record['reach_id'] == reach and record.get('metric', metric) == metric
                    and 0 <= lag <= local_window):
                item = dict(record)
                item.setdefault('verification_status', 'pending')
                item['evidence_strength'] = ('中等' if item['verification_status'] == 'verified'
                                              else '待核实')
                found.append(item)
        return sorted(found, key=lambda r: json.dumps(r, sort_keys=True, ensure_ascii=False))

    def local_support(records):
        return max((1.0 if r['verification_status'] == 'verified' else 0.5
                    for r in records), default=0.0)

    largest_change = max(r['absolute_change'] for reach, r in observations.items()
                         if health[reach]['status'] == 'fresh')
    candidates = []
    for source_index in range(target_index - 1, -1, -1):
        reach = chain[source_index]
        observation = observations.get(reach)
        if reach in missing:
            candidates.append({
                'source_id': reach, 'source_type': 'upstream',
                'event_time': observation['event_time'] if observation else None,
                'time_lag_minutes': None, 'propagation_window': _window(scenario, reach, chain[-1]),
                'absolute_change': None, 'topology_distance': target_index - source_index,
                'local_evidence': evidence(reach), 'ranking_score': 0.0,
                'evidence_strength': '缺证据', 'status': 'not_excluded',
                'role': 'missing_evidence', 'exclude_basis': [],
                'evidence_gaps': [f'{reach} 数据缺失或过期，不能把最后一次正常观测用于当前排除。'],
                'explanation': '未排除（缺证据）', 'data_health': health[reach],
            })
            continue
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
            'local_evidence': local_support(local),
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
        if any(upstream[r]['status'] != 'retained' for r in segment[:-1]):
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
    for candidate in candidates:
        if candidate['status'] == 'retained' and candidate['evidence_strength'] == '弱':
            candidate['evidence_gaps'].append('缺少连续的中间断面传播证据，不能单独确认上游来源。')
            if candidate['absolute_change'] < target['absolute_change']:
                candidate['evidence_gaps'].append('目标绝对变化量大于该上游断面，尚缺幅度放大的解释或本地侧核验。')
    # Local checks cover the target and intermediate reaches, without duplicating
    # the retained upstream origin as a separate local-source hypothesis.
    for index in range(1, len(chain)):
        reach = chain[index]
        local = evidence(reach)
        supported = reach in path[1:] and not local and not missing
        basis = []
        if supported:
            predecessor = chain[index - 1]
            lag = _lag(observations[predecessor], observations[reach])
            basis = [f'{predecessor}→{reach} 拓扑成立，事件时间差 {lag:g} 分钟在配置窗口内；'
                     f"绝对变化量 {observations[predecessor]['absolute_change']:g} → "
                     f"{observations[reach]['absolute_change']:g} 未放大。",
                     '本次固定模拟输入的 local_records 中无该断面独立本地记录；'
                     '仅在本场景证据范围内排除，不代表现实中不存在本地源。']
        is_source = not supported and (bool(local) or reach == chain[-1])
        record_lag = min(((_time(target['event_time']) - _time(r['event_time'])).total_seconds() / 60
                          for r in local), default=None)
        components = {
            'time': max(0.0, 1 - record_lag / local_window) if local else 0.0,
            'absolute_change': observations[reach]['absolute_change'] / largest_change
                               if reach not in missing else 0.0,
            'topology': 1 / (1 + target_index - index),
            'local_evidence': local_support(local),
        }
        local_gaps = [] if supported else [
            '本地记录仅为侧证据，不能单独确认来源。' if local else '缺少独立本地证据，不能确认或排除本地来源。']
        if local and any(r['verification_status'] != 'verified' for r in local):
            local_gaps.append('人工或模拟本地记录待核实；保留来源、录入人与核实状态，不等同于传感器证据。')
        candidates.append({
            'source_id': f'{reach}:local', 'source_type': 'local',
            'event_time': local[0]['event_time'] if local else
                          observations[reach]['event_time'] if reach in observations else None,
            'time_lag_minutes': record_lag,
            'propagation_window': None,
            'absolute_change': observations[reach]['absolute_change'] if reach not in missing else None,
            'topology_distance': target_index - index, 'local_evidence': local,
            'ranking_score': round(sum(weights[k] * components[k] for k in weights), 6) if is_source else 0.0,
            'score_components': components,
            'evidence_strength': '不支持' if supported else
                                 '中等' if local_support(local) == 1 else '待核实' if local else '弱',
            'status': 'excluded' if supported else 'retained' if is_source else 'needs_review',
            'role': 'independent_source' if is_source else 'local_check',
            'exclude_basis': basis, 'evidence_gaps': local_gaps,
        })
    candidates = rank_candidates(candidates)
    sources = [c for c in candidates if c['rank'] is not None]
    gaps = ['时间先后与幅度衰减仅支持关联，缺少独立采样及水动力验证，不能证明因果来源。']
    complete = (not missing and bool(path) and len(sources) == 1
                and sources[0]['source_id'] == path[0]
                and all(c['status'] == 'excluded' for c in candidates if c['source_type'] == 'local'))
    checks = []
    if missing:
        state = 'UNRESOLVED'
        conclusion = '来源未定｜关键上游 ' + '、'.join(missing) + ' 证据缺失'
        gaps.extend(f'{reach} 缺测或观测过期，未排除（缺证据）。' for reach in missing)
        checks.extend({'priority': i + 1, 'reach_id': reach, 'action': '优先恢复数据链并核对缺失时段观测。'}
                      for i, reach in enumerate(missing))
    elif complete:
        state = 'SUPPORTED_SINGLE'
        conclusion = f"证据支持单源{sources[0]['source_id']}（中等证据；非因果证明）"
    elif len(sources) >= 2 and any(c['local_evidence'] for c in sources if c['source_type'] == 'local'):
        state = 'MULTI_CANDIDATE'
        conclusion = '多源候选，来源未定；按相对证据排序保留 ' + '、'.join(c['source_id'] for c in sources)
    else:
        state = 'UNRESOLVED'
        conclusion = '当前证据不足以给出单源结论'
    if not complete:
        gaps.append('传播路径或本地排除证据不完整，不能沿用完整路径下的单源结论。')
        checks.extend([
            {'priority': len(checks) + 1, 'reach_id': chain[-1], 'action': '核查附近排口与施工记录，确认来源和录入信息。'},
            {'priority': len(checks) + 2, 'reach_id': chain[-1], 'action': '必要时人工采样，补充独立核验依据。'},
        ])
    for candidate in candidates:
        gaps.extend(candidate['evidence_gaps'])
    gaps = list(dict.fromkeys(gaps))
    return {
        'scenario_id': report['scenario_id'], 'seed': report['seed'],
        'analysis_time': report['analysis_time'], 'data_classification': report['data_classification'],
        'target_anomaly': target, 'candidates': candidates,
        'independent_sources': [c['source_id'] for c in sources],
        'propagation_path': [c for c in candidates if c['role'] == 'propagation_path'],
        'excluded': [c for c in candidates if c['status'] == 'excluded'],
        'conclusion': conclusion, 'status': state, 'evidence_gaps': gaps,
        'data_health': health, 'next_checks': checks,
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
