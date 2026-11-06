"""S5 persistence, same-anomaly reanalysis and failed-run regression coverage."""

import json
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from backend.store import TraceStore
from backend.trace_runner import run_attribution


@pytest.fixture
def store():
    with TraceStore(":memory:") as instance:
        instance.init_schema()
        yield instance


@pytest.fixture
def scenario():
    path = Path(__file__).resolve().parents[1] / "data/trace_scenarios.json"
    scenarios = json.loads(path.read_text(encoding="utf-8"))["scenarios"]
    return next(item for item in scenarios if item["scenario_id"] == "S5")


def anomaly_id_for(scenario):
    target = scenario["target"]
    return f"{target['reach_id']}-{target['metric']}-{target['event_time']}"


def test_run_attribution_persists_version(store, scenario):
    output = run_attribution(scenario, store)
    assert output["run_id"].startswith("run-")
    assert output["attribution_version"] == 1
    saved = store.get_attribution(anomaly_id_for(scenario), 1)
    assert saved is not None
    assert saved["run_id"] == output["run_id"]
    assert saved["output_json"]["status"] == output["status"]
    assert saved["input_snapshot"] == {
        "history_samples": scenario["history_samples"],
        "current_samples": scenario["current_samples"],
        "target": scenario["target"],
    }
    assert store.get_samples("reach-c", "turbidity")


def test_s5_late_data_creates_new_version(store, scenario):
    # Explicit IDs avoid depending on how quickly two millisecond-clock runs execute.
    first = run_attribution(scenario, store, run_id="run-s5-before")
    assert first["attribution_version"] == 1
    assert first["status"] == "UNRESOLVED"
    anomaly_id = anomaly_id_for(scenario)
    original_version = store.get_attribution(anomaly_id, 1)

    scenario2 = deepcopy(scenario)
    analysis_time = datetime.fromisoformat(scenario2["analysis_time"])
    event_time = analysis_time - timedelta(minutes=10)
    received_time = analysis_time - timedelta(minutes=1)
    age = (analysis_time - event_time).total_seconds() / 60
    assert 0 < age < scenario2["attribution_config"]["max_sample_age_minutes"]
    history = [s for s in scenario2["history_samples"]
               if s["reach_id"] == "reach-a" and s["metric"] == "turbidity"]
    assert all(datetime.fromisoformat(s["event_time"]) < event_time for s in history)
    scenario2["current_samples"].append({
        "reach_id": "reach-a", "metric": "turbidity", "value": 6,
        "event_time": event_time.isoformat(),
        "received_time": received_time.isoformat(), "source": "simulated",
    })
    assert event_time < received_time < analysis_time
    second = run_attribution(scenario2, store, run_id="run-s5-after")
    assert second["attribution_version"] == 2
    assert second["run_id"] != first["run_id"]
    assert anomaly_id_for(scenario2) == anomaly_id
    versions = store.list_attributions(anomaly_id)
    assert len(versions) == 2
    assert [row["version"] for row in versions] == [1, 2]
    assert store.get_attribution(anomaly_id, 1) == original_version
    assert original_version["output_json"]["status"] == "UNRESOLVED"
    saved_second = store.get_attribution(anomaly_id, 2)
    assert saved_second["run_id"] == second["run_id"]
    assert saved_second["output_json"]["status"] == second["status"]
    assert saved_second["output_json"]["data_health"]["reach-a"]["status"] == "fresh"
    assert store.get_attribution(anomaly_id) == saved_second
    assert len(saved_second["input_snapshot"]["current_samples"]) == len(scenario["current_samples"]) + 1
    assert len(original_version["input_snapshot"]["current_samples"]) == len(scenario["current_samples"])


def test_run_attribution_failure_marks_run_failed(store, scenario):
    # Keep a matching target so execution reaches analyze_attribution after start_run.
    scenario["attribution_config"]["max_sample_age_minutes"] = 0
    run_id = "run-s5-failure"
    with pytest.raises(ValueError, match="max_sample_age_minutes") as raised:
        run_attribution(scenario, store, run_id=run_id)
    row = store._db.execute(
        "SELECT * FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert row is not None
    assert row["status"] == "failed"
    assert row["error_message"] == str(raised.value)
    assert row["finished_at"] is not None
    assert store.list_attributions(anomaly_id_for(scenario)) == []
    assert store.get_anomaly(anomaly_id_for(scenario))["current_version"] == 1
