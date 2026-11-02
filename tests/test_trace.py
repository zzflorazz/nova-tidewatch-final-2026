"""Offline Trace baseline and attribution regression tests; no MQTT or global runtime state."""

import json
import math
from copy import deepcopy
from pathlib import Path

import pytest

from backend.baseline import analyze_scenario, calculate_baselines, evaluate_samples
from backend.attribution import analyze_attribution, rank_candidates


def load_s1():
    path = Path(__file__).resolve().parents[1] / "data/trace_scenarios.json"
    return json.loads(path.read_text(encoding="utf-8"))["scenarios"][0]


def sample(reach, metric, value):
    return {"reach_id": reach, "metric": metric, "value": value}


def test_baseline_s1():
    scenario = load_s1()
    original = deepcopy(scenario)
    report = analyze_scenario(scenario)
    assert scenario == original  # Pure calculation, including nested sample/config objects.
    assert report == analyze_scenario(scenario)
    assert report["status"] == "无活动异常"
    assert report["active_anomalies"] == []
    assert report["data_classification"] == "SIMULATED"
    assert len(report["results"]) == 6
    expected_keys = {(reach, metric) for reach in ("reach-a", "reach-b", "reach-c")
                     for metric in ("turbidity", "flow")}
    assert {(s["reach_id"], s["metric"]) for s in report["results"]} == expected_keys
    for result in report["results"]:
        history = [s["value"] for s in scenario["history_samples"]
                   if (s["reach_id"], s["metric"]) == (result["reach_id"], result["metric"])]
        assert len(history) == 9
        assert min(history) <= result["value"] <= max(history)
        assert result["q1"] <= result["value"] <= result["q3"]
        assert result["fallback"] is None
        assert result["deviation"] == pytest.approx(0.25)
    assert all(s["source"] == "simulated" for s in scenario["history_samples"] + scenario["current_samples"])


def test_baseline_reach_isolation():
    scenario = load_s1()
    before = analyze_scenario(scenario)
    changed = deepcopy(scenario)
    for s in changed["history_samples"]:
        if s["reach_id"] == "reach-a":
            s["value"] += 100
    after = analyze_scenario(changed)
    for old, new in zip(before["results"], after["results"]):
        if old["reach_id"] == "reach-a":
            assert new["median"] == pytest.approx(old["median"] + 100)
            assert new["is_anomaly"]
        else:
            assert new == old
    assert {s["reach_id"] for s in after["active_anomalies"]} == {"reach-a"}


def test_quartiles_and_metric_isolation():
    history = [sample("reach-a", "turbidity", value) for value in [0, 2, 4, 6]]
    history += [sample("reach-a", "flow", value) for value in [100, 100, 100]]
    baseline = calculate_baselines(history)
    assert baseline[("reach-a", "turbidity")] == {
        "sample_count": 4, "median": 3, "q1": 1.5, "q3": 4.5, "iqr": 3}
    assert baseline[("reach-a", "flow")]["median"] == 100


def test_zero_iqr_deterministic():
    history = [sample("reach-a", "flow", 2) for _ in range(5)]
    current = [sample("reach-a", "flow", value) for value in [2, 2.5, 1.5]]
    config = {"flow": {"deviation_threshold": 3, "min_noise": 0.1}}
    results = evaluate_samples(history, current, config)
    assert results == evaluate_samples(list(reversed(history)), current, config)
    assert [r["deviation"] for r in results] == [0, 5, -5]
    assert [r["is_anomaly"] for r in results] == [False, True, True]
    for result in results:
        assert result["iqr"] == 0
        assert result["effective_iqr"] == 0.1
        assert result["fallback"] == "minimum_noise"
        assert math.isfinite(result["deviation"])
    # A single historical sample follows the same documented deterministic rule.
    assert evaluate_samples(history[:1], current, config)[1]["deviation"] == 5


def test_absolute_change_not_baseline_multiple():
    history = [sample(reach, "turbidity", center + offset)
               for reach, center in [("reach-a", 2), ("reach-c", 100)]
               for offset in [-1, 0, 1]]
    current = [sample("reach-a", "turbidity", 5), sample("reach-c", "turbidity", 111),
               sample("reach-c", "turbidity", 89)]
    config = {"turbidity": {"deviation_threshold": 3, "min_noise": 0.1}}
    results = evaluate_samples(history, current, config)
    assert [r["absolute_change"] for r in results] == [3, 11, 11]
    # Relative multiples rank a above c; absolute changes correctly rank c above a.
    assert 5 / 2 > 111 / 100
    assert results[1]["absolute_change"] > results[0]["absolute_change"]
    assert results[2]["deviation"] < 0


