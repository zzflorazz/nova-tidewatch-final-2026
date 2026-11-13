"""Workbench API contracts; each test owns an independent memory database."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app, find_scenario


@pytest.fixture
def client():
    with TestClient(create_app(":memory:")) as instance:
        yield instance


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    health = response.json()
    assert health["status"] == "ok"
    assert health["data_classification"] == "SIMULATED"
    assert health["database"]["status"] == "ok"
    assert health["model"]["status"] == "loaded"
    business_routes = [r for r in client.app.routes if r.path.startswith("/api/")]
    assert len(business_routes) == 15
    assert [(r.path, r.methods) for r in business_routes if r.methods != {"GET"}] == [
        ("/api/scenario/load", {"POST"}),
        ("/api/attributions/run", {"POST"}),
        ("/api/analyze", {"POST"}),
        ("/api/anomalies/{anomaly_id}/samples", {"POST"}),
    ]


def test_list_scenarios_returns_all_implemented(client):
    response = client.get("/api/scenarios")
    assert response.status_code == 200
    scenarios = response.json()
    assert [s["scenario_id"] for s in scenarios] == ["S1", "S2", "S3", "S4a", "S4b", "S5", "D1"]
    assert all(s["status"] == "implemented" for s in scenarios)
    assert all(set(s) == {"scenario_id", "status", "analysis_time", "target", "origin", "display_name", "display_note"} for s in scenarios)
    assert scenarios[0]["target"] is None


def test_get_scenario_and_404(client):
    response = client.get("/api/scenarios/S5")
    assert response.status_code == 200
    assert response.json() == find_scenario("S5")
    assert response.json()["history_samples"]
    missing = client.get("/api/scenarios/not-found")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "未找到场景：not-found"


def test_anomalies_empty_initially(client):
    response = client.get("/api/anomalies")
    assert response.status_code == 200
    assert response.json() == []


def test_attribution_versions_after_store_write(client):
    async def seed():
        # SQLite is owned by the app's event-loop thread. These are direct
        # store calls, dispatched through TestClient's portal, not write routes.
        store = client.app.state.store
        event_time = "2026-11-02T14:30:00+08:00"
        store.create_anomaly("anomaly-1", "reach-c", "turbidity", event_time, 30)
        expected = []
        for version in (1, 2):
            run_id = f"run-api-{version}"
            store.start_run(run_id, "S5", "anomaly-1")
            output = {"status": "UNRESOLVED", "conclusion": f"版本{version}：来源未定",
                      "candidates": [{"source_id": "reach-a", "status": "not_excluded"}]}
            snapshot = {"samples": [{"reach_id": "reach-c", "value": 30}],
                        "config": {"version": version}}
            saved_version = store.save_attribution(
                run_id, "anomaly-1", "2026-11-02T14:35:00+08:00",
                output["status"], output["conclusion"], snapshot, output,
            )
            assert saved_version == version
            store.finish_run(run_id, "success")
            run = store.get_run(run_id)
            assert run["status"] == "success"
            assert run["attribution_version"] == version
            assert run["scenario_id"] == "S5"
            assert run["started_at"] and run["finished_at"]
            expected.append(store.get_attribution("anomaly-1", version))
        assert store.get_run("not-found") is None
        store.create_anomaly("anomaly-2", "reach-b", "flow", event_time, 2)
        return expected

    expected = client.portal.call(seed)
    response = client.get("/api/anomalies")
    assert response.status_code == 200
    assert [a["anomaly_id"] for a in response.json()] == ["anomaly-1", "anomaly-2"]
    assert [a["latest_version"] for a in response.json()] == [2, 0]
    detail = client.get("/api/anomalies/anomaly-1")
    assert detail.status_code == 200
    assert detail.json()["latest_version"] == 2
    assert detail.json()["anomaly"]["current_version"] == 3
    summaries = client.get("/api/anomalies/anomaly-1/attributions")
    assert summaries.status_code == 200
    keys = ("version", "status", "conclusion", "run_id", "analysis_time", "created_at")
    assert summaries.json() == [{key: row[key] for key in keys} for row in expected]
    for version, row in enumerate(expected, start=1):
        response = client.get(f"/api/anomalies/anomaly-1/attributions/{version}")
        assert response.status_code == 200
        assert response.json() == row
        assert isinstance(response.json()["input_snapshot"], dict)
        assert isinstance(response.json()["output_json"], dict)
    assert client.get("/api/anomalies/anomaly-2/attributions").json() == []
    missing_version = client.get("/api/anomalies/anomaly-1/attributions/99")
    assert missing_version.status_code == 404
    assert missing_version.json()["detail"] == "未找到归因版本：99"


def test_unknown_anomaly_404(client):
    for suffix in ("", "/attributions", "/attributions/1"):
        response = client.get(f"/api/anomalies/not-found{suffix}")
        assert response.status_code == 404
        assert response.json()["detail"] == "未找到异常：not-found"



def target_anomaly_id(scenario):
    target = scenario["target"]
    return f"{target['reach_id']}-{target['metric']}-{target['event_time']}"


def test_run_attribution_creates_version(client):
    scenario = find_scenario("S2")
    response = client.post("/api/attributions/run", json={"scenario_id": "S2"})
    assert response.status_code == 200
    output = response.json()
    assert output["status"] == "SUPPORTED_SINGLE"
    assert output["attribution_version"] == 1
    assert output["run_id"]
    versions = client.get(f"/api/anomalies/{target_anomaly_id(scenario)}/attributions")
    assert versions.status_code == 200
    assert len(versions.json()) == 1
    assert versions.json()[0]["run_id"] == output["run_id"]
    run = client.get(f"/api/runs/{output['run_id']}")
    assert run.status_code == 200
    assert run.json()["status"] == "success"
    assert run.json()["scenario_id"] == "S2"
    assert run.json()["anomaly_id"] == target_anomaly_id(scenario)
    assert run.json()["attribution_version"] == 1


def test_run_unknown_scenario_404(client):
    response = client.post("/api/attributions/run", json={"scenario_id": "not-found"})
    assert response.status_code == 404
    assert response.json()["detail"] == "未找到场景：not-found"


def test_late_sample_creates_new_version(client):
    scenario = find_scenario("S5")
    anomaly_id = target_anomaly_id(scenario)
    response = client.post("/api/attributions/run", json={"scenario_id": "S5"})
    assert response.status_code == 200
    assert response.json()["attribution_version"] == 1
    assert response.json()["status"] == "UNRESOLVED"
    original = client.get(f"/api/anomalies/{anomaly_id}/attributions/1").json()
    analysis_time = datetime.fromisoformat(scenario["analysis_time"])
    sample = {
        "reach_id": "reach-a", "metric": "turbidity", "value": 6,
        "event_time": (analysis_time - timedelta(minutes=10)).isoformat(),
        "received_time": (analysis_time - timedelta(minutes=1)).isoformat(),
        "source": "manual",
    }
    response = client.post(f"/api/anomalies/{anomaly_id}/samples", json={
        "scenario_id": "S5", "sample": sample,
    })
    assert response.status_code == 200
    assert response.json()["attribution_version"] == 2
    assert response.json()["data_health"]["reach-a"]["status"] == "fresh"
    assert response.json()["run_id"] != original["run_id"]
    versions = client.get(f"/api/anomalies/{anomaly_id}/attributions").json()
    assert [v["version"] for v in versions] == [1, 2]
    assert client.get(f"/api/anomalies/{anomaly_id}/attributions/1").json() == original
    latest = client.get(f"/api/anomalies/{anomaly_id}/attributions/2")
    assert latest.status_code == 200
    assert latest.json()["input_snapshot"]["current_samples"][-1] == sample
    assert original["conclusion"] == versions[0]["conclusion"]
    assert find_scenario("S5") == scenario


def test_late_sample_anomaly_mismatch_400(client):
    response = client.post("/api/anomalies/wrong-id/samples", json={
        "scenario_id": "S5", "sample": {},
    })
    assert response.status_code == 400
    assert "不匹配" in response.json()["detail"]
    assert client.get("/api/anomalies").json() == []


def test_rerun_does_not_duplicate_samples(client):
    async def get_samples():
        return client.app.state.store.get_samples("reach-c", "turbidity")

    first = client.post("/api/attributions/run", json={"scenario_id": "S5"})
    assert first.status_code == 200
    samples_before = client.portal.call(get_samples)
    assert samples_before
    second = client.post("/api/attributions/run", json={"scenario_id": "S5"})
    assert second.status_code == 200
    assert second.json()["attribution_version"] == 2
    samples_after = client.portal.call(get_samples)
    assert len(samples_after) == len(samples_before)
    assert samples_after == samples_before


def test_run_unimplemented_scenario_400(client, monkeypatch):
    scenario = find_scenario("S2")
    scenario["status"] = "reserved"
    monkeypatch.setattr("backend.api.find_scenario", lambda scenario_id: scenario)
    response = client.post("/api/attributions/run", json={"scenario_id": "S2"})
    assert response.status_code == 400
    assert response.json()["detail"] == "场景尚未实现：S2"


def test_unknown_run_404(client):
    response = client.get("/api/runs/not-found")
    assert response.status_code == 404
    assert response.json()["detail"] == "未找到运行记录：not-found"


def test_late_sample_unknown_scenario_404(client):
    response = client.post("/api/anomalies/any-id/samples", json={
        "scenario_id": "not-found", "sample": {},
    })
    assert response.status_code == 404
    assert response.json()["detail"] == "未找到场景：not-found"


def test_sample_exists_normalizes_time_and_value_without_writing(client):
    async def check():
        store = client.app.state.store
        assert not store.sample_exists("reach-a", "turbidity", "2026-11-02T01:00:00Z", 6)
        sample = {
            "reach_id": "reach-a", "metric": "turbidity", "value": 6,
            "event_time": "2026-11-02T09:00:00+08:00",
            "received_time": "2026-11-02T09:01:00+08:00", "source": "simulated",
        }
        store.insert_sample(sample)
        store.insert_sample({**sample, "value": 7})
        before = store.get_samples("reach-a", "turbidity")
        assert store.sample_exists("reach-a", "turbidity", "2026-11-02T01:00:00Z", "6.0")
        assert store.sample_exists("reach-a", "turbidity", sample["event_time"], 7.0)
        assert not store.sample_exists("reach-a", "turbidity", sample["event_time"], 8)
        assert not store.sample_exists("reach-b", "turbidity", sample["event_time"], 6)
        assert not store.sample_exists("reach-a", "flow", sample["event_time"], 6)
        assert store.get_samples("reach-a", "turbidity") == before

    client.portal.call(check)



def test_evidence_chain_after_run(client):
    scenario = find_scenario("S3")
    response = client.post("/api/attributions/run", json={"scenario_id": "S3"})
    assert response.status_code == 200
    output = response.json()
    anomaly_id = target_anomaly_id(scenario)
    response = client.get(f"/api/anomalies/{anomaly_id}/evidence")
    assert response.status_code == 200
    evidence = response.json()
    assert evidence["version"] == 1
    assert evidence["anomaly"]["anomaly_id"] == anomaly_id
    assert evidence["run"] == client.get(f"/api/runs/{output['run_id']}").json()
    assert evidence["run"]["status"] == "success"
    assert evidence["run"]["attribution_version"] == 1
    saved = client.get(f"/api/anomalies/{anomaly_id}/attributions/1").json()
    assert evidence["output"] == saved["output_json"]
    assert evidence["input_summary"] == {
        "history_sample_count": len(scenario["history_samples"]),
        "current_sample_count": len(scenario["current_samples"]),
        "target": scenario["target"],
    }
    assert {c["source_id"] for c in evidence["candidates"]} == set(output["independent_sources"])
    assert all(c["role"] == "independent_source" and c["status"] == "retained"
               for c in evidence["candidates"])
    for key in ("excluded", "propagation_path", "evidence_gaps", "next_checks",
                "ranking_score_meaning", "data_classification"):
        assert evidence[key] == output[key]
    assert evidence["next_checks"]
    assert [v["version"] for v in evidence["versions"]] == [1]
    assert set(evidence["versions"][0]) == {"version", "status", "conclusion", "run_id", "created_at"}
    assert evidence["note"] == "候选排序分为相对分，不代表因果概率；排除仅限本场景证据范围。"


def test_evidence_version_query(client):
    scenario = find_scenario("S5")
    anomaly_id = target_anomaly_id(scenario)
    first_run = client.post("/api/attributions/run", json={"scenario_id": "S5"})
    assert first_run.status_code == 200
    first = client.get(f"/api/anomalies/{anomaly_id}/evidence?version=1")
    assert first.status_code == 200
    first = first.json()
    analysis_time = datetime.fromisoformat(scenario["analysis_time"])
    sample = {
        "reach_id": "reach-a", "metric": "turbidity", "value": 6,
        "event_time": (analysis_time - timedelta(minutes=10)).isoformat(),
        "received_time": (analysis_time - timedelta(minutes=1)).isoformat(), "source": "manual",
    }
    second_run = client.post(f"/api/anomalies/{anomaly_id}/samples", json={"scenario_id": "S5", "sample": sample})
    assert second_run.status_code == 200
    second = second_run.json()
    for version, expected in [(1, first["output"]), (2, {k: v for k, v in second.items()
                                                         if k not in ("run_id", "attribution_version")})]:
        response = client.get(f"/api/anomalies/{anomaly_id}/evidence", params={"version": version})
        assert response.status_code == 200
        evidence = response.json()
        assert evidence["version"] == version
        assert evidence["output"] == expected
        assert evidence["output"]["status"] in {"UNRESOLVED", "MULTI_CANDIDATE", "SUPPORTED_SINGLE", "SUPPORTED_LOCAL"}
        assert [v["version"] for v in evidence["versions"]] == [1, 2]
        assert evidence["run"]["attribution_version"] == version
        assert evidence["run"]["anomaly_id"] == anomaly_id
        assert evidence["input_summary"]["current_sample_count"] == len(scenario["current_samples"]) + version - 1
    latest = client.get(f"/api/anomalies/{anomaly_id}/evidence").json()
    assert latest["version"] == 2
    assert latest["output"]["data_health"]["reach-a"]["status"] == "fresh"
    assert client.get(f"/api/anomalies/{anomaly_id}/evidence?version=1").json()["output"]["conclusion"] == first["output"]["conclusion"]


def test_evidence_unknown_anomaly_404(client):
    response = client.get("/api/anomalies/not-found/evidence")
    assert response.status_code == 404
    assert response.json()["detail"] == "未找到异常：not-found"


def test_evidence_unknown_version_404(client):
    scenario = find_scenario("S5")
    anomaly_id = target_anomaly_id(scenario)

    async def seed():
        target = scenario["target"]
        client.app.state.store.create_anomaly(anomaly_id, target["reach_id"], target["metric"], target["event_time"], 30)

    client.portal.call(seed)
    empty = client.get(f"/api/anomalies/{anomaly_id}/evidence")
    assert empty.status_code == 404
    assert empty.json()["detail"] == "该异常暂无归因版本"
    assert client.post("/api/attributions/run", json={"scenario_id": "S5"}).status_code == 200
    response = client.get(f"/api/anomalies/{anomaly_id}/evidence?version=99")
    assert response.status_code == 404
    assert response.json()["detail"] == "未找到归因版本：99"
