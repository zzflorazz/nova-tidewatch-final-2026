"""Pure-Python offline logistic reranker; probability is not causal probability."""

import math
import random


FEATURE_NAMES = ("time", "absolute_change", "topology", "local_evidence",
                 "log1p(absolute_change_value)", "time_lag_minutes")
LEARNING_RATE = 0.1
ITERATIONS = 1500
RANDOM_SEED = 42
DEFAULT_L2 = 0.01


def build_scenario_groups(rows):
    """Group copies and attach a same-scenario observed-lag mean, without labels.

    Call separately for each split. Groups with no observed lag cannot supply
    an imputation mean; prediction then explicitly rejects a missing lag.
    """
    groups = {}
    for row in rows:
        groups.setdefault(row["scenario_uid"], []).append({**row, "features": dict(row["features"])})
    for group in groups.values():
        observed = [float(row["features"]["time_lag_minutes"]) for row in group
                    if row["features"]["time_lag_minutes"] is not None]
        if not all(math.isfinite(value) for value in observed):
            raise ValueError("Lag must be finite")
        mean = math.fsum(observed) / len(observed) if observed else None
        for row in group:
            row["_scenario_time_lag_mean"] = mean
    return groups


def _vector(row):
    features = row["features"]
    lag = features["time_lag_minutes"]
    if lag is None:
        lag = row.get("_scenario_time_lag_mean")
        if lag is None:
            raise ValueError("Missing lag requires build_scenario_groups and an observed scenario peer")
    change = float(features["absolute_change_value"])
    if change < 0:
        raise ValueError("absolute_change_value must be nonnegative")
    values = [float(features[key]) for key in FEATURE_NAMES[:4]] + [math.log1p(change), float(lag)]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Features must be finite")
    return values


def standardize(values, means, stds):
    return [(value - mean) / std for value, mean, std in zip(values, means, stds)]


def sigmoid(value):
    if value >= 0:
        result = 1 / (1 + math.exp(-value))
    else:
        exponential = math.exp(value)
        result = exponential / (1 + exponential)
    return min(1 - 1e-15, max(1e-15, result))


def log_loss(labels, probabilities):
    if not labels or len(labels) != len(probabilities):
        raise ValueError("Nonempty labels and equally sized probabilities required")
    return -math.fsum(y * math.log(p) + (1 - y) * math.log1p(-p)
                      for y, p in zip(labels, probabilities)) / len(labels)


def rule_score(row, weights):
    """Recover the existing rule's source score, including hard exclusions."""
    if row["role"] != "independent_source" or row["status"] != "retained":
        return 0.0
    if "ranking_score" in row:
        return float(row["ranking_score"])
    return round(sum(float(weights[key]) * float(row["features"][key])
                     for key in FEATURE_NAMES[:4]), 6)


class Reranker:
    def __init__(self, l2=DEFAULT_L2):
        if not math.isfinite(l2) or l2 < 0:
            raise ValueError("l2 must be finite and nonnegative")
        self.l2 = float(l2)
        self._weights = None

    def fit(self, rows) -> None:
        groups = build_scenario_groups(rows)
        ordered = sorted((row for group in groups.values() for row in group),
                         key=lambda row: (row["scenario_uid"], row["source_id"]))
        if not ordered or any(row["label"] not in (0, 1) for row in ordered):
            raise ValueError("Nonempty binary-labelled rows required")
        raw = [_vector(row) for row in ordered]
        count = len(raw)
        self._means = [math.fsum(column) / count for column in zip(*raw)]
        self._stds = [math.sqrt(math.fsum((x[j] - self._means[j]) ** 2 for x in raw) / count) or 1.0
                      for j in range(len(FEATURE_NAMES))]
        matrix = [standardize(row, self._means, self._stds) for row in raw]
        labels = [row["label"] for row in ordered]
        rng = random.Random(RANDOM_SEED)
        self._weights = [rng.uniform(-0.01, 0.01) for _ in FEATURE_NAMES]
        self._intercept = 0.0
        for _ in range(ITERATIONS):
            gradient = [0.0] * len(FEATURE_NAMES)
            bias_gradient = 0.0
            for x, y in zip(matrix, labels):
                error = sigmoid(self._intercept + sum(w * value for w, value in zip(self._weights, x))) - y
                bias_gradient += error
                for j, value in enumerate(x):
                    gradient[j] += error * value
            self._weights = [w - LEARNING_RATE * (g / count + self.l2 * w)
                             for w, g in zip(self._weights, gradient)]
            self._intercept -= LEARNING_RATE * bias_gradient / count
        probabilities = [sigmoid(self._intercept + sum(w * value for w, value in zip(self._weights, x)))
                         for x in matrix]
        self.training_log_loss = log_loss(labels, probabilities)

    def _check_fitted(self):
        if self._weights is None:
            raise ValueError("Model is not fitted")

    def predict_proba(self, row) -> float:
        self._check_fitted()
        values = standardize(_vector(row), self._means, self._stds)
        return sigmoid(self._intercept + sum(w * value for w, value in zip(self._weights, values)))

    def coefficients(self) -> dict:
        self._check_fitted()
        return dict(zip(FEATURE_NAMES, self._weights))

    def feature_means(self):
        self._check_fitted()
        return dict(zip(FEATURE_NAMES, self._means))

    def feature_stds(self):
        self._check_fitted()
        return dict(zip(FEATURE_NAMES, self._stds))

    def to_dict(self):
        self._check_fitted()
        return {"format_version": 1, "feature_names": list(FEATURE_NAMES),
                "coefficients": self.coefficients(), "intercept": self._intercept,
                "feature_means": self.feature_means(), "feature_stds": self.feature_stds(),
                "l2": self.l2, "learning_rate": LEARNING_RATE, "iterations": ITERATIONS,
                "random_seed": RANDOM_SEED, "training_log_loss": self.training_log_loss}

    @classmethod
    def from_dict(cls, data):
        if data["format_version"] != 1 or data["feature_names"] != list(FEATURE_NAMES):
            raise ValueError("Unsupported model format or feature order")
        model = cls(data["l2"])
        model._weights = [float(data["coefficients"][name]) for name in FEATURE_NAMES]
        model._means = [float(data["feature_means"][name]) for name in FEATURE_NAMES]
        model._stds = [float(data["feature_stds"][name]) for name in FEATURE_NAMES]
        model._intercept = float(data["intercept"])
        model.training_log_loss = float(data["training_log_loss"])
        if (not all(math.isfinite(v) for v in model._weights + model._means + model._stds
                    + [model._intercept, model.training_log_loss]) or any(s <= 0 for s in model._stds)):
            raise ValueError("Model parameters must be finite with positive standard deviations")
        return model


