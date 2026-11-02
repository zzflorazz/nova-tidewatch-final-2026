"""Per-reach, per-metric median/IQR baselines using only supplied samples.

Quartiles use linear interpolation at (n - 1) * p (inclusive endpoints).
A zero IQR uses the metric's configured minimum noise as denominator;
the observed IQR remains zero and fallback is explicitly reported.
"""

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _quantile(ordered, probability):
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def calculate_baselines(history):
    """Return statistics keyed by (reach_id, metric), without mutating input."""
    groups = defaultdict(list)
    for sample in history:
        groups[(sample["reach_id"], sample["metric"])].append(
            _number(sample["value"], "sample value")
        )
    baselines = {}
    for key, values in groups.items():
        ordered = sorted(values)
        q1 = _quantile(ordered, 0.25)
        q3 = _quantile(ordered, 0.75)
        baselines[key] = {
            "sample_count": len(ordered),
            "median": median(ordered),
            "q1": q1,
            "q3": q3,
            "iqr": q3 - q1,
        }
    return baselines


def evaluate_samples(history, current, config):
    """Evaluate each supplied current sample against its own historical group.

    config maps metric -> {deviation_threshold, min_noise}. An anomaly means
    abs(signed deviation) > threshold; equality is within the allowed band.
    Missing history/config is an error, never an implicit normal result.
    """
    baselines = calculate_baselines(history)
    results = []
    for sample in current:
        key = (sample["reach_id"], sample["metric"])
        if key not in baselines:
            raise ValueError(f"No historical baseline for {key}")
        settings = config[sample["metric"]]
        threshold = _number(settings["deviation_threshold"], "deviation_threshold")
        noise = _number(settings["min_noise"], "min_noise")
        if threshold <= 0 or noise <= 0:
            raise ValueError("deviation_threshold and min_noise must be positive")
        baseline = baselines[key]
        value = _number(sample["value"], "current value")
        fallback = "minimum_noise" if baseline["iqr"] == 0 else None
        denominator = noise if fallback else baseline["iqr"]
        change = value - baseline["median"]
        deviation = change / denominator
        results.append({
            **sample,
            **baseline,
            "effective_iqr": denominator,
            "fallback": fallback,
            "deviation": deviation,
            "absolute_change": abs(change),
            "deviation_threshold": threshold,
            "is_anomaly": abs(deviation) > threshold,
        })
    return results


def _time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Sample times and analysis_time require a timezone")
    return parsed


def analyze_scenario(scenario):
    """Analyze a fixed snapshot. Reserved scenarios have no executable logic.

    Validate explicit history/current separation and the analysis cutoff;
    this stage does not implement a history store or late-data processing.
    """
    if scenario["status"] != "implemented":
        raise ValueError("Scenario is reserved and not implemented")
    history = scenario["history_samples"]
    current = scenario["current_samples"]
    if not history or not current:
        raise ValueError("Historical and current samples are required")
    cutoff = _time(scenario["analysis_time"])
    for sample in history + current:
        if _time(sample["event_time"]) > cutoff or _time(sample["received_time"]) > cutoff:
            raise ValueError("Sample is outside the analysis snapshot")
    latest_history = {}
    for sample in history:
        key = (sample["reach_id"], sample["metric"])
        event_time = _time(sample["event_time"])
        latest_history[key] = max(latest_history.get(key, event_time), event_time)
    for sample in current:
        key = (sample["reach_id"], sample["metric"])
        if key in latest_history and latest_history[key] >= _time(sample["event_time"]):
            raise ValueError("History must precede the current sample")
    results = evaluate_samples(history, current, scenario["baseline_config"])
    anomalies = [result for result in results if result["is_anomaly"]]
    return {
        "scenario_id": scenario["scenario_id"],
        "seed": scenario["seed"],
        "analysis_time": scenario["analysis_time"],
        "data_classification": scenario["data_classification"],
        "status": "活动异常" if anomalies else "无活动异常",
        "results": results,
        "active_anomalies": anomalies,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path,
                        default=Path(__file__).resolve().parents[1] / "data/trace_scenarios.json")
    parser.add_argument("--scenario", default="S1")
    args = parser.parse_args()
    scenarios = json.loads(args.scenarios.read_text(encoding="utf-8"))["scenarios"]
    scenario = next((s for s in scenarios if s["scenario_id"] == args.scenario), None)
    if scenario is None:
        parser.error(f"Unknown scenario: {args.scenario}")
    try:
        result = analyze_scenario(scenario)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
