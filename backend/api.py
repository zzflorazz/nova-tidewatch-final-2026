"""TideWatch Trace workbench queries and versioned attribution operations."""

import json
import sqlite3
from contextlib import asynccontextmanager
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .reranker import Reranker, rerank_attribution
from .store import TraceStore
from .trace_runner import run_attribution


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "trace_scenarios.json"
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "trace.db"
DEMO_PATH = Path(__file__).resolve().parents[1] / "data" / "demo_decoy_scenario.json"
MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "reranker.json"


class RunRequest(BaseModel):
    scenario_id: str
    use_rerank: bool = False


class ScenarioLoadRequest(BaseModel):
    scenario_id: str
    seed: int | None = None


class LateSampleRequest(BaseModel):
    scenario_id: str
    sample: dict


@lru_cache(maxsize=1)
def _load_reranker_file(path: str, mtime_ns: int, ctime_ns: int, size: int) -> Reranker:
    # Only successful loads are cached; the signature changes on replacement.
    return Reranker.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def load_reranker() -> Reranker:
    try:
        path = Path(MODEL_PATH)
        stat = path.stat()
        return _load_reranker_file(str(path.resolve()), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
        raise HTTPException(status_code=503, detail="重排序模型不可用：文件缺失、无法读取或格式无效") from error


def optional_reranking(output: dict) -> dict:
    try:
        model = load_reranker()
    except HTTPException:
        order = [c["source_id"] for c in sorted(
            (c for c in output.get("candidates", [])
             if c.get("role") == "independent_source" and c.get("status") == "retained"),
            key=lambda c: (-c["ranking_score"], c["source_id"]),
        )]
        return {
            "applied": False, "reason": "模型不可用，已降级为仅规则排序",
            "model_version": None, "status": output["status"],
            "rule_order": order, "model_order": list(order), "order_changed": False,
            "candidates": [],
            "constraints": {
                "excluded_untouched": True, "missing_evidence_untouched": True,
                "status_unchanged": True, "propagation_path_excluded_from_ranking": True,
            },
            "note": "仅规则排序；不恢复已排除项，不改变业务结论，不输出因果概率。",
        }
    return rerank_attribution(output, model)


def load_scenarios() -> list[dict]:
    scenarios = [{**s, "origin": "case_book"} for s in
                 json.loads(DATA_PATH.read_text(encoding="utf-8"))["scenarios"]]
    if DEMO_PATH.exists():
        scenarios.append({**json.loads(DEMO_PATH.read_text(encoding="utf-8")), "origin": "self_built_demo"})
    return scenarios


def find_scenario(scenario_id: str) -> dict | None:
    return next((s for s in load_scenarios() if s["scenario_id"] == scenario_id), None)


def create_app(db_path: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Own the SQLite connection in the same thread as the async routes.
        store = TraceStore(db_path or str(DEFAULT_DB_PATH))
        try:
            store.init_schema()
            app.state.store = store
            yield
        finally:
            store.close()

    app = FastAPI(title="TideWatch Trace API", version="1.0.0", lifespan=lifespan)

    def require_anomaly(anomaly_id: str) -> dict:
        anomaly = app.state.store.get_anomaly(anomaly_id)
        if anomaly is None:
            raise HTTPException(status_code=404, detail=f"未找到异常：{anomaly_id}")
        return anomaly

    def require_runnable_scenario(scenario_id: str) -> dict:
        scenario = find_scenario(scenario_id)
        if scenario is None:
            raise HTTPException(status_code=404, detail=f"未找到场景：{scenario_id}")
        if scenario.get("status") != "implemented":
            raise HTTPException(status_code=400, detail=f"场景尚未实现：{scenario_id}")
        if not scenario.get("target"):
            raise HTTPException(status_code=400, detail=f"场景没有目标异常，无法运行归因：{scenario_id}")
        return scenario

    def run_record(run_id: str) -> dict:
        store = app.state.store
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"未找到运行记录：{run_id}")
        record = (store.get_attribution(run["anomaly_id"], run["attribution_version"])
                  if run["anomaly_id"] and run["attribution_version"] is not None else None)
        if record is not None:
            output = record["output_json"]
            run = {**run, "input_snapshot": record["input_snapshot"], "output_json": output,
                   "candidates": output.get("candidates", []),
                   "excluded": output.get("excluded", []), "conclusion": record["conclusion"]}
        return run

    @app.get("/health")
    @app.get("/api/health")
    async def health():
        database = {"status": "error", "path": str(db_path or DEFAULT_DB_PATH), "writable": False}
        connection = app.state.store._db
        try:
            connection.execute("SELECT 1").fetchone()
            database["path"] = next(row[2] or ":memory:" for row in
                                    connection.execute("PRAGMA database_list") if row[1] == "main")
            # An empty UPDATE exercises actual SQLite write permissions, but
            # changes no rows. Always roll the probe back and leave no data.
            connection.execute("SAVEPOINT health_write_probe")
            try:
                connection.execute("UPDATE anomalies SET current_version = current_version WHERE 0")
                database.update(status="ok", writable=True)
            finally:
                connection.execute("ROLLBACK TO health_write_probe")
                connection.execute("RELEASE health_write_probe")
        except (sqlite3.Error, StopIteration):
            database.update(status="error", writable=False)
            database["reason"] = "数据库无法查询或不可写"
        try:
            scenarios = {"status": "ok", "count": len(load_scenarios())}
        except (OSError, ValueError, KeyError, TypeError):
            scenarios = {"status": "error", "count": 0, "reason": "固定场景文件无法读取或格式无效"}
        model = {"status": "loaded", "path": str(MODEL_PATH)}
        try:
            load_reranker()
        except HTTPException as error:
            model.update(status="unavailable", reason=error.detail)
        healthy = database["status"] == scenarios["status"] == "ok" and model["status"] == "loaded"
        return {"status": "ok" if healthy else "degraded", "data_classification": "SIMULATED",
                "database": database, "scenarios": scenarios, "model": model}

    @app.post("/scenario/load")
    @app.post("/api/scenario/load")
    async def load_scenario(request: ScenarioLoadRequest):
        scenario = find_scenario(request.scenario_id)
        if scenario is None:
            raise HTTPException(status_code=404, detail=f"未找到场景：{request.scenario_id}")
        if request.seed is not None and request.seed != scenario["seed"]:
            raise HTTPException(status_code=400, detail="seed 与固定场景定义不一致；仅允许加载固定模拟场景")
        return {
            **{key: scenario.get(key) for key in ("scenario_id", "display_name", "analysis_time", "target")},
            "history_sample_count": len(scenario.get("history_samples", [])),
            "current_sample_count": len(scenario.get("current_samples", [])),
            "local_record_count": len(scenario.get("local_records", [])),
            "data_classification": scenario["data_classification"],
        }

    @app.get("/api/scenarios")
    async def list_scenarios():
        return [
            {key: scenario.get(key) for key in ("scenario_id", "status", "analysis_time", "target", "origin", "display_name", "display_note")}
            for scenario in load_scenarios()
        ]

    @app.get("/api/scenarios/{scenario_id}")
    async def get_scenario(scenario_id: str):
        scenario = find_scenario(scenario_id)
        if scenario is None:
            raise HTTPException(status_code=404, detail=f"未找到场景：{scenario_id}")
        return scenario

    @app.get("/api/anomalies")
    async def list_anomalies():
        return [
            {**anomaly, "latest_version": anomaly["current_version"] - 1}
            for anomaly in app.state.store.list_anomalies()
        ]

    @app.get("/anomalies/{anomaly_id}")
    @app.get("/api/anomalies/{anomaly_id}")
    async def get_anomaly(anomaly_id: str):
        anomaly = require_anomaly(anomaly_id)
        return {"anomaly": anomaly, "latest_version": anomaly["current_version"] - 1,
                "versions": app.state.store.list_attributions(anomaly_id)}

    @app.get("/api/anomalies/{anomaly_id}/attributions")
    async def list_attributions(anomaly_id: str):
        require_anomaly(anomaly_id)
        return [
            {key: row[key] for key in (
                "version", "status", "conclusion", "run_id", "analysis_time", "created_at"
            )}
            for row in app.state.store.list_attributions(anomaly_id)
        ]

    @app.get("/api/anomalies/{anomaly_id}/attributions/{version}")
    async def get_attribution(anomaly_id: str, version: int):
        require_anomaly(anomaly_id)
        attribution = app.state.store.get_attribution(anomaly_id, version)
        if attribution is None:
            raise HTTPException(status_code=404, detail=f"未找到归因版本：{version}")
        return attribution

    @app.get("/api/anomalies/{anomaly_id}/evidence")
    async def get_evidence(anomaly_id: str, version: int | None = None):
        anomaly = require_anomaly(anomaly_id)
        store = app.state.store
        attribution = store.get_attribution(anomaly_id, version)
        if attribution is None:
            detail = "该异常暂无归因版本" if version is None else f"未找到归因版本：{version}"
            raise HTTPException(status_code=404, detail=detail)
        output = attribution["output_json"]
        snapshot = attribution["input_snapshot"]
        return {
            "anomaly": anomaly,
            "version": attribution["version"],
            "versions": [
                {key: row[key] for key in ("version", "status", "conclusion", "run_id", "created_at")}
                for row in store.list_attributions(anomaly_id)
            ],
            "run": run_record(attribution["run_id"]),
            "input_summary": {
                "history_sample_count": len(snapshot.get("history_samples", [])),
                "current_sample_count": len(snapshot.get("current_samples", [])),
                "target": snapshot.get("target", {}),
            },
            "output": output,
            "candidates": [c for c in output.get("candidates", [])
                           if c.get("role") == "independent_source" and c.get("status") == "retained"],
            "excluded": output.get("excluded", []),
            "propagation_path": output.get("propagation_path", []),
            "evidence_gaps": output.get("evidence_gaps", []),
            "next_checks": output.get("next_checks", []),
            "ranking_score_meaning": output.get("ranking_score_meaning", ""),
            "data_classification": output.get("data_classification", ""),
            "note": "候选排序分为相对分，不代表因果概率；排除仅限本场景证据范围。",
        }

    @app.post("/api/attributions/run")
    async def run_scenario(request: RunRequest):
        scenario = require_runnable_scenario(request.scenario_id)
        output = run_attribution(scenario, app.state.store)
        if request.use_rerank:
            output["reranking"] = optional_reranking(output)
        else:
            # A missing model is reported even when inference is not requested;
            # the healthy default response remains unchanged.
            try:
                load_reranker()
            except HTTPException:
                output["reranking"] = optional_reranking(output)
        return output

    @app.get("/api/anomalies/{anomaly_id}/rerank")
    async def get_reranking(anomaly_id: str, version: int | None = None):
        require_anomaly(anomaly_id)
        attribution = app.state.store.get_attribution(anomaly_id, version)
        if attribution is None:
            detail = "该异常暂无归因版本" if version is None else f"未找到归因版本：{version}"
            raise HTTPException(status_code=404, detail=detail)
        result = optional_reranking(attribution["output_json"])
        return {**result, "run_id": attribution["run_id"], "attribution_version": attribution["version"]}

    @app.post("/analyze")
    @app.post("/api/analyze")
    async def analyze(request: RunRequest):
        output = await run_scenario(request)
        target = output["target_anomaly"]
        anomaly_id = f"{target['reach_id']}-{target['metric']}-{target['event_time']}"
        version = output["attribution_version"]
        # Keep the raw run endpoint unchanged; contract clients receive both
        # the original output and the explicit anomaly/attribution envelope.
        record = app.state.store.get_attribution(anomaly_id, version)
        return {**output, "anomaly": require_anomaly(anomaly_id), "version": version,
                "attribution": record}

    @app.get("/anomalies/{anomaly_id}/candidates")
    @app.get("/api/anomalies/{anomaly_id}/candidates")
    async def get_candidates(anomaly_id: str, version: int | None = None):
        require_anomaly(anomaly_id)
        record = app.state.store.get_attribution(anomaly_id, version)
        if record is None:
            raise HTTPException(status_code=404, detail="未找到指定或最新归因版本")
        return record["output_json"].get("candidates", [])

    @app.get("/runs/{run_id}")
    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str):
        return run_record(run_id)

    @app.post("/api/anomalies/{anomaly_id}/samples")
    async def add_late_sample(anomaly_id: str, request: LateSampleRequest):
        scenario = deepcopy(require_runnable_scenario(request.scenario_id))
        target = scenario["target"]
        expected_anomaly_id = f"{target['reach_id']}-{target['metric']}-{target['event_time']}"
        if expected_anomaly_id != anomaly_id:
            raise HTTPException(status_code=400, detail="场景目标异常与路径中的 anomaly_id 不匹配")
        # One current observation per reach/metric is required by attribution.
        # Only replace this analysis snapshot; persisted samples/versions remain.
        key = (request.sample.get("reach_id"), request.sample.get("metric"))
        scenario["current_samples"] = [s for s in scenario["current_samples"]
                                       if (s["reach_id"], s["metric"]) != key]
        scenario["current_samples"].append(request.sample)
        return run_attribution(scenario, app.state.store)

    app.mount("/workbench", StaticFiles(
        directory=Path(__file__).resolve().parents[1] / "web" / "workbench", html=True
    ), name="workbench")
    app.mount("/monitor", StaticFiles(
        directory=Path(__file__).resolve().parents[1] / "web", html=True
    ), name="monitor")
    return app


app = create_app()