def test_configurable_deviation_threshold_and_boundary():
    history = [sample("reach-a", "turbidity", value) for value in [9, 10, 11]]
    current = [sample("reach-a", "turbidity", value) for value in [13, 7, 13.1]]
    config = {"turbidity": {"deviation_threshold": 3, "min_noise": 0.1}}
    assert [r["is_anomaly"] for r in evaluate_samples(history, current, config)] == [False, False, True]
    config["turbidity"]["deviation_threshold"] = 2
    assert all(r["is_anomaly"] for r in evaluate_samples(history, current, config))


def test_missing_history_and_invalid_noise_are_not_normal():
    current = [sample("reach-a", "flow", 1)]
    config = {"flow": {"deviation_threshold": 3, "min_noise": 0.1}}
    with pytest.raises(ValueError, match="No historical baseline"):
        evaluate_samples([], current, config)
    config["flow"]["min_noise"] = 0
    with pytest.raises(ValueError, match="must be positive"):
        evaluate_samples(current, current, config)
    with pytest.raises(ValueError, match="finite number"):
        calculate_baselines([sample("reach-a", "flow", float("nan"))])


def test_analysis_snapshot_and_history_separation():
    scenario = load_s1()
    scenario["history_samples"][0]["event_time"] = scenario["current_samples"][0]["event_time"]
    with pytest.raises(ValueError, match="History must precede"):
        analyze_scenario(scenario)
    scenario = load_s1()
    scenario["current_samples"][0]["received_time"] = "2026-11-02T10:00:00+08:00"
    with pytest.raises(ValueError, match="outside the analysis snapshot"):
        analyze_scenario(scenario)


def test_other_scenarios_are_reserved():
    path = Path(__file__).resolve().parents[1] / "data/trace_scenarios.json"
    reserved = json.loads(path.read_text(encoding="utf-8"))["scenarios"][2:]
    assert [s["scenario_id"] for s in reserved] == ["S3", "S4", "S5"]
    for scenario in reserved:
        assert scenario["status"] == "reserved"
        assert scenario["history_samples"] == scenario["current_samples"] == []
        with pytest.raises(ValueError, match="reserved"):
            analyze_scenario(scenario)



def load_s2():
    path = Path(__file__).resolve().parents[1] / "data/trace_scenarios.json"
    return next(s for s in json.loads(path.read_text(encoding="utf-8"))["scenarios"]
                if s["scenario_id"] == "S2")


def by_source(report):
    return {candidate["source_id"]: candidate for candidate in report["candidates"]}


def test_single_source_s2():
    scenario = load_s2()
    report = analyze_attribution(scenario)
    assert report["independent_sources"] == ["reach-a"]
    assert report["conclusion"].startswith("证据支持单源reach-a")
    assert report["target_anomaly"]["reach_id"] == "reach-c"
    assert report["target_anomaly"]["is_anomaly"]
    assert report["data_classification"] == "SIMULATED"
    a = by_source(report)["reach-a"]
    assert a["evidence_strength"] == "中等"
    assert a["status"] == "retained"
    assert a["rank"] == 1
    assert a["ranking_score"] == 75
    assert a["score_components"] == {"time": 1, "absolute_change": 1,
                                      "topology": 0.5, "local_evidence": 0}
    required = {"source_id", "source_type", "event_time", "time_lag_minutes",
                "propagation_window", "absolute_change", "topology_distance",
                "local_evidence", "ranking_score", "evidence_strength", "status",
                "role", "exclude_basis", "evidence_gaps"}
    assert all(required <= c.keys() for c in report["candidates"])
    text = json.dumps(report, ensure_ascii=False)
    assert "A导致C" not in text and "已证明来源" not in text
    assert "不是概率" in report["ranking_score_meaning"]


def test_b_is_propagation_not_independent():
    report = analyze_attribution(load_s2())
    b = by_source(report)["reach-b"]
    assert b["role"] == "propagation_path"
    assert b["status"] == "propagation"
    assert b["rank"] is None
    assert b["ranking_score"] == 0
    assert "不作为第二个独立来源" in b["explanation"]
    assert [c["source_id"] for c in report["propagation_path"]] == ["reach-b"]
    assert "reach-b" not in report["independent_sources"]


