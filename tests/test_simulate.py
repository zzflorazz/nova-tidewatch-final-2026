"""Determinism, scenario-level splitting and real-backend feature provenance."""

import json
from collections import Counter, defaultdict
from datetime import datetime

import pytest

from backend.attribution import analyze_attribution
from scripts import generate_training_data as generator


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    output_dir = tmp_path_factory.mktemp("simulated-candidates")
    summary = generator.generate_dataset(output_dir, seed=700, scenarios_per_family=200)
    training = [json.loads(line) for line in (output_dir / "simulated_candidates.jsonl").read_text().splitlines()]
    contracts = [json.loads(line) for line in (output_dir / "simulated_contract.jsonl").read_text().splitlines()]
    return output_dir, summary, training, contracts


def test_same_seed_produces_identical_bytes(generated, tmp_path):
    first_dir, summary, _, _ = generated
    assert generator.generate_dataset(tmp_path, seed=700, scenarios_per_family=200) == summary
    for filename in ("simulated_candidates.jsonl", "simulated_contract.jsonl"):
        assert (first_dir / filename).read_bytes() == (tmp_path / filename).read_bytes()


def test_each_scenario_has_one_split(generated):
    _, _, training, _ = generated
    splits = defaultdict(set)
    for row in training:
        splits[row["scenario_uid"]].add(row["split"])
    assert len(splits) == 600
    assert all(len(values) == 1 for values in splits.values())
    for family_index, family in enumerate(generator.TRAINING_FAMILIES):
        family_rows = [r for r in training if r["family"] == family]
        first_seed = 700 + generator.FAMILIES.index(family) * 200
        assert {r["seed"] for r in family_rows if r["split"] == "train"} == set(range(first_seed, first_seed + 160))
        assert {r["seed"] for r in family_rows if r["split"] == "test"} == set(range(first_seed + 160, first_seed + 200))


def test_injected_source_and_propagation_labels(generated):
    _, _, training, _ = generated
    groups = defaultdict(list)
    for row in training:
        groups[row["scenario_uid"]].append(row)
    for rows in groups.values():
        family = rows[0]["family"]
        positives = {r["source_id"] for r in rows if r["label"] == 1}
        if family == "single_upstream":
            assert positives == {"reach-a"}
            path = [r for r in rows if r["role"] == "propagation_path"]
            assert len(path) == 1
            assert path[0]["source_id"] == "reach-b"
            assert path[0]["label"] == 0
            assert path[0]["status"] == "propagation"
        elif family == "decoy_local":
            assert positives == {"reach-a"}
        else:
            assert positives == {"reach-c:local"}
        assert len([r for r in rows if r["label"] == 1]) == 1
        assert any(r["label"] == 0 for r in rows)


def test_train_test_counts_by_scenario(generated):
    _, summary, training, contracts = generated
    assert summary["scenario_counts"] == {family: 200 for family in generator.FAMILIES}
    assert summary["scenario_splits"] == {"train": 480, "test": 120}
    assert summary["candidate_rows"] == {"train": 960, "test": 240}
    assert Counter(r["split"] for r in training) == {"train": 960, "test": 240}
    assert len(contracts) == summary["contract_scenarios"] == 800
    for family, rows_per_scenario in [(family, 2) for family in generator.TRAINING_FAMILIES]:
        counts = summary["per_family"][family]
        assert counts == {"train_scenarios": 160, "test_scenarios": 40,
                          "train_rows": 160 * rows_per_scenario, "test_rows": 40 * rows_per_scenario}


def test_excluded_and_contract_rows_are_not_training_labels(generated):
    _, _, training, contracts = generated
    for row in training:
        assert row["family"] in generator.TRAINING_FAMILIES
        assert row["status"] != "excluded"
        assert row["role"] in {"independent_source", "propagation_path"}
        assert row["label"] in (0, 1)
        assert row["features"]["evidence_strength"] in {"强", "中", "弱"}
    assert {r["family"] for r in contracts} == {"baseline", "missing_upstream", "single_local", "multi_candidate"}

    def has_label(value):
        if isinstance(value, dict):
            return "label" in value or any(has_label(v) for v in value.values())
        if isinstance(value, list):
            return any(has_label(v) for v in value)
        return False

    assert not any(has_label(record) for record in contracts)
    for record in contracts:
        if record["family"] == "baseline":
            assert record["baseline_result"]["active_anomalies"] == []
            assert record["candidates"] == []
            assert record["attribution"] is None
            assert record["expected_rejection"] == {
                "type": "ValueError", "message": "Target must identify a computed active anomaly"}
        elif record["family"] == "missing_upstream":
            output = record["attribution"]
            assert output["status"] == "UNRESOLVED"
            a = next(c for c in output["candidates"] if c["source_id"] == "reach-a")
            assert a["role"] == "missing_evidence" and a["status"] == "not_excluded"
            assert a["exclude_basis"] == []
        else:
            expected = {"reach-c:local"} if record["family"] == "single_local" else {"reach-a", "reach-c:local"}
            assert set(record["attribution"]["independent_sources"]) == expected


