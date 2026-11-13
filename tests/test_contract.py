"""Appendix API contracts, real health probes and recoverable rule fallback."""

import re
import shutil

import pytest
from fastapi.testclient import TestClient

import backend.api as api
from backend.attribution import analyze_attribution
from backend.baseline import analyze_scenario


@pytest.fixture
def client():
    with TestClient(api.create_app(":memory:")) as value:
        yield value


@pytest.fixture
def model_file(tmp_path, monkeypatch):
    # Exercise rename/restore on a real copy, never rename the repository model.
    path = tmp_path / "reranker.json"
    shutil.copyfile(api.MODEL_PATH, path)
    monkeypatch.setattr(api, "MODEL_PATH", path)
    return path


def test_scenario_load_contract(client):
    for scenario in api.load_scenarios():
        expected = {
            **{k: scenario.get(k) for k in ("scenario_id", "display_name", "analysis_time", "target")},
            "history_sample_count": len(scenario["history_samples"]),
            "current_sample_count": len(scenario["current_samples"]),
            "local_record_count": len(scenario.get("local_records", [])),
            "data_classification": "SIMULATED",
        }
        for route in ("/api/scenario/load", "/scenario/load"):
            for seed in ({}, {"seed": scenario["seed"]}):
                response = client.post(route, json={"scenario_id": scenario["scenario_id"], **seed})
                assert response.status_code == 200
                assert response.json() == expected
            bad = client.post(route, json={"scenario_id": scenario["scenario_id"],
                                           "seed": scenario["seed"] + 1})
            assert bad.status_code == 400
            assert "固定" in bad.json()["detail"]
    assert client.get("/api/anomalies").json() == []  # Loading never runs analysis.
    assert client.post("/scenario/load", json={"scenario_id": "unknown"}).status_code == 404


def test_analyze_contract(client):
    for version, route in enumerate(("/analyze", "/api/analyze"), 1):
        response = client.post(route, json={"scenario_id": "S2", "use_rerank": False})
        assert response.status_code == 200
        output = response.json()
        assert output["status"] == "SUPPORTED_SINGLE"
        assert output["version"] == output["attribution_version"] == version
        assert output["run_id"] and output["candidates"]
        assert output["attribution"]["run_id"] == output["run_id"]
        assert output["attribution"]["version"] == version
        anomaly_id = output["anomaly"]["anomaly_id"]
        assert output["attribution"]["anomaly_id"] == anomaly_id
        assert output["attribution"]["output_json"]["candidates"] == output["candidates"]
        for prefix in ("", "/api"):
            anomaly = client.get(f"{prefix}/anomalies/{anomaly_id}").json()
            assert anomaly["latest_version"] == version
            assert len(anomaly["versions"]) == version
            run = client.get(f"{prefix}/runs/{output['run_id']}").json()
            assert run["status"] == "success"
            assert run["input_snapshot"]["target"] == api.find_scenario("S2")["target"]
            assert run["candidates"] == output["candidates"]
            assert run["conclusion"] == output["conclusion"]


def test_candidates_endpoint_has_basis(client):
    first = client.post("/analyze", json={"scenario_id": "S4b"}).json()
    anomaly_id = first["anomaly"]["anomaly_id"]
    client.post("/analyze", json={"scenario_id": "S4b"})
    required = {"score_components", "ranking_score", "status", "role", "exclude_basis", "evidence_gaps"}
    for prefix in ("", "/api"):
        path = f"{prefix}/anomalies/{anomaly_id}/candidates"
        latest = client.get(path)
        historic = client.get(path, params={"version": 1})
        assert latest.status_code == historic.status_code == 200
        assert historic.json() == first["candidates"]
        assert any(c["status"] == "excluded" for c in latest.json())
        for candidate in latest.json():
            assert required <= candidate.keys()
            if candidate["status"] == "excluded":
                assert candidate["exclude_basis"]
        assert client.get(path, params={"version": 999}).status_code == 404
        assert client.get(f"{prefix}/anomalies/unknown/candidates").status_code == 404


def test_health_reflects_real_state(client, model_file):
    healthy = client.get("/api/health").json()
    assert healthy["status"] == "ok"
    assert healthy["model"]["status"] == "loaded"
    assert healthy["database"] == {"status": "ok", "path": ":memory:", "writable": True}
    assert healthy["scenarios"] == {"status": "ok", "count": len(api.load_scenarios())}
    assert client.get("/health").json() == healthy
    hidden = model_file.with_suffix(".hidden")
    model_file.rename(hidden)
    try:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
        assert response.json()["model"]["status"] == "unavailable"
        assert response.json()["model"]["reason"]
    finally:
        hidden.rename(model_file)
    assert client.get("/api/health").json()["model"]["status"] == "loaded"
    # In-place replacement invalidates a previously successful cached model.
    good = model_file.read_bytes()
    try:
        model_file.write_text("not valid JSON", encoding="utf-8")
        assert client.get("/api/health").json()["status"] == "degraded"
    finally:
        model_file.write_bytes(good)
    assert client.get("/api/health").json()["status"] == "ok"


