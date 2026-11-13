"""Generate deterministic SIMULATED candidate rows, never train a model.

Labels describe injected simulation sources, not established real-world causes.
All numeric features are copied from analyze_attribution candidates. Evidence
strength is categorical normalization only: 中等 -> 中, 待核实 -> 弱; its raw
value is retained. Native propagation status is preserved. Local-check rows
(needs_review) and excluded candidates are not learning-source candidates.

A normal baseline is intentionally rejected by the existing attribution API.
The contract file records that exception alongside the actual baseline result;
it never presents that rejection as a successful attribution.
"""

import argparse
import json
import random
import sys
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.attribution import analyze_attribution
from backend.baseline import analyze_scenario


FAMILIES = ("single_upstream", "single_local", "multi_candidate", "missing_upstream", "baseline")
TRAINING_FAMILIES = ("single_upstream", "decoy_local", "decoy_upstream")
TEMPLATE_IDS = dict(zip(FAMILIES, ("S2", "S4b", "S3", "S5", "S2")))
FAMILIES += ("decoy_local", "decoy_upstream")
TEMPLATE_IDS.update(decoy_local="S2", decoy_upstream="S2")
FEATURE_COMPONENTS = ("time", "absolute_change", "topology", "local_evidence")
STRENGTHS = {"强": "强", "中等": "中", "弱": "弱", "待核实": "弱"}


def load_templates():
    path = ROOT / "data" / "trace_scenarios.json"
    return {s["scenario_id"]: s for s in json.loads(path.read_text(encoding="utf-8"))["scenarios"]}


def make_sample(reach, value, event_time, received_delay):
    return {
        "reach_id": reach, "metric": "turbidity", "value": round(value, 6),
        "event_time": event_time.isoformat(),
        "received_time": (event_time + timedelta(seconds=received_delay)).isoformat(),
        "source": "simulated",
    }