def test_all_attribution_inputs_are_simulated(monkeypatch, tmp_path):
    calls = []

    def capture(scenario):
        assert scenario["data_classification"] == "SIMULATED"
        cutoff = datetime.fromisoformat(scenario["analysis_time"])
        for sample in scenario["history_samples"] + scenario["current_samples"]:
            assert sample["source"] == "simulated"
            assert datetime.fromisoformat(sample["event_time"]) < datetime.fromisoformat(sample["received_time"]) < cutoff
        for sample in scenario["current_samples"]:
            age = (cutoff - datetime.fromisoformat(sample["event_time"])).total_seconds() / 60
            assert age <= scenario["attribution_config"]["max_sample_age_minutes"]
            history = [s for s in scenario["history_samples"] if s["reach_id"] == sample["reach_id"]]
            assert all(datetime.fromisoformat(s["event_time"]) < datetime.fromisoformat(sample["event_time"])
                       for s in history)
        for record in scenario["local_records"]:
            assert record["source"] == "manual"
            assert record["verification_status"] == "pending"
        calls.append(scenario["scenario_id"])
        return analyze_attribution(scenario)

    monkeypatch.setattr(generator, "analyze_attribution", capture)
    generator.generate_dataset(tmp_path, seed=100, scenarios_per_family=5)
    assert len(calls) == len(set(calls)) == 35
    assert {uid.rsplit("-", 1)[0] for uid in calls} == set(generator.FAMILIES)


def test_features_are_copied_from_existing_candidates(generated):
    _, _, training, _ = generated
    outputs = {}
    for row in training:
        uid = row["scenario_uid"]
        if uid not in outputs:
            scenario, _ = generator.build_scenario(row["family"], row["seed"])
            outputs[uid] = analyze_attribution(scenario)
        candidate = next(c for c in outputs[uid]["candidates"] if c["source_id"] == row["source_id"])
        features = row["features"]
        for component in ("time", "absolute_change", "topology", "local_evidence"):
            assert features[component] == candidate["score_components"][component]
        assert features["absolute_change_value"] == candidate["absolute_change"]
        assert features["time_lag_minutes"] == candidate["time_lag_minutes"]
        assert features["topology_distance"] == candidate["topology_distance"]
        assert row["evidence_strength_raw"] == candidate["evidence_strength"]
        assert row["role"] == candidate["role"] and row["status"] == candidate["status"]
    assert len({r["features"]["absolute_change_value"] for r in training}) > 10



def test_decoys_are_retained_independent_negatives(generated):
    _, summary, training, _ = generated
    for family in ("decoy_local", "decoy_upstream"):
        negatives = [r for r in training if r["family"] == family and r["label"] == 0]
        assert len(negatives) == 200
        assert all(r["role"] == "independent_source" and r["status"] == "retained" for r in negatives)
        assert summary["independent_source_negatives"][family] == 200


def test_decoy_amplitude_hard_examples(generated):
    _, summary, training, _ = generated
    groups = defaultdict(list)
    for row in training:
        groups[row["scenario_uid"]].append(row)
    counts = Counter()
    for rows in groups.values():
        family = rows[0]["family"]
        if family not in ("decoy_local", "decoy_upstream"):
            continue
        positive = next(r for r in rows if r["label"] == 1)
        negative = next(r for r in rows if r["label"] == 0)
        if negative["features"]["absolute_change_value"] > positive["features"]["absolute_change_value"]:
            counts[family] += 1
    assert counts["decoy_local"] >= 80
    assert counts["decoy_upstream"] >= 80
    assert dict(counts) == summary["hard_examples"]


def test_temporal_features_make_decoys_learnable(generated):
    from statistics import mean
    _, _, training, _ = generated
    true_upstream = [r["features"]["time"] for r in training
                     if r["family"] == "single_upstream" and r["source_id"] == "reach-a"]
    decoy_upstream = [r["features"]["time"] for r in training
                      if r["family"] == "decoy_upstream" and r["source_id"] == "reach-a"]
    assert mean(true_upstream) - mean(decoy_upstream) > 0.5
    true_local = [r["features"]["time"] for r in training
                  if r["family"] == "decoy_upstream" and r["source_id"] == "reach-c:local"]
    decoy_local = [r["features"]["time"] for r in training
                   if r["family"] == "decoy_local" and r["source_id"] == "reach-c:local"]
    assert mean(true_local) - mean(decoy_local) > 0.5
    for row in training:
        if row["family"] == "decoy_upstream" and row["source_id"] == "reach-a":
            scenario, _ = generator.build_scenario(row["family"], row["seed"])
            window = scenario["propagation_windows"]["reach-a->reach-c"]
            assert window[0] <= row["features"]["time_lag_minutes"] <= window[1]
            times = {s["reach_id"]: datetime.fromisoformat(s["event_time"]) for s in scenario["current_samples"]}
            lag_ab = (times["reach-b"] - times["reach-a"]).total_seconds() / 60
            assert lag_ab < scenario["propagation_windows"]["reach-a->reach-b"][0]