def test_health_database_and_scenario_errors(client, monkeypatch, tmp_path):
    async def query_only(enabled):
        client.app.state.store._db.execute(f"PRAGMA query_only = {int(enabled)}")
    client.portal.call(query_only, True)
    try:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
        assert response.json()["database"]["writable"] is False
    finally:
        client.portal.call(query_only, False)
    assert client.get("/api/health").json()["database"]["status"] == "ok"
    monkeypatch.setattr(api, "DATA_PATH", tmp_path / "missing-scenarios.json")
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["scenarios"]["status"] == "error"


def test_model_unavailable_degrades(client, model_file):
    # First load successfully, then remove: a warm cache must not mask failure.
    assert client.get("/health").json()["model"]["status"] == "loaded"
    original = client.post("/api/attributions/run", json={"scenario_id": "S3"}).json()
    hidden = model_file.with_suffix(".hidden")
    model_file.rename(hidden)
    try:
        requests = [(route, use_rerank) for route in
                    ("/api/attributions/run", "/api/analyze", "/analyze")
                    for use_rerank in (False, True)]
        for route, use_rerank in requests:
            response = client.post(route, json={"scenario_id": "S3", "use_rerank": use_rerank})
            assert response.status_code == 200
            result = response.json()
            reranking = result["reranking"]
            assert reranking["applied"] is False
            assert reranking["reason"] == "模型不可用，已降级为仅规则排序"
            assert reranking["model_order"] == reranking["rule_order"]
            assert all(reranking["constraints"].values())
            for key in ("status", "conclusion", "candidates", "excluded", "evidence_gaps"):
                assert result[key] == original[key]
        target = original["target_anomaly"]
        anomaly_id = f"{target['reach_id']}-{target['metric']}-{target['event_time']}"
        latest = client.get(f"/api/anomalies/{anomaly_id}/rerank")
        assert latest.status_code == 200 and not latest.json()["applied"]
        assert client.get(f"/api/anomalies/{anomaly_id}/rerank?version=1").status_code == 200
    finally:
        hidden.rename(model_file)
    recovered = client.get(f"/api/anomalies/{anomaly_id}/rerank")
    assert recovered.status_code == 200 and recovered.json()["applied"]


def assert_no_causal_claim(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"conclusion", "explanation", "evidence_gaps", "next_checks"}:
                scan_text(child)
            assert_no_causal_claim(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_causal_claim(child)


def scan_text(value):
    if isinstance(value, dict):
        for child in value.values():
            scan_text(child)
    elif isinstance(value, list):
        for child in value:
            scan_text(child)
    elif isinstance(value, str):
        # Remove only known, explicit negations; never blanket-ignore a whole
        # sentence that may also contain a positive causal assertion.
        text = value
        for phrase in ("非因果证明", "不是因果证明", "不等于因果证明", "不能证明因果来源"):
            text = text.replace(phrase, "")
        assert not re.search("证明|导致|因果成立|必然来自", text), value


def test_no_causal_claim(client):
    scenarios = api.load_scenarios()
    assert len(scenarios) == 7
    for scenario in scenarios:
        if scenario.get("target"):
            response = client.post("/analyze", json={"scenario_id": scenario["scenario_id"], "use_rerank": True})
            assert response.status_code == 200
            assert_no_causal_claim(response.json())
        else:
            # Baseline has no target: preserve its normal, candidate-free contract.
            result = analyze_scenario(scenario)
            assert result["active_anomalies"] == []
            assert_no_causal_claim(result)
    for bad in ("A导致C", "已证明来源", "因果成立", "必然来自A", "非因果证明；A导致C"):
        with pytest.raises(AssertionError):
            scan_text(bad)


def test_attenuation_field():
    for scenario in api.load_scenarios():
        if not scenario.get("target"):
            continue
        result = analyze_attribution(scenario)
        target_change = result["target_anomaly"]["absolute_change"]
        for candidate in result["candidates"]:
            assert candidate["attenuation_note"]
            if candidate["source_type"] == "upstream" and candidate["role"] != "missing_evidence":
                assert candidate["attenuation"] == round(candidate["absolute_change"] / target_change, 6)
            else:
                assert candidate["attenuation"] is None
        for collection in ("excluded", "propagation_path"):
            assert all("attenuation" in c for c in result[collection])


def test_aliases_share_handlers(client):
    paths = {route.path: route for route in client.app.routes}
    for path in ("/scenario/load", "/analyze", "/anomalies/{anomaly_id}/candidates",
                 "/health", "/anomalies/{anomaly_id}", "/runs/{run_id}"):
        assert paths[path].endpoint is paths["/api" + path].endpoint
