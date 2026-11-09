"""Export the real S5 two-version HTTP workflow using a temporary SQLite file."""

import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.api import create_app


OUTPUT_PATH = ROOT / "evidence" / "trace_api_s5_evidence.json"


def request_json(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


def main():
    temp_dir = Path(tempfile.mkdtemp(prefix="tidewatch-api-evidence-"))
    try:
        with TestClient(create_app(str(temp_dir / "trace.db"))) as client:
            first = request_json(client, "POST", "/api/attributions/run", json={"scenario_id": "S5"})
            target = first["target_anomaly"]
            anomaly_id = f"{target['reach_id']}-{target['metric']}-{target['event_time']}"
            evidence_path = f"/api/anomalies/{anomaly_id}/evidence"
            version_1 = request_json(client, "GET", evidence_path, params={"version": 1})
            analysis_time = datetime.fromisoformat(version_1["output"]["analysis_time"])
            sample = {
                "reach_id": "reach-a", "metric": "turbidity", "value": 6,
                "event_time": (analysis_time - timedelta(minutes=10)).isoformat(),
                "received_time": (analysis_time - timedelta(minutes=1)).isoformat(),
                "source": "manual",
            }
            second = request_json(client, "POST", f"/api/anomalies/{anomaly_id}/samples",
                                  json={"scenario_id": "S5", "sample": sample})
            version_2 = request_json(client, "GET", evidence_path)
            preserved = request_json(client, "GET", evidence_path, params={"version": 1})
            assert first["attribution_version"] == version_1["version"] == 1
            assert second["attribution_version"] == version_2["version"] == 2
            assert version_1["run"]["run_id"] != version_2["run"]["run_id"]
            assert preserved["output"] == version_1["output"]
            version_count = len(version_2["versions"])
            assert version_count == 2
            result = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "api_version": client.app.version,
                "workflow": ["POST /api/attributions/run", "POST /api/anomalies/{id}/samples",
                             "GET /api/anomalies/{id}/evidence"],
                "version_1": version_1,
                "version_2": version_2,
                "version_count": version_count,
                "note": "真实 HTTP 调用生成，非手工填写",
            }
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                               encoding="utf-8")
        print(f"生成路径：{OUTPUT_PATH}")
        for key in ("version_1", "version_2"):
            output = result[key]["output"]
            print(f"{key}: status={output['status']}; conclusion={output['conclusion']}")
    finally:
        shutil.rmtree(temp_dir)


if __name__ == "__main__":
    main()
