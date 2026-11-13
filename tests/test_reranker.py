"""Offline numerical, provenance and train/test isolation contracts."""

import json
import math
from copy import deepcopy
from pathlib import Path

import pytest

from backend.attribution import analyze_attribution
from backend.reranker import FEATURE_NAMES, Reranker, build_scenario_groups, rule_score
from scripts.generate_training_data import build_scenario
from scripts.train_reranker import load_rows, load_rule_weights


@pytest.fixture(scope="module")
def data():
    path = Path(__file__).resolve().parents[1] / "data/simulated_candidates.jsonl"
    return load_rows(path)


@pytest.fixture(scope="module")
def model(data):
    result = Reranker()
    result.fit(data[0])
    return result


def test_training_is_deterministic(data, model):
    repeated = Reranker()
    repeated.fit(list(reversed(data[0])))
    assert repeated.coefficients() == model.coefficients()
    assert repeated.to_dict() == model.to_dict()


def test_predict_proba_in_range(data, model):
    assert all(0 < model.predict_proba(row) < 1 for row in data[1])


def test_model_serialization_roundtrip(data, model):
    restored = Reranker.from_dict(json.loads(json.dumps(model.to_dict())))
    assert restored.to_dict() == model.to_dict()
    assert [restored.predict_proba(row) for row in data[1]] == [model.predict_proba(row) for row in data[1]]
    assert len(json.dumps(model.to_dict()).encode("utf-8")) < 50 * 1024


def test_rule_score_recovery(data):
    weights = load_rule_weights()
    outputs = {}
    for row in data[1]:
        uid = row["scenario_uid"]
        if uid not in outputs:
            scenario, _ = build_scenario(row["family"], row["seed"])
            outputs[uid] = analyze_attribution(scenario)
            assert outputs[uid]["ranking_weights"] == weights
        candidate = next(c for c in outputs[uid]["candidates"] if c["source_id"] == row["source_id"])
        assert rule_score(row, weights) == candidate["ranking_score"]
        if "ranking_score" in row:
            assert rule_score(row, weights) == row["ranking_score"]
        assert rule_score({**row, "ranking_score": candidate["ranking_score"]}, weights) == candidate["ranking_score"]


def test_no_feature_leakage(data, model):
    assert len(FEATURE_NAMES) == 6
    assert not {"label", "source_id", "scenario_uid", "status", "role"} & set(FEATURE_NAMES)
    row = deepcopy(data[1][0])
    before = model.predict_proba(row)
    row.update(label=1-row["label"], source_id="other", scenario_uid="other", status="other", role="other")
    assert model.predict_proba(row) == before
    train = data[0]
    raw_columns = [[r["features"][key] for r in train] for key in FEATURE_NAMES[:4]]
    raw_columns += [[math.log1p(r["features"]["absolute_change_value"]) for r in train],
                    [r["features"]["time_lag_minutes"] for r in train]]
    for name, column in zip(FEATURE_NAMES, raw_columns):
        expected_mean = math.fsum(column) / len(column)
        expected_std = math.sqrt(math.fsum((v - expected_mean) ** 2 for v in column) / len(column)) or 1
        assert model.feature_means()[name] == pytest.approx(expected_mean)
        assert model.feature_stds()[name] == pytest.approx(expected_std)
    parameters = model.to_dict()
    model.predict_proba(data[1][0])
    assert model.to_dict() == parameters


def test_missing_time_lag_uses_mean(data, model):
    rows = deepcopy(data[1][:2])
    for row in rows:
        row["scenario_uid"] = "test-imputation"
    rows[0]["features"]["time_lag_minutes"] = 20.0
    rows[1]["features"]["time_lag_minutes"] = None
    grouped = build_scenario_groups(rows)["test-imputation"]
    missing = grouped[1]
    assert missing["_scenario_time_lag_mean"] == 20.0
    filled_mean = deepcopy(missing)
    filled_mean["features"]["time_lag_minutes"] = 20.0
    filled_zero = deepcopy(missing)
    filled_zero["features"]["time_lag_minutes"] = 0.0
    assert model.predict_proba(missing) == model.predict_proba(filled_mean)
    assert model.predict_proba(missing) != model.predict_proba(filled_zero)
    assert rows[1]["features"]["time_lag_minutes"] is None
