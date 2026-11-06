"""SQLite storage for samples, immutable attribution versions and run metadata.

Times are stored in canonical UTC ISO8601 form. ``current_version`` is the
next attribution version to allocate, starting at 1. Attribution query methods
decode ``input_snapshot`` and ``output_json`` into dictionaries.
"""

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp must be an ISO8601 string with a timezone")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp requires a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Sample value must be a finite number")
    if not math.isfinite(value):
        raise ValueError("Sample value must be a finite number")
    return float(value)


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Identifier and source fields must be nonempty strings")
    return value


class TraceStore:
    def __init__(self, db_path="data/trace.db"):
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), isolation_level=None, timeout=5)
        self._db.row_factory = sqlite3.Row

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    @contextmanager
    def _transaction(self):
        # Serialize version allocation across separate SQLite connections.
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise

    def init_schema(self) -> None:
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS samples (
                id INTEGER PRIMARY KEY,
                reach_id TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL,
                event_time TEXT NOT NULL,
                received_time TEXT NOT NULL,
                source TEXT NOT NULL,
                anomaly_id TEXT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                UNIQUE(reach_id, metric, event_time, version)
            );
            CREATE TABLE IF NOT EXISTS anomalies (
                anomaly_id TEXT PRIMARY KEY,
                reach_id TEXT NOT NULL,
                metric TEXT NOT NULL,
                event_time TEXT NOT NULL,
                value REAL NOT NULL,
                first_detected_at TEXT NOT NULL,
                current_version INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS attributions (
                id INTEGER PRIMARY KEY,
                run_id TEXT NOT NULL UNIQUE,
                anomaly_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                analysis_time TEXT NOT NULL,
                status TEXT NOT NULL,
                conclusion TEXT NOT NULL,
                input_snapshot TEXT NOT NULL,
                output_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(anomaly_id, version)
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                scenario_id TEXT,
                anomaly_id TEXT,
                attribution_version INTEGER,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL CHECK(status IN ('running', 'success', 'failed')),
                error_message TEXT
            );
        """)

    def insert_sample(self, sample: dict, anomaly_id=None) -> int:
        reach_id = _text(sample["reach_id"])
        metric = _text(sample["metric"])
        value = _number(sample["value"])
        event_time = _timestamp(sample["event_time"])
        received_time = _timestamp(sample["received_time"])
        source = _text(sample["source"])
        with self._transaction():
            version = self._db.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM samples "
                "WHERE reach_id = ? AND metric = ? AND event_time = ?",
                (reach_id, metric, event_time),
            ).fetchone()[0]
            self._db.execute(
                "INSERT INTO samples "
                "(reach_id, metric, value, event_time, received_time, source, anomaly_id, version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (reach_id, metric, value, event_time, received_time, source, anomaly_id, version),
            )
        return version

    def get_samples(self, reach_id, metric, before=None) -> list[dict]:
        sql = "SELECT * FROM samples WHERE reach_id = ? AND metric = ?"
        params = [reach_id, metric]
        if before is not None:
            sql += " AND event_time <= ?"
            params.append(_timestamp(before))
        sql += " ORDER BY event_time ASC, version ASC"
        return [dict(row) for row in self._db.execute(sql, params).fetchall()]

    def create_anomaly(self, anomaly_id, reach_id, metric, event_time, value) -> None:
        with self._transaction():
            self._db.execute(
                "INSERT INTO anomalies "
                "(anomaly_id, reach_id, metric, event_time, value, first_detected_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (_text(anomaly_id), _text(reach_id), _text(metric),
                 _timestamp(event_time), _number(value), _now()),
            )

    def get_anomaly(self, anomaly_id) -> dict | None:
        row = self._db.execute(
            "SELECT * FROM anomalies WHERE anomaly_id = ?", (anomaly_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def save_attribution(self, run_id, anomaly_id, analysis_time, status,
                         conclusion, input_snapshot, output) -> int:
        if not isinstance(input_snapshot, dict) or not isinstance(output, dict):
            raise TypeError("input_snapshot and output must be dictionaries")
        snapshot_json = json.dumps(input_snapshot, ensure_ascii=False, allow_nan=False)
        output_json = json.dumps(output, ensure_ascii=False, allow_nan=False)
        analysis_time = _timestamp(analysis_time)
        with self._transaction():
            anomaly = self._db.execute(
                "SELECT current_version FROM anomalies WHERE anomaly_id = ?", (anomaly_id,),
            ).fetchone()
            if anomaly is None:
                raise KeyError(f"Unknown anomaly: {anomaly_id}")
            run = self._db.execute(
                "SELECT anomaly_id FROM runs WHERE run_id = ?", (run_id,),
            ).fetchone()
            if run is not None and run["anomaly_id"] not in (None, anomaly_id):
                raise ValueError("Run belongs to a different anomaly")
            version = anomaly["current_version"]
            self._db.execute(
                "INSERT INTO attributions "
                "(run_id, anomaly_id, version, analysis_time, status, conclusion, "
                "input_snapshot, output_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_text(run_id), anomaly_id, version, analysis_time, _text(status),
                 _text(conclusion), snapshot_json, output_json, _now()),
            )
            self._db.execute(
                "UPDATE anomalies SET current_version = current_version + 1 WHERE anomaly_id = ?",
                (anomaly_id,),
            )
            # If start_run was used, link its metadata in the same transaction.
            self._db.execute(
                "UPDATE runs SET anomaly_id = ?, attribution_version = ? WHERE run_id = ?",
                (anomaly_id, version, run_id),
            )
        return version

    @staticmethod
    def _attribution(row):
        if row is None:
            return None
        result = dict(row)
        result["input_snapshot"] = json.loads(result["input_snapshot"])
        result["output_json"] = json.loads(result["output_json"])
        return result

    def get_attribution(self, anomaly_id, version=None) -> dict | None:
        if version is None:
            anomaly = self.get_anomaly(anomaly_id)
            if anomaly is None:
                return None
            version = anomaly["current_version"] - 1
        row = self._db.execute(
            "SELECT * FROM attributions WHERE anomaly_id = ? AND version = ?",
            (anomaly_id, version),
        ).fetchone()
        return self._attribution(row)

    def list_attributions(self, anomaly_id) -> list[dict]:
        rows = self._db.execute(
            "SELECT * FROM attributions WHERE anomaly_id = ? ORDER BY version ASC",
            (anomaly_id,),
        ).fetchall()
        return [self._attribution(row) for row in rows]

    def start_run(self, run_id, scenario_id=None, anomaly_id=None) -> None:
        with self._transaction():
            self._db.execute(
                "INSERT INTO runs (run_id, scenario_id, anomaly_id, started_at, status) "
                "VALUES (?, ?, ?, ?, 'running')",
                (_text(run_id), scenario_id, anomaly_id, _now()),
            )

    def finish_run(self, run_id, status, error_message=None) -> None:
        if status not in ("success", "failed"):
            raise ValueError("Finished run status must be success or failed")
        with self._transaction():
            row = self._db.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown run: {run_id}")
            if row["status"] != "running":
                raise ValueError("Run is already finished")
            self._db.execute(
                "UPDATE runs SET finished_at = ?, status = ?, error_message = ? WHERE run_id = ?",
                (_now(), status, error_message, run_id),
            )
