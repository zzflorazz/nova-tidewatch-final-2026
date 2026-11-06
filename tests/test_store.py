"""Core SQLite storage contracts using an isolated in-memory database per test."""

from datetime import datetime

import pytest

from backend.store import TraceStore


EVENT_TIME = "2026-11-02T09:00:00+08:00"
ANALYSIS_TIME = "2026-11-02T09:30:00+08:00"
ANOMALY_ID = "test-anomaly"


@pytest.fixture
def store():
    with TraceStore(":memory:") as instance:
        instance.init_schema()
        yield instance


def make_sample(value=6, event_time=EVENT_TIME, received_time=ANALYSIS_TIME):
    return {
        "reach_id": "reach-a",
        "metric": "turbidity",
        "value": value,
        "event_time": event_time,
        "received_time": received_time,
        "source": "simulated",
    }


def create_anomaly(store):
    store.create_anomaly(ANOMALY_ID, "reach-a", "turbidity", EVENT_TIME, 24)


def save_version(store, run_id, conclusion):
    return store.save_attribution(
        run_id, ANOMALY_ID, ANALYSIS_TIME, "UNRESOLVED", conclusion,
        {"samples": [make_sample()], "note": conclusion},
        {"status": "UNRESOLVED", "conclusion": conclusion, "candidates": []},
    )


def test_init_schema_idempotent(store):
    # The fixture has already called init_schema once.
    store.init_schema()
    tables = {row["name"] for row in store._db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()}
    assert {"samples", "anomalies", "attributions", "runs"} <= tables


def test_insert_sample_appends_version(store):
    assert store.insert_sample(make_sample(value=6)) == 1
    assert store.insert_sample(make_sample(
        value=9, received_time="2026-11-02T09:31:00+08:00"
    )) == 2
    rows = store.get_samples("reach-a", "turbidity")
    assert [row["version"] for row in rows] == [1, 2]
    assert [row["value"] for row in rows] == [6, 9]
    assert rows[0]["id"] != rows[1]["id"]
    assert rows[0]["received_time"] < rows[1]["received_time"]


def test_get_samples_before_filter(store):
    store.insert_sample(make_sample(3, "2026-11-02T09:20:00+08:00"))
    store.insert_sample(make_sample(1, "2026-11-02T09:00:00+08:00"))
    store.insert_sample(make_sample(2, "2026-11-02T09:10:00+08:00"))
    store.insert_sample(make_sample(2.5, "2026-11-02T09:10:00+08:00"))
    store.insert_sample({**make_sample(99), "reach_id": "reach-b"})
    store.insert_sample({**make_sample(99), "metric": "flow"})
    # This UTC cutoff equals 09:10 +08:00 and includes both boundary versions.
    rows = store.get_samples("reach-a", "turbidity", before="2026-11-02T01:10:00Z")
    assert [row["value"] for row in rows] == [1, 2, 2.5]
    assert [row["value"] for row in store.get_samples("reach-a", "turbidity")] == [1, 2, 2.5, 3]
    assert store.get_samples("reach-a", "turbidity", before="2026-11-02T08:59:00+08:00") == []


def test_create_and_get_anomaly(store):
    assert store.get_anomaly(ANOMALY_ID) is None
    create_anomaly(store)
    row = store.get_anomaly(ANOMALY_ID)
    assert row["anomaly_id"] == ANOMALY_ID
    assert (row["reach_id"], row["metric"], row["value"]) == ("reach-a", "turbidity", 24)
    assert datetime.fromisoformat(row["event_time"]) == datetime.fromisoformat(EVENT_TIME)
    assert datetime.fromisoformat(row["first_detected_at"]).tzinfo is not None
    assert row["current_version"] == 1


def test_save_attribution_increments_version(store):
    create_anomaly(store)
    assert save_version(store, "run-first", "版本一：来源未定") == 1
    first = store.get_attribution(ANOMALY_ID, 1)
    assert save_version(store, "run-second", "版本二：补充证据仍待核实") == 2
    assert store.get_anomaly(ANOMALY_ID)["current_version"] == 3
    assert store.get_attribution(ANOMALY_ID, 1) == first
    assert first["input_snapshot"]["note"] == "版本一：来源未定"
    assert first["output_json"]["conclusion"] == "版本一：来源未定"
    assert store.get_attribution(ANOMALY_ID, 2)["output_json"]["conclusion"] == "版本二：补充证据仍待核实"


def test_get_attribution_latest(store):
    create_anomaly(store)
    assert store.get_attribution(ANOMALY_ID) is None
    save_version(store, "run-first", "旧结论")
    save_version(store, "run-second", "最新结论")
    latest = store.get_attribution(ANOMALY_ID)
    assert latest["version"] == 2
    assert latest["run_id"] == "run-second"
    assert latest["conclusion"] == "最新结论"
    assert latest == store.get_attribution(ANOMALY_ID, 2)
    assert store.get_attribution(ANOMALY_ID, 1)["conclusion"] == "旧结论"


def test_list_attributions(store):
    create_anomaly(store)
    assert store.list_attributions(ANOMALY_ID) == []
    for number in range(1, 4):
        save_version(store, f"run-{number}", f"结论版本{number}")
    rows = store.list_attributions(ANOMALY_ID)
    assert [row["version"] for row in rows] == [1, 2, 3]
    assert [row["run_id"] for row in rows] == ["run-1", "run-2", "run-3"]
    assert all(row["anomaly_id"] == ANOMALY_ID for row in rows)


def test_run_lifecycle(store):
    store.start_run("run-lifecycle", scenario_id="S5")
    running = dict(store._db.execute(
        "SELECT * FROM runs WHERE run_id = ?", ("run-lifecycle",)
    ).fetchone())
    assert running["status"] == "running"
    assert running["scenario_id"] == "S5"
    assert running["finished_at"] is None
    store.finish_run("run-lifecycle", "success")
    finished = dict(store._db.execute(
        "SELECT * FROM runs WHERE run_id = ?", ("run-lifecycle",)
    ).fetchone())
    assert finished["status"] == "success"
    assert finished["finished_at"] >= running["started_at"]
    assert finished["error_message"] is None
    with pytest.raises(KeyError, match="Unknown run"):
        store.finish_run("not-started", "success")


def test_save_attribution_unknown_anomaly(store):
    with pytest.raises(KeyError, match="Unknown anomaly"):
        save_version(store, "run-orphan", "不应保存的结论")
    assert store.list_attributions(ANOMALY_ID) == []
