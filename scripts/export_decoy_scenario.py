"""Export one reproducible self-built demo using the existing decoy constructor."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.generate_training_data import build_scenario

DEMO_SEED = 20261113
OUTPUT_PATH = ROOT / "data/demo_decoy_scenario.json"


def build_demo():
    scenario, truth = build_scenario("decoy_local", DEMO_SEED)
    scenario.update(
        scenario_id="D1", status="implemented", data_classification="SIMULATED",
        origin="self_built_demo", injected_source_ids=sorted(truth),
        demo_note="自建对照场景：用于展示规则排序误判与模型排序纠正，不属于案例书 S1–S5；注入真源为 reach-a，不代表真实水体因果证明。",
    )
    return scenario


def main():
    OUTPUT_PATH.write_text(json.dumps(build_demo(), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                           encoding="utf-8")
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
