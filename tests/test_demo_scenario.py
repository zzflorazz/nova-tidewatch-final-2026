"""Self-built demo stays reproducible and separate from case-book scenarios."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client():
    with TestClient(create_app(":memory:")) as instance:
        yield instance


def test_demo_scenario_reproducible():
    script = ROOT / "scripts/export_decoy_scenario.py"
    path = ROOT / "data/demo_decoy_scenario.json"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True, capture_output=True)
    first = path.read_bytes()
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True, capture_output=True)
    assert path.read_bytes() == first
    demo = json.loads(first)
    assert demo["scenario_id"] == "D1"
    assert demo["origin"] == "self_built_demo"
    assert demo["status"] == "implemented"
    assert demo["data_classification"] == "SIMULATED"
    assert "不属于案例书" in demo["demo_note"]


def test_scenarios_include_demo(client):
    response = client.get("/api/scenarios")
    assert response.status_code == 200
    rows = response.json()
    assert [s["scenario_id"] for s in rows if s["origin"] == "case_book"] == ["S1", "S2", "S3", "S4a", "S4b", "S5"]
    assert [s["scenario_id"] for s in rows if s["origin"] == "self_built_demo"] == ["D1"]
    assert len(rows) == 7


def test_demo_run_and_rerank_differs(client):
    response = client.post("/api/attributions/run", json={"scenario_id": "D1", "use_rerank": True})
    assert response.status_code == 200
    result = response.json()
    reranking = result["reranking"]
    demo = client.get("/api/scenarios/D1").json()
    assert reranking["rule_order"] == ["reach-c:local", "reach-a"]
    assert reranking["rule_order"] != reranking["model_order"]
    assert reranking["model_order"][0] in demo["injected_source_ids"]
    assert reranking["order_changed"] is True
    assert result["status"] == reranking["status"] == "MULTI_CANDIDATE"
    assert all(reranking["constraints"].values())
    assert all(c["contributions"] for c in reranking["candidates"])


def test_case_book_scenarios_unchanged():
    scenarios = {s["scenario_id"]: s for s in json.loads((ROOT / "data/trace_scenarios.json").read_text())["scenarios"]}
    assert list(scenarios) == ["S1", "S2", "S3", "S4a", "S4b", "S5"]
    assert scenarios["S1"].get("target") is None
    assert [s["value"] for s in scenarios["S1"]["current_samples"]] == [6.25, 1.025, 12.25, 1.525, 24.25, 2.025]
    expected = {
        "S2": ("2026-11-02T09:25:00+08:00", [80, 40, 25]),
        "S3": ("2026-11-02T11:00:00+08:00", [70, 5.1, 40]),
        "S4a": ("2026-11-02T10:20:00+08:00", [24, 15, 27]),
        "S4b": ("2026-11-02T16:40:00+08:00", [7, 6, 26]),
        "S5": ("2026-11-02T14:30:00+08:00", [12, 30]),
    }
    for sid, (time, values) in expected.items():
        assert scenarios[sid]["target"] == {"reach_id": "reach-c", "metric": "turbidity", "event_time": time}
        assert [s["value"] for s in scenarios[sid]["current_samples"]] == values


def test_demo_backfill_uses_generic_versioned_endpoint(client):
    first = client.post("/api/attributions/run", json={"scenario_id": "D1"})
    assert first.status_code == 200
    demo = client.get("/api/scenarios/D1").json()
    target = demo["target"]
    anomaly_id = f"{target['reach_id']}-{target['metric']}-{target['event_time']}"
    original = client.get(f"/api/anomalies/{anomaly_id}/attributions/1").json()
    sample = dict(next(s for s in demo["current_samples"] if s["reach_id"] == "reach-a"))
    sample.update(value=sample["value"] + 1, source="manual")
    second = client.post(f"/api/anomalies/{anomaly_id}/samples", json={"scenario_id": "D1", "sample": sample})
    assert second.status_code == 200
    assert second.json()["attribution_version"] == 2
    assert client.get(f"/api/anomalies/{anomaly_id}/attributions/1").json() == original
    assert client.get("/api/scenarios/D1").json() == demo
