"""Real-scene hard constraints, explanations and historical API reranking."""

import json
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.attribution import analyze_attribution
from backend.reranker import FEATURE_NAMES, Reranker, rerank_attribution, sigmoid

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ("S2", "S3", "S4a", "S4b", "S5")


@pytest.fixture(scope="module")
def model():
    return Reranker.from_dict(json.loads((ROOT / "models/reranker.json").read_text()))


def scenario(sid):
    return next(s for s in json.loads((ROOT / "data/trace_scenarios.json").read_text())["scenarios"]
                if s["scenario_id"] == sid)


def output(sid):
    return analyze_attribution(scenario(sid))


def test_excluded_never_ranked(model):
    original = output("S4b")
    before = deepcopy(original)
    excluded = next(c for c in original["candidates"] if c["source_id"] == "reach-a")
    assert excluded["status"] == "excluded" and excluded["exclude_basis"]
    result = rerank_attribution(original, model)
    assert "reach-a" not in result["model_order"]
    assert all(c["source_id"] != "reach-a" for c in result["candidates"])
    assert original == before


def test_unresolved_status_not_forced(model):
    original = output("S5")
    before = deepcopy(original)
    result = rerank_attribution(original, model)
    assert result["status"] == original["status"] == "UNRESOLVED"
    assert set(result["model_order"]) == {c["source_id"] for c in original["candidates"]
                                        if c["role"] == "independent_source" and c["status"] == "retained"}
    assert "reach-a" not in result["model_order"]
    assert original == before


def test_multi_candidate_keeps_both(model):
    original = output("S3")
    result = rerank_attribution(original, model)
    assert result["applied"]
    assert set(result["model_order"]) == {"reach-a", "reach-c:local"}
    assert result["status"] == "MULTI_CANDIDATE"
    assert all(result["constraints"].values())


def test_propagation_path_not_in_ranking(model):
    original = output("S2")
    path = next(c for c in original["candidates"] if c["source_id"] == "reach-b")
    assert path["role"] == "propagation_path"
    result = rerank_attribution(original, model)
    assert "reach-b" not in result["model_order"]
    assert all(c["source_id"] != "reach-b" for c in result["candidates"])
    assert path in original["propagation_path"]


def test_single_candidate_skips_model(model):
    result = rerank_attribution(output("S4a"), model)
    assert result["applied"] is False
    assert result["reason"] == "保留候选不足，未应用模型重排序"
    assert result["rule_order"] == result["model_order"] == ["reach-a"]


def test_contributions_sum_to_logit(model):
    result = rerank_attribution(output("S3"), model)
    assert len(result["candidates"]) == 2
    for candidate in result["candidates"]:
        assert set(candidate["contributions"]) == set(FEATURE_NAMES)
        assert abs(sum(candidate["contributions"].values()) + candidate["intercept"] - candidate["logit"]) < 1e-9
        assert candidate["model_score"] == sigmoid(candidate["logit"])
        assert candidate["explanation"]


def test_deterministic_output(model):
    original = output("S3")
    expected = rerank_attribution(original, model)
    assert expected == rerank_attribution(original, model)
    reordered = deepcopy(original)
    reordered["candidates"].reverse()
    assert expected == rerank_attribution(reordered, model)
    parameters = model.to_dict()
    parameters["coefficients"] = {name: 0 for name in FEATURE_NAMES}
    tied = rerank_attribution(original, Reranker.from_dict(parameters))
    assert tied["model_order"] == sorted(tied["model_order"])
    assert tied["order_changed"] == (tied["model_order"] != tied["rule_order"])


@pytest.mark.parametrize("sid", SCENARIOS)
def test_status_and_evidence_untouched(model, sid):
    original = output(sid)
    before = deepcopy(original)
    reranked = rerank_attribution(original, model)
    assert original == before
    assert reranked["status"] == before["status"]
    assert reranked["model_version"].startswith("logreg-v1-")
    assert all(reranked["constraints"].values())
    assert set(reranked["model_order"]) == set(reranked["rule_order"])


def test_missing_lag_uses_same_scene_mean(model):
    original = output("S3")
    a = next(c for c in original["candidates"] if c["source_id"] == "reach-a")
    local = next(c for c in original["candidates"] if c["source_id"] == "reach-c:local")
    a["time_lag_minutes"] = None
    expected = deepcopy(original)
    next(c for c in expected["candidates"] if c["source_id"] == "reach-a")["time_lag_minutes"] = local["time_lag_minutes"]
    assert rerank_attribution(original, model) == rerank_attribution(expected, model)
    local["time_lag_minutes"] = None
    skipped = rerank_attribution(original, model)
    assert not skipped["applied"] and "场景均值" in skipped["reason"]


@pytest.fixture
def client():
    with TestClient(create_app(":memory:")) as value:
        yield value


def test_api_optional_reranking_and_stored_output_unchanged(client):
    response = client.post("/api/attributions/run", json={"scenario_id": "S3", "use_rerank": True})
    assert response.status_code == 200
    result = response.json()
    assert result["reranking"]["applied"]
    target = scenario("S3")["target"]
    anomaly_id = f"{target['reach_id']}-{target['metric']}-{target['event_time']}"
    stored = client.get(f"/api/anomalies/{anomaly_id}/attributions/1").json()
    assert "reranking" not in stored["output_json"]
    assert stored["output_json"] == {k: v for k, v in result.items()
                                      if k not in ("run_id", "attribution_version", "reranking")}
    latest = client.get(f"/api/anomalies/{anomaly_id}/rerank")
    assert latest.status_code == 200
    assert latest.json() == {**result["reranking"], "run_id": result["run_id"], "attribution_version": 1}
    assert client.get(f"/api/anomalies/{anomaly_id}/attributions/1").json() == stored
    assert client.get(f"/api/anomalies/{anomaly_id}/rerank?version=99").status_code == 404
    assert client.get("/api/anomalies/not-found/rerank").json()["detail"] == "未找到异常：not-found"


def test_api_reranks_s5_history_without_rewriting_versions(client):
    first = client.post("/api/attributions/run", json={"scenario_id": "S5"}).json()
    assert "reranking" not in first
    s = scenario("S5")
    target = s["target"]
    anomaly_id = f"{target['reach_id']}-{target['metric']}-{target['event_time']}"
    original = client.get(f"/api/anomalies/{anomaly_id}/attributions/1").json()
    cutoff = datetime.fromisoformat(s["analysis_time"])
    sample = {"reach_id": "reach-a", "metric": "turbidity", "value": 6,
              "event_time": (cutoff - timedelta(minutes=10)).isoformat(),
              "received_time": (cutoff - timedelta(minutes=1)).isoformat(), "source": "manual"}
    response = client.post(f"/api/anomalies/{anomaly_id}/samples", json={"scenario_id": "S5", "sample": sample})
    assert response.status_code == 200
    for version in (1, 2):
        result = client.get(f"/api/anomalies/{anomaly_id}/rerank", params={"version": version})
        assert result.status_code == 200
        assert result.json()["attribution_version"] == version
        assert result.json()["status"] == "UNRESOLVED"
    assert client.get(f"/api/anomalies/{anomaly_id}/attributions/1").json() == original
