"""Export actual versioned attribution and constraint-preserving reranking."""

import json
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.reranker import Reranker, rerank_attribution
from backend.store import TraceStore
from backend.trace_runner import run_attribution


SCENARIO_IDS = ("S2", "S3", "S4a", "S4b", "S5")


def main():
    scenarios = {s["scenario_id"]: s for s in json.loads(
        (ROOT / "data/trace_scenarios.json").read_text(encoding="utf-8"))["scenarios"]}
    model = Reranker.from_dict(json.loads((ROOT / "models/reranker.json").read_text(encoding="utf-8")))
    records = []
    with tempfile.TemporaryDirectory(prefix="tidewatch-rerank-contract-") as temp_dir:
        with TraceStore(str(Path(temp_dir) / "trace.db")) as store:
            store.init_schema()
            for sid in SCENARIO_IDS:
                s = deepcopy(scenarios[sid])
                target = s["target"]
                anomaly_id = f"{target['reach_id']}-{target['metric']}-{target['event_time']}"
                run_attribution(s, store, run_id=f"rerank-contract-{sid}-v1")
                if sid == "S5":
                    cutoff = datetime.fromisoformat(s["analysis_time"])
                    s["current_samples"].append({
                        "reach_id": "reach-a", "metric": "turbidity", "value": 6,
                        "event_time": (cutoff - timedelta(minutes=10)).isoformat(),
                        "received_time": (cutoff - timedelta(minutes=1)).isoformat(), "source": "manual",
                    })
                    run_attribution(s, store, run_id=f"rerank-contract-{sid}-v2")
                versions = []
                for attribution in store.list_attributions(anomaly_id):
                    original = attribution["output_json"]
                    before = deepcopy(original)
                    reranking = rerank_attribution(original, model)
                    assert original == before
                    assert reranking["status"] == original["status"]
                    assert all(reranking["constraints"].values())
                    versions.append({"version": attribution["version"], "run_id": attribution["run_id"],
                                     "output": original, "reranking": reranking})
                latest = versions[-1]["reranking"]
                records.append({
                    "scenario_id": sid,
                    **{key: latest[key] for key in ("status", "rule_order", "model_order", "order_changed",
                                                    "constraints", "applied", "reason", "model_version")},
                    "versions": versions,
                })
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "data_classification": "SIMULATED",
        "scenarios": records,
        "summary": {"total": len(records), "order_changed": sum(r["order_changed"] for r in records),
                    "skipped": sum(not r["applied"] for r in records)},
        "note": "模型仅重排序，不改变业务判断；summary 按五个场景的最新版统计，S5 同时保留版本1与补数后的版本2。",
    }
    path = ROOT / "evidence/rerank_contract.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"生成路径：{path}")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