def rerank_attribution(output: dict, model: Reranker) -> dict:
    """Return an explainable ranking sidecar; never mutate business output."""
    import hashlib
    import json

    parameters = model.to_dict()
    fingerprint = hashlib.sha256(json.dumps(
        parameters, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()
    pool = [c for c in output.get("candidates", [])
            if c.get("role") == "independent_source" and c.get("status") == "retained"]
    weights = output.get("ranking_weights", {})

    def feature_row(candidate):
        return {
            "scenario_uid": "same-attribution", "source_id": candidate["source_id"],
            "role": candidate["role"], "status": candidate["status"],
            "features": {
                **{name: candidate["score_components"][name] for name in FEATURE_NAMES[:4]},
                "absolute_change_value": candidate["absolute_change"],
                "time_lag_minutes": candidate["time_lag_minutes"],
            },
        }

    def original_score(candidate):
        if candidate.get("ranking_score") is not None:
            return float(candidate["ranking_score"])
        return rule_score(feature_row(candidate), weights)

    ordered = sorted(pool, key=lambda c: (-original_score(c), c["source_id"]))
    rule_order = [c["source_id"] for c in ordered]
    result = {
        "applied": False, "reason": "", "model_version": f"logreg-v1-{fingerprint}",
        "constraints": {
            "excluded_untouched": True, "missing_evidence_untouched": True,
            "status_unchanged": True, "propagation_path_excluded_from_ranking": True,
        },
        "status": output["status"], "rule_order": rule_order, "model_order": list(rule_order),
        "order_changed": False, "candidates": [],
        "note": "模型仅在同场景保留的独立来源之间重排序，不恢复已排除项，不输出因果概率。",
    }
    if len(pool) < 2:
        result["reason"] = "保留候选不足，未应用模型重排序"
        return result
    try:
        # Match offline preprocessing: group retained sources plus propagation
        # rows before filtering the ranking pool; exclude checks/missing evidence.
        rows = [feature_row(c) for c in output["candidates"] if
                (c.get("role") == "independent_source" and c.get("status") == "retained")
                or (c.get("role") == "propagation_path" and c.get("status") == "propagation")]
        group = build_scenario_groups(rows)["same-attribution"]
        grouped = {row["source_id"]: row for row in group}
        explained = []
        for rank, candidate in enumerate(ordered, 1):
            row = grouped[candidate["source_id"]]
            values = standardize(_vector(row),
                                 [parameters["feature_means"][name] for name in FEATURE_NAMES],
                                 [parameters["feature_stds"][name] for name in FEATURE_NAMES])
            contributions = {name: parameters["coefficients"][name] * value
                             for name, value in zip(FEATURE_NAMES, values)}
            intercept = parameters["intercept"]
            logit = intercept + sum(contributions.values())
            explained.append({
                "source_id": candidate["source_id"], "rule_score": original_score(candidate),
                "rule_rank": rank, "model_score": model.predict_proba(row), "model_rank": None,
                "contributions": contributions, "intercept": intercept, "logit": logit,
                "explanation": "",
            })
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        result["reason"] = f"候选特征不足或无有效场景均值，未应用模型重排序：{error}"
        return result
    model_order = sorted(explained, key=lambda c: (-c["model_score"], c["source_id"]))
    for rank, candidate in enumerate(model_order, 1):
        candidate["model_rank"] = rank
        dominant = max(FEATURE_NAMES, key=lambda name: abs(candidate["contributions"][name]))
        value = candidate["contributions"][dominant]
        direction = "推高" if value > 0 else "压低" if value < 0 else "不改变"
        candidate["explanation"] = (
            f"模型排序第{rank}，主要特征贡献为 {dominant}（{value:+.6f}，{direction}排序 logit）；"
            "相同分数按来源标识升序排列。"
        )
    result.update(applied=True, candidates=explained,
                  model_order=[c["source_id"] for c in model_order])
    result["order_changed"] = result["model_order"] != result["rule_order"]
    return result
