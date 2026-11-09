"""Persist scenario samples and record one version of a Trace attribution."""

import sqlite3
import time

from .attribution import analyze_attribution
from .store import TraceStore


def run_attribution(scenario: dict, store: TraceStore, run_id: str | None = None) -> dict:
    """Persist samples, compute attribution and save an append-only version.

    The caller initializes the store schema. The anomaly value comes from the
    current sample matching the target; existing anomaly records are retained.
    """
    if run_id is None:
        run_id = f"run-{int(time.time() * 1000)}"

    target = scenario["target"]
    reach_id = target["reach_id"]
    metric = target["metric"]
    event_time = target["event_time"]
    anomaly_id = f"{reach_id}-{metric}-{event_time}"
    target_sample = next(
        (sample for sample in scenario["current_samples"]
         if sample["reach_id"] == reach_id and sample["metric"] == metric
         and sample["event_time"] == event_time),
        None,
    )
    if target_sample is None:
        raise ValueError("No current sample matches the target anomaly")

    try:
        store.create_anomaly(anomaly_id, reach_id, metric, event_time, target_sample["value"])
    except sqlite3.IntegrityError:
        # Ignore an existing anomaly, but propagate unrelated integrity errors.
        if store.get_anomaly(anomaly_id) is None:
            raise

    store.start_run(run_id, scenario.get("scenario_id"), anomaly_id)
    try:
        for sample in scenario["history_samples"]:
            if not store.sample_exists(
                sample["reach_id"], sample["metric"], sample["event_time"], sample["value"]
            ):
                store.insert_sample(sample, anomaly_id=anomaly_id)
        for sample in scenario["current_samples"]:
            if not store.sample_exists(
                sample["reach_id"], sample["metric"], sample["event_time"], sample["value"]
            ):
                store.insert_sample(sample, anomaly_id=anomaly_id)

        output = analyze_attribution(scenario)
        input_snapshot = {
            "history_samples": scenario["history_samples"],
            "current_samples": scenario["current_samples"],
            "target": scenario["target"],
        }
        version = store.save_attribution(
            run_id, anomaly_id, scenario["analysis_time"],
            output["status"], output["conclusion"], input_snapshot, output,
        )
    except Exception as error:
        store.finish_run(run_id, "failed", str(error))
        raise

    store.finish_run(run_id, "success")
    output["run_id"] = run_id
    output["attribution_version"] = version
    return output