def build_scenario(family, seed, templates=None):
    """Return an input scenario and the independently injected source-ID set."""
    if family not in FAMILIES:
        raise ValueError(f"Unknown family: {family}")
    rng = random.Random(seed)
    templates = load_templates() if templates is None else templates
    scenario = deepcopy(templates[TEMPLATE_IDS[family]])
    uid = f"{family}-{seed}"
    target_time = datetime.fromisoformat("2026-11-02T12:00:00+08:00")
    times = {
        "reach-a": target_time - timedelta(minutes=rng.randint(22, 28)),
        "reach-b": target_time - timedelta(minutes=rng.randint(9, 14)),
        "reach-c": target_time,
    }
    scenario.update(
        scenario_id=uid, seed=seed, status="implemented", data_classification="SIMULATED",
        description="带注入来源标签的教学模拟输入，不代表真实河道来源。",
        analysis_time=(target_time + timedelta(minutes=rng.randint(1, 2))).isoformat(),
        target={"reach_id": "reach-c", "metric": "turbidity", "event_time": target_time.isoformat()},
        history_samples=[], current_samples=[], local_records=[],
    )
    scenario["attribution_config"]["max_sample_age_minutes"] = 30
    centers = {reach: round(center + rng.uniform(-0.15, 0.15), 6)
               for reach, center in zip(scenario["topology"], (6, 5, 8))}
    steps = {reach: rng.uniform(0.4, 0.7) for reach in scenario["topology"]}
    changes = {reach: rng.uniform(-0.15, 0.15) * steps[reach] for reach in scenario["topology"]}
    truth = set()
    if family == "single_upstream":
        changes.update(zip(scenario["topology"], (rng.uniform(24, 40), rng.uniform(12, 18), rng.uniform(6, 10))))
        truth = {"reach-a"}
    elif family in ("single_local", "multi_candidate"):
        changes["reach-c"] = rng.uniform(18, 28)
        truth = {"reach-c:local"}
        if family == "multi_candidate":
            changes["reach-a"] = rng.uniform(6, 10)
            truth.add("reach-a")
        scenario["local_records"] = [{
            "record_id": f"{uid}-construction", "reach_id": "reach-c", "metric": "turbidity",
            "event_time": (target_time - timedelta(minutes=rng.randint(3, 15))).isoformat(),
            "source": "manual", "entered_by": "simulated_operator",
            "verification_status": "pending", "data_classification": "SIMULATED",
            "description": "模拟注入的 C 附近施工记录，现实证据等级仍为待核实。",
        }]
    elif family == "missing_upstream":
        changes["reach-c"] = rng.uniform(18, 28)

    if family in ("decoy_local", "decoy_upstream"):
        # Distinguish injected labels by observed temporal evidence, not noise:
        # false local records occur near the local-window edge; false upstream
        # signals occur near the upstream-window edge and cannot form A->B->C.
        if family == "decoy_local":
            changes["reach-a"] = rng.uniform(12, 18)
            changes["reach-c"] = rng.uniform(24, 32)
            record_lag = rng.uniform(30, 34)
            truth = {"reach-a"}
        else:
            changes["reach-a"] = rng.uniform(30, 40)
            changes["reach-c"] = rng.uniform(16, 22)
            times["reach-a"] = target_time - timedelta(minutes=rng.uniform(15, 15.4))
            times["reach-b"] = target_time - timedelta(minutes=rng.uniform(12, 14))
            record_lag = rng.uniform(3, 8)
            truth = {"reach-c:local"}
        scenario["local_records"] = [{
            "record_id": f"{uid}-construction", "reach_id": "reach-c", "metric": "turbidity",
            "event_time": (target_time - timedelta(minutes=record_lag)).isoformat(),
            "source": "manual", "entered_by": "simulated_operator",
            "verification_status": "pending", "data_classification": "SIMULATED",
            "description": "教学模拟的待核实本地记录；注入标签不等于现实因果证明。",
        }]

    for reach in scenario["topology"]:
        offsets = [-2, -1, 0, 1, 2, -1, 0, 1, 0]
        rng.shuffle(offsets)
        for index, offset in enumerate(offsets):
            event_time = target_time - timedelta(minutes=100 - index * 5)
            scenario["history_samples"].append(make_sample(
                reach, centers[reach] + offset * steps[reach], event_time, rng.randint(1, 5)))
        if not (family == "missing_upstream" and reach == "reach-a"):
            scenario["current_samples"].append(make_sample(
                reach, centers[reach] + changes[reach], times[reach], rng.randint(1, 5)))
    return scenario, truth


def extract_features(candidate):
    """Copy existing score components/measurements; do not calculate new scores."""
    return {
        **{key: candidate["score_components"][key] for key in FEATURE_COMPONENTS},
        "absolute_change_value": candidate["absolute_change"],
        "time_lag_minutes": candidate["time_lag_minutes"],
        "topology_distance": candidate["topology_distance"],
        "evidence_strength": STRENGTHS[candidate["evidence_strength"]],
    }


def _write_jsonl(stream, record):
    stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")


