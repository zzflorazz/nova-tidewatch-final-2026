"""Offline Trace stage-one regression tests; no MQTT or global runtime state."""

import json
import math
from copy import deepcopy
from pathlib import Path

import pytest

from backend.baseline import analyze_scenario, calculate_baselines, evaluate_samples


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
    reserved = json.loads(path.read_text(encoding="utf-8"))["scenarios"][1:]
    assert [s["scenario_id"] for s in reserved] == ["S2", "S3", "S4", "S5"]
    for scenario in reserved:
        assert scenario["status"] == "reserved"
        assert scenario["history_samples"] == scenario["current_samples"] == []
        with pytest.raises(ValueError, match="reserved"):
            analyze_scenario(scenario)
