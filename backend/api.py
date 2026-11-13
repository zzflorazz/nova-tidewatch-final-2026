"""TideWatch Trace workbench queries and versioned attribution operations."""

import json
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


class LateSampleRequest(BaseModel):
    scenario_id: str
    sample: dict


@lru_cache(maxsize=1)
def load_reranker() -> Reranker:
    try:
        return Reranker.from_dict(json.loads(MODEL_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(status_code=503, detail="重排序模型不可用") from error


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

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "data_classification": "SIMULATED"}

    @app.get("/api/scenarios")
    async def list_scenarios():
        return [
            {key: scenario.get(key) for key in ("scenario_id", "status", "analysis_time", "target", "origin")}
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

    @app.get("/api/anomalies/{anomaly_id}")
    async def get_anomaly(anomaly_id: str):
        anomaly = require_anomaly(anomaly_id)
        return {"anomaly": anomaly, "latest_version": anomaly["current_version"] - 1}

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
            "run": store.get_run(attribution["run_id"]),
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
            output["reranking"] = rerank_attribution(output, load_reranker())
        return output

    @app.get("/api/anomalies/{anomaly_id}/rerank")
    async def get_reranking(anomaly_id: str, version: int | None = None):
        require_anomaly(anomaly_id)
        attribution = app.state.store.get_attribution(anomaly_id, version)
        if attribution is None:
            detail = "该异常暂无归因版本" if version is None else f"未找到归因版本：{version}"
            raise HTTPException(status_code=404, detail=detail)
        result = rerank_attribution(attribution["output_json"], load_reranker())
        return {**result, "run_id": attribution["run_id"], "attribution_version": attribution["version"]}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str):
        run = app.state.store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"未找到运行记录：{run_id}")
        return run

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
    return app


app = create_app()