def generate_dataset(output_dir=ROOT / "data", seed=20261110, scenarios_per_family=200):
    if isinstance(scenarios_per_family, bool) or not isinstance(scenarios_per_family, int) or scenarios_per_family < 5 or scenarios_per_family % 5:
        raise ValueError("scenarios_per_family must be a positive multiple of 5 (exact 80/20 split)")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    templates = load_templates()
    summary = {
        "scenario_counts": {family: 0 for family in FAMILIES},
        "candidate_rows": {"train": 0, "test": 0},
        "scenario_splits": {"train": 0, "test": 0},
        "per_family": {family: {"train_scenarios": 0, "test_scenarios": 0,
                                "train_rows": 0, "test_rows": 0} for family in TRAINING_FAMILIES},
        "contract_scenarios": 0,
        "independent_source_negatives": {family: 0 for family in TRAINING_FAMILIES},
        "hard_examples": {"decoy_local": 0, "decoy_upstream": 0},
    }
    with (output_dir / "simulated_candidates.jsonl").open("w", encoding="utf-8", newline="\n") as training, \
            (output_dir / "simulated_contract.jsonl").open("w", encoding="utf-8", newline="\n") as contracts:
        for family_index, family in enumerate(FAMILIES):
            for index in range(scenarios_per_family):
                scenario_seed = seed + family_index * scenarios_per_family + index
                scenario, truth = build_scenario(family, scenario_seed, templates)
                uid = scenario["scenario_id"]
                summary["scenario_counts"][family] += 1
                if family == "baseline":
                    baseline = analyze_scenario(scenario)
                    if baseline["active_anomalies"]:
                        raise ValueError(f"Baseline family unexpectedly anomalous: {uid}")
                    try:
                        analyze_attribution(scenario)
                    except ValueError as error:
                        if str(error) != "Target must identify a computed active anomaly":
                            raise
                        rejection = {"type": type(error).__name__, "message": str(error)}
                    else:
                        raise ValueError("Normal baseline unexpectedly accepted by attribution")
                    _write_jsonl(contracts, {
                        "scenario_uid": uid, "family": family, "seed": scenario_seed,
                        "data_classification": "SIMULATED", "scenario": scenario,
                        "baseline_result": baseline, "attribution": None,
                        "expected_rejection": rejection, "candidates": [],
                    })
                    summary["contract_scenarios"] += 1
                    continue

                output = analyze_attribution(scenario)
                if family not in TRAINING_FAMILIES:
                    if family == "missing_upstream":
                        missing = next(c for c in output["candidates"] if c["source_id"] == "reach-a")
                        if output["status"] != "UNRESOLVED" or missing["status"] != "not_excluded":
                            raise ValueError(f"Missing-upstream contract violated: {uid}")
                    if family in ("single_local", "multi_candidate"):
                        if set(output["independent_sources"]) != truth:
                            raise ValueError(f"Contract sources differ from injected sources: {uid}")
                    _write_jsonl(contracts, {
                        "scenario_uid": uid, "family": family, "seed": scenario_seed,
                        "data_classification": "SIMULATED", "scenario": scenario, "attribution": output,
                    })
                    summary["contract_scenarios"] += 1
                    continue

                split = "train" if index < scenarios_per_family * 4 // 5 else "test"
                summary["scenario_splits"][split] += 1
                summary["per_family"][family][f"{split}_scenarios"] += 1
                positives = set()
                eligible = [c for c in output["candidates"] if c["status"] != "excluded"
                            and c["role"] in ("independent_source", "propagation_path")]
                if len(truth) != 1 or len(eligible) < 2:
                    raise ValueError(f"Ranking scenario lacks a unique source or negative: {uid}")
                if family in ("decoy_local", "decoy_upstream"):
                    positive_id = next(iter(truth))
                    negative_id = "reach-c:local" if family == "decoy_local" else "reach-a"
                    positive = next(c for c in eligible if c["source_id"] == positive_id)
                    negative = next(c for c in eligible if c["source_id"] == negative_id)
                    if negative["role"] != "independent_source" or negative["status"] != "retained":
                        raise ValueError(f"Decoy is not an eligible independent source: {uid}")
                    if negative["absolute_change"] > positive["absolute_change"]:
                        summary["hard_examples"][family] += 1
                for candidate in output["candidates"]:
                    if candidate["status"] == "excluded" or candidate["role"] not in ("independent_source", "propagation_path"):
                        continue
                    label = int(candidate["source_id"] in truth)
                    if not label and candidate["role"] == "independent_source":
                        summary["independent_source_negatives"][family] += 1
                    if label:
                        positives.add(candidate["source_id"])
                    _write_jsonl(training, {
                        "scenario_uid": uid, "family": family, "seed": scenario_seed, "split": split,
                        "source_id": candidate["source_id"], "role": candidate["role"],
                        "status": candidate["status"], "label": label,
                        "data_classification": "SIMULATED",
                        "evidence_strength_raw": candidate["evidence_strength"],
                        "features": extract_features(candidate),
                    })
                    summary["candidate_rows"][split] += 1
                    summary["per_family"][family][f"{split}_rows"] += 1
                if positives != truth:
                    raise ValueError(f"Injected sources missing from eligible candidates: {uid}")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261110)
    parser.add_argument("--per-family", type=int, default=200)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    summary = generate_dataset(args.output_dir, args.seed, args.per_family)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