@pytest.mark.parametrize("reach, event_time", [
    ("reach-a", "09:15:00"),  # A->C is only 10 minutes
    ("reach-a", "08:45:00"),  # A->C exceeds 35 minutes
    ("reach-b", "09:22:00"),  # B->C is only 3 minutes
    ("reach-b", "09:02:00"),  # B->C exceeds 20 minutes
])
def test_s2_time_windows(reach, event_time):
    scenario = load_s2()
    for s in scenario["current_samples"]:
        if s["reach_id"] == reach:
            s["event_time"] = f"2026-11-02T{event_time}+08:00"
    report = analyze_attribution(scenario)
    c = by_source(report)[reach]
    assert c["status"] == "excluded"
    assert c["rank"] is None and c["ranking_score"] == 0
    assert any("不在传播窗口" in basis for basis in c["exclude_basis"])
    assert by_source(report)["reach-b"]["role"] != "propagation_path"


def test_s2_a_to_b_window_also_required():
    scenario = load_s2()
    # A->C and B->C still pass, but A->B = 4 minutes fails [5,20].
    a = next(s for s in scenario["current_samples"] if s["reach_id"] == "reach-a")
    a["event_time"] = "2026-11-02T09:08:00+08:00"
    report = analyze_attribution(scenario)
    assert report["propagation_path"] == []
    assert by_source(report)["reach-b"]["role"] == "independent_source"
    assert "证据支持单源" not in report["conclusion"]


def test_s2_absolute_change_decay():
    scenario = load_s2()
    results = {r["reach_id"]: r for r in analyze_scenario(scenario)["results"]}
    assert [results[r]["median"] for r in scenario["topology"]] == [6, 12, 24]
    assert [results[r]["absolute_change"] for r in scenario["topology"]] == [74, 28, 1]
    assert all(r["is_anomaly"] and r["fallback"] is None for r in results.values())
    assert results["reach-c"]["deviation"] > 3
    assert results["reach-a"]["event_time"] < results["reach-b"]["event_time"] < results["reach-c"]["event_time"]
    # Actual amplitude growth at B must break propagation, despite valid times.
    next(s for s in scenario["current_samples"] if s["reach_id"] == "reach-b")["value"] = 100
    report = analyze_attribution(scenario)
    assert by_source(report)["reach-b"]["role"] != "propagation_path"
    assert "证据支持单源" not in report["conclusion"]


def test_s2_exclusion_basis_present():
    report = analyze_attribution(load_s2())
    excluded = {c["source_id"]: c for c in report["excluded"]}
    assert set(excluded) == {"reach-b:local", "reach-c:local"}
    for item in report["candidates"]:
        if item["status"] == "excluded":
            assert item["exclude_basis"]
            assert all(basis.strip() for basis in item["exclude_basis"])
            assert item["rank"] is None
    for item in excluded.values():
        assert any("事件时间差" in basis and "绝对变化量" in basis for basis in item["exclude_basis"])
        assert any("local_records" in basis for basis in item["exclude_basis"])


def test_s2_deterministic():
    scenario = load_s2()
    original = deepcopy(scenario)
    report = analyze_attribution(scenario)
    assert report == analyze_attribution(scenario)
    assert scenario == original
    reordered = deepcopy(scenario)
    reordered["history_samples"].reverse()
    reordered["current_samples"].reverse()
    assert analyze_attribution(reordered) == report
    assert rank_candidates(list(reversed(report["candidates"]))) == report["candidates"]
    # The scenario identifier is metadata, never the conclusion switch.
    reordered["scenario_id"] = "renamed-fixed-snapshot"
    assert analyze_attribution(reordered)["conclusion"] == report["conclusion"]


def test_s2_local_record_blocks_propagation_exclusion():
    scenario = load_s2()
    scenario["local_records"] = [{"record_id": "test-local-b", "reach_id": "reach-b",
                                   "event_time": "2026-11-02T09:10:00+08:00",
                                   "description": "独立本地记录待核实", "source": "simulated"}]
    report = analyze_attribution(scenario)
    candidates = by_source(report)
    assert candidates["reach-b"]["role"] != "propagation_path"
    assert candidates["reach-b:local"]["status"] != "excluded"
    assert candidates["reach-b:local"]["local_evidence"]
    assert "证据支持单源" not in report["conclusion"]


@pytest.mark.parametrize("event_time, expected", [("09:17:00", "propagation"),
                                                    ("09:05:00", "propagation"),
                                                    ("09:26:00", "excluded")])
def test_s2_inclusive_window_and_event_order(event_time, expected):
    scenario = load_s2()
    next(s for s in scenario["current_samples"] if s["reach_id"] == "reach-b")["event_time"] = (
        f"2026-11-02T{event_time}+08:00")
    assert by_source(analyze_attribution(scenario))["reach-b"]["status"] == expected
