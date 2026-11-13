"""Train and evaluate an offline logistic reranker using only standard Python."""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.reranker import FEATURE_NAMES, Reranker, build_scenario_groups, rule_score


LIMITATIONS = [
    "训练与评估均基于程序化生成数据，不代表真实水体场景准确率",
    "decoy 族中 true source 与 decoy 在 time 特征上差异明显，模型可能高估泛化能力",
    "候选排序概率为相对分，不是因果概率",
]


def load_rule_weights():
    scenarios = json.loads((ROOT / "data/trace_scenarios.json").read_text(encoding="utf-8"))["scenarios"]
    weights = [s["attribution_config"]["ranking_weights"] for s in scenarios if "attribution_config" in s]
    if not weights or any(w != weights[0] for w in weights):
        raise ValueError("Cannot recover a common rule score: scenario weights differ")
    return weights[0]


def load_rows(path):
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    seen = {}
    for row in rows:
        if row["split"] not in ("train", "test") or row["label"] not in (0, 1):
            raise ValueError("Invalid split or label")
        uid = row["scenario_uid"]
        if uid in seen and seen[uid] != row["split"]:
            raise ValueError("Scenario crosses train/test splits")
        seen[uid] = row["split"]
    train = [r for r in rows if r["split"] == "train"]
    test = [r for r in rows if r["split"] == "test"]
    if not train or not test:
        raise ValueError("Both train and test data are required")
    return train, test


def evaluate(model, rows, weights):
    groups = build_scenario_groups(rows)
    results = {strategy: [] for strategy in ("rule", "model", "fusion")}
    details = []
    families = {}
    single_candidate = 0
    bound = sum(weights.values())
    for uid, group in sorted(groups.items()):
        eligible = [r for r in group if r["role"] == "independent_source" and r["status"] == "retained"]
        if not eligible or sum(r["label"] for r in eligible) != 1:
            raise ValueError(f"Expected one true eligible source: {uid}")
        family = group[0]["family"]
        if any(r["family"] != family for r in group):
            raise ValueError("Inconsistent scenario family")
        single_candidate += int(len(eligible) == 1)
        scores = {r["source_id"]: {"rule": rule_score(r, weights), "model": model.predict_proba(r)} for r in eligible}
        for values in scores.values():
            values["fusion"] = 0.5 * (values["rule"] / bound) + 0.5 * values["model"]
        true_source = next(r["source_id"] for r in eligible if r["label"] == 1)
        detail = {"scenario_uid": uid, "family": family, "true_source": true_source, "strategies": {}}
        for strategy in results:
            ranked = sorted(eligible, key=lambda r: (-scores[r["source_id"]][strategy], r["source_id"]))
            rank = next(i for i, r in enumerate(ranked, 1) if r["label"] == 1)
            hit = int(rank == 1)
            results[strategy].append((hit, 1 / rank))
            families.setdefault(family, {}).setdefault(strategy, []).append(hit)
            detail["strategies"][strategy] = {"top_source": ranked[0]["source_id"], "true_source_rank": rank}
        details.append(detail)
    metrics = {s: {"top1_accuracy": sum(h for h, _ in values) / len(values),
                   "mrr": sum(rr for _, rr in values) / len(values), "scenarios": len(values)}
               for s, values in results.items()}
    by_family = {f: {s: {"top1_accuracy": sum(hits) / len(hits), "scenarios": len(hits)}
                     for s, hits in strategies.items()} for f, strategies in families.items()}
    return metrics, by_family, details, single_candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data/simulated_candidates.jsonl")
    parser.add_argument("--l2", type=float, default=0.01)
    args = parser.parse_args()
    train, test = load_rows(args.data)
    model = Reranker(l2=args.l2)
    model.fit(train)
    weights = load_rule_weights()
    metrics, by_family, details, single_candidate = evaluate(model, test, weights)
    model_path = ROOT / "models/reranker.json"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_text = json.dumps(model.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if len(model_text.encode("utf-8")) >= 50 * 1024:
        raise ValueError("Model exceeds 50KB")
    model_path.write_text(model_text, encoding="utf-8")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "data_classification": "SIMULATED",
        "train_scenarios": len(build_scenario_groups(train)), "test_scenarios": len(build_scenario_groups(test)),
        "train_candidate_rows": len(train), "test_candidate_rows": len(test),
        "feature_names": list(FEATURE_NAMES), "coefficients": model.coefficients(),
        "feature_means": model.feature_means(), "feature_stds": model.feature_stds(),
        "metrics": metrics, "by_family": by_family, "limitations": LIMITATIONS,
        "training": {"l2": model.l2, "iterations": model.to_dict()["iterations"],
                     "learning_rate": model.to_dict()["learning_rate"], "log_loss": model.training_log_loss},
        "evaluation_policy": {"eligible": "retained independent_source only, same pool for all strategies",
                              "single_candidate_test_scenarios": single_candidate,
                              "rule_weights": weights, "rule_normalization": "score / sum(weights)",
                              "tie_break": "source_id ascending", "fusion_model_weight": 0.5},
        "data_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
        "model_sha256": hashlib.sha256(model_text.encode("utf-8")).hexdigest(),
        "scenario_results": details,
    }
    path = ROOT / "evidence/model_comparison.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("strategy       top-1       MRR")
    for strategy, value in metrics.items():
        print(f"{strategy:10} {value['top1_accuracy']:10.6f} {value['mrr']:10.6f}")
    print(json.dumps({"by_family": by_family, "coefficients": model.coefficients()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
