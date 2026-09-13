#!/usr/bin/env python3
"""Audit whether Router Opportunity labels are predictable from observables.

The audit is intentionally model-light and observable-only.  It evaluates the
frozen J7.1 rule, a small one-vs-rest softmax logistic model, and a depth-3
multiclass tree.  It never trains an MLP and never changes the training gate.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_router_materiality import (  # noqa: E402
    build_headroom_rows,
    finite,
    read_csv,
    write_csv,
    write_json,
)
from safe_expert_selector import (  # noqa: E402
    SELECTOR_PROFILES,
    select_complexity_aware_expert_with_reason,
    ComplexitySelectorThresholds,
)


CURRENT = "A0"
ACTION_IDS = ("A0", "A1", "A2", "A3", "A4", "A5")
FEATURE_ID = "scene_id"
FORBIDDEN_FEATURE_FRAGMENTS = (
    "mechanism", "true_delay", "true_phase", "true_rho", "target_truth",
    "l_causal", "l_target_only", "safe", "oracle", "headroom", "pd",
    "pfa", "false_clusters", "best_safe", "action", "family", "label",
)


def _finite_array(values: Sequence[Any]) -> np.ndarray:
    data = np.asarray([finite(value) for value in values], dtype=float)
    return data


def _entropy(labels: Sequence[str]) -> float:
    if not labels:
        return 0.0
    counts = Counter(labels)
    total = float(len(labels))
    return float(-sum((count / total) * math.log2(count / total)
                      for count in counts.values()))


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits, axis=1, keepdims=True)
    exponent = np.exp(np.clip(shifted, -60.0, 60.0))
    return exponent / np.sum(exponent, axis=1, keepdims=True)


class MulticlassLogistic:
    def __init__(self, classes: Sequence[str], iterations: int = 600,
                 learning_rate: float = 0.15, l2: float = 0.02) -> None:
        self.classes = tuple(classes)
        self.iterations = iterations
        self.learning_rate = learning_rate
        self.l2 = l2
        self.mean: np.ndarray | None = None
        self.scale: np.ndarray | None = None
        self.weights: np.ndarray | None = None

    def fit(self, matrix: np.ndarray, labels: Sequence[str]) -> None:
        if matrix.ndim != 2 or matrix.shape[0] != len(labels):
            raise ValueError("logistic matrix/label shape mismatch")
        self.mean = np.nanmean(matrix, axis=0)
        self.mean = np.where(np.isfinite(self.mean), self.mean, 0.0)
        self.scale = np.nanstd(matrix, axis=0)
        self.scale = np.where(np.isfinite(self.scale) & (self.scale > 1.0e-12),
                              self.scale, 1.0)
        normalized = np.where(np.isfinite(matrix), matrix, self.mean)
        normalized = (normalized - self.mean) / self.scale
        design = np.column_stack([np.ones(normalized.shape[0]), normalized])
        class_count = len(self.classes)
        self.weights = np.zeros((class_count, design.shape[1]), dtype=float)
        class_index = {name: index for index, name in enumerate(self.classes)}
        target = np.zeros((len(labels), class_count), dtype=float)
        for row_index, label in enumerate(labels):
            if label not in class_index:
                raise ValueError(f"unknown logistic class: {label}")
            target[row_index, class_index[label]] = 1.0
        if class_count == 1:
            return
        for _ in range(self.iterations):
            probabilities = _softmax(design @ self.weights.T)
            gradient = ((probabilities - target).T @ design) / design.shape[0]
            gradient[:, 1:] += self.l2 * self.weights[:, 1:]
            self.weights -= self.learning_rate * gradient

    def predict_proba(self, matrix: np.ndarray) -> np.ndarray:
        if self.mean is None or self.scale is None or self.weights is None:
            raise RuntimeError("logistic model is not fitted")
        normalized = np.where(np.isfinite(matrix), matrix, self.mean)
        normalized = (normalized - self.mean) / self.scale
        design = np.column_stack([np.ones(normalized.shape[0]), normalized])
        if len(self.classes) == 1:
            return np.ones((matrix.shape[0], 1), dtype=float)
        return _softmax(design @ self.weights.T)

    def predict(self, matrix: np.ndarray) -> tuple[list[str], np.ndarray]:
        probabilities = self.predict_proba(matrix)
        indices = np.argmax(probabilities, axis=1)
        return ([self.classes[int(index)] for index in indices],
                probabilities[np.arange(probabilities.shape[0]), indices])


@dataclass
class TreeNode:
    probabilities: dict[str, float]
    feature: int | None = None
    threshold: float | None = None
    left: "TreeNode | None" = None
    right: "TreeNode | None" = None


class ShallowTree:
    def __init__(self, classes: Sequence[str], max_depth: int = 3) -> None:
        self.classes = tuple(classes)
        self.max_depth = max_depth
        self.root: TreeNode | None = None

    @staticmethod
    def _probabilities(labels: Sequence[str]) -> dict[str, float]:
        counts = Counter(labels)
        total = float(len(labels))
        return {key: count / total for key, count in counts.items()} if total else {}

    def _fit_node(self, matrix: np.ndarray, labels: list[str], depth: int) -> TreeNode:
        node = TreeNode(self._probabilities(labels))
        if depth >= self.max_depth or len(set(labels)) <= 1 or len(labels) < 3:
            return node
        parent_entropy = _entropy(labels)
        best_gain = 0.0
        best: tuple[int, float, np.ndarray, np.ndarray] | None = None
        for feature in range(matrix.shape[1]):
            values = matrix[:, feature]
            finite_values = values[np.isfinite(values)]
            unique = np.unique(finite_values)
            if unique.size < 2:
                continue
            candidates = (unique[:-1] + unique[1:]) / 2.0
            if candidates.size > 24:
                indices = np.linspace(0, candidates.size - 1, 24).astype(int)
                candidates = candidates[indices]
            for threshold in candidates:
                left_mask = np.isfinite(values) & (values <= threshold)
                right_mask = np.isfinite(values) & (values > threshold)
                if not np.any(left_mask) or not np.any(right_mask):
                    continue
                left_labels = [labels[index] for index in np.flatnonzero(left_mask)]
                right_labels = [labels[index] for index in np.flatnonzero(right_mask)]
                weighted = (len(left_labels) * _entropy(left_labels)
                            + len(right_labels) * _entropy(right_labels)) / len(labels)
                gain = parent_entropy - weighted
                if gain > best_gain:
                    best_gain = gain
                    best = (feature, float(threshold), left_mask, right_mask)
        if best is None:
            return node
        feature, threshold, left_mask, right_mask = best
        node.feature = feature
        node.threshold = threshold
        node.left = self._fit_node(
            matrix[left_mask], [labels[index] for index in np.flatnonzero(left_mask)], depth + 1)
        node.right = self._fit_node(
            matrix[right_mask], [labels[index] for index in np.flatnonzero(right_mask)], depth + 1)
        return node

    def fit(self, matrix: np.ndarray, labels: Sequence[str]) -> None:
        if matrix.ndim != 2 or matrix.shape[0] != len(labels):
            raise ValueError("tree matrix/label shape mismatch")
        self.root = self._fit_node(matrix, list(labels), 0)

    def _predict_one(self, row: np.ndarray, node: TreeNode) -> dict[str, float]:
        if node.feature is None or node.left is None or node.right is None:
            return node.probabilities
        value = row[node.feature]
        if not math.isfinite(float(value)):
            return node.probabilities
        return self._predict_one(
            row, node.left if value <= float(node.threshold) else node.right)

    def predict(self, matrix: np.ndarray) -> tuple[list[str], np.ndarray]:
        if self.root is None:
            raise RuntimeError("tree model is not fitted")
        predictions: list[str] = []
        confidences: list[float] = []
        for row in matrix:
            probabilities = self._predict_one(row, self.root)
            if not probabilities:
                predictions.append(CURRENT)
                confidences.append(0.0)
                continue
            label, confidence = max(probabilities.items(), key=lambda item: item[1])
            predictions.append(label)
            confidences.append(float(confidence))
        return predictions, np.asarray(confidences, dtype=float)


def spearman(values: np.ndarray, labels: np.ndarray) -> float:
    finite_mask = np.isfinite(values) & np.isfinite(labels)
    values = values[finite_mask]
    labels = labels[finite_mask]
    if values.size < 2 or np.std(values) == 0.0 or np.std(labels) == 0.0:
        return math.nan
    value_ranks = np.argsort(np.argsort(values)).astype(float)
    label_ranks = np.argsort(np.argsort(labels)).astype(float)
    return float(np.corrcoef(value_ranks, label_ranks)[0, 1])


def mutual_information(values: np.ndarray, labels: np.ndarray, bins: int = 4) -> float:
    finite_mask = np.isfinite(values) & np.isfinite(labels)
    values = values[finite_mask]
    labels = labels[finite_mask].astype(int)
    if values.size < 2 or len(np.unique(labels)) < 2:
        return 0.0
    edges = np.unique(np.quantile(values, np.linspace(0.0, 1.0, bins + 1)))
    if edges.size < 3:
        return 0.0
    bucket = np.clip(np.digitize(values, edges[1:-1], right=False), 0, edges.size - 2)
    total = float(values.size)
    result = 0.0
    for bucket_id in np.unique(bucket):
        for label in (0, 1):
            joint = float(np.sum((bucket == bucket_id) & (labels == label))) / total
            if joint == 0.0:
                continue
            p_bucket = float(np.mean(bucket == bucket_id))
            p_label = float(np.mean(labels == label))
            result += joint * math.log(joint / (p_bucket * p_label), 2)
    return float(result)


def effect_size(values: np.ndarray, labels: np.ndarray) -> float:
    positive = values[(labels == 1) & np.isfinite(values)]
    negative = values[(labels == 0) & np.isfinite(values)]
    if positive.size < 1 or negative.size < 1:
        return math.nan
    pooled = math.sqrt(max(
        ((positive.size - 1) * np.var(positive, ddof=1) if positive.size > 1 else 0.0)
        + ((negative.size - 1) * np.var(negative, ddof=1) if negative.size > 1 else 0.0),
        0.0) / max(positive.size + negative.size - 2, 1))
    if pooled <= 0.0:
        return math.nan
    return float((np.mean(positive) - np.mean(negative)) / pooled)


def validate_feature_columns(rows: list[Mapping[str, Any]],
                             forbidden: Sequence[str]) -> list[str]:
    if not rows:
        raise ValueError("inference feature table is empty")
    columns = [key for key in rows[0] if key != FEATURE_ID]
    if not columns:
        raise ValueError("inference feature table has no numeric columns")
    fragments = tuple(str(value).lower() for value in forbidden) + FORBIDDEN_FEATURE_FRAGMENTS
    bad = [column for column in columns
           if any(fragment in column.lower() for fragment in fragments)]
    if bad:
        raise ValueError(f"forbidden feature columns: {bad}")
    for row in rows:
        for column in columns:
            if row.get(column, "") == "":
                continue
            if not math.isfinite(finite(row.get(column))):
                raise ValueError(f"non-numeric inference feature: {column}")
    return columns


def _j71_features(row: Mapping[str, Any]) -> dict[str, float]:
    return {
        "joint_confidence": finite(row.get("joint_confidence")),
        "joint_rmse_rad": finite(row.get("joint_rmse_rad")),
        "residual_coherence": finite(row.get("joint_residual_coherence")),
        "tau_ns": finite(row.get("tau_ns")),
        "beta1_deg_per_pulse": finite(row.get("beta1_deg_per_pulse")),
        "beta2_deg_per_pulse2": finite(row.get("beta2_deg_per_pulse2")),
        "d3_j6_delay_disagreement_ns": finite(
            row.get("d3_j6_delay_disagreement_ns")),
        "p1_j6_phase_disagreement_deg_per_pulse": finite(
            row.get("p1_j6_phase_disagreement_deg_per_pulse")),
        "raw_coherence": finite(row.get("raw_coherence")),
        "support_fraction": finite(row.get("support_fraction")),
        "delay_confidence": finite(row.get("delay_confidence")),
        "phase_confidence": finite(row.get("phase_confidence")),
        "phase_signal": finite(row.get("phase_signal")),
        "delay_signal": finite(row.get("delay_signal_ns")),
        "delay_extrapolation_ratio": finite(row.get("delay_extrapolation_ratio")),
        "abs_beta2_deg_per_pulse2": finite(row.get("abs_beta2_deg_per_pulse2")),
        "joint_vs_p1_residual_coherence_gain": finite(
            row.get("joint_minus_p1_residual_coherence_gain")),
    }


def j71_predict(feature_row: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[str, float, str]:
    thresholds = ComplexitySelectorThresholds(0.005, 0.02)
    method, reason = select_complexity_aware_expert_with_reason(
        _j71_features(feature_row), SELECTOR_PROFILES["strict"], thresholds)
    action_map = config["learnability"]["j71_action_map"]
    return str(action_map.get(method, CURRENT)), 1.0 if method != "J0_Current" else 0.0, reason


def _candidate_lookup(candidate_rows: list[Mapping[str, Any]]) -> dict[tuple[str, str], Mapping[str, Any]]:
    output: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in candidate_rows:
        key = (str(row.get("scene_id")), str(row.get("action_id")))
        if key in output:
            raise ValueError(f"duplicate candidate row: {key}")
        output[key] = row
    return output


def _metrics(predictions: list[tuple[str, str, str, float]],
             candidate_lookup: Mapping[tuple[str, str], Mapping[str, Any]],
             oracle_by_scene: Mapping[str, Mapping[str, Any]],
             low_confidence_threshold: float = 0.60) -> dict[str, Any]:
    if not predictions:
        return {"scene_count": 0}
    active = 0
    safe_active = 0
    unsafe = 0
    catastrophic = 0
    captured = 0.0
    oracle_total = float(sum(finite(row.get("safe_headroom_db"), 0.0)
                             for row in oracle_by_scene.values()))
    for scene_id, action_id, _, confidence in predictions:
        if action_id != CURRENT:
            active += 1
        candidate = candidate_lookup[(scene_id, action_id)]
        safe = str(candidate.get("safe", "")).lower() in {"1", "true", "yes"}
        lost = finite(candidate.get("current_detected_targets_lost"), 0.0) > 0.0
        if action_id != CURRENT and safe:
            safe_active += 1
            captured += max(0.0, finite(candidate.get("gain_vs_Current"), 0.0))
        if action_id != CURRENT and not safe:
            unsafe += 1
        if action_id != CURRENT and lost:
            catastrophic += 1
    count = len(predictions)
    return {
        "scene_count": count,
        "active_count": active,
        "safe_action_precision": safe_active / active if active else 1.0,
        "unsafe_activation_rate": unsafe / count,
        "unsafe_activation_rate_given_active": unsafe / active if active else 0.0,
        "catastrophic_target_loss_activation_rate": catastrophic / count,
        "catastrophic_target_loss_rate_given_active": catastrophic / active if active else 0.0,
        "abstain_rate": (count - active) / count,
        "captured_safe_headroom_db": captured,
        "oracle_safe_headroom_db": oracle_total,
        "captured_safe_headroom_fraction": captured / oracle_total if oracle_total > 0 else 0.0,
        "low_confidence_current_count": sum(
            action_id == CURRENT and confidence < low_confidence_threshold
            for _, action_id, _, confidence in predictions),
    }


def _split_groups(records: list[Mapping[str, Any]]) -> dict[str, dict[str, list[int]]]:
    groups: dict[str, dict[str, list[int]]] = {"seed-out": {}, "family-out": {}}
    for index, row in enumerate(records):
        groups["seed-out"].setdefault(str(row["seed"]), []).append(index)
        groups["family-out"].setdefault(str(row["family"]), []).append(index)
    angles = np.asarray([finite(row.get("geometry_look_angle_deg")) for row in records])
    order = np.argsort(np.where(np.isfinite(angles), angles, 0.0))
    for rank, index in enumerate(order):
        groups.setdefault("angle-out", {}).setdefault(f"angle_bin_{rank * 4 // len(records)}", []).append(int(index))
    return groups


def _fit_predictor(name: str, matrix: np.ndarray, labels: list[str],
                   train: list[int], test: list[int]) -> tuple[list[str], np.ndarray]:
    def class_order(value: str) -> tuple[int, str]:
        return (ACTION_IDS.index(value), value) if value in ACTION_IDS else (len(ACTION_IDS), value)

    classes = tuple(sorted(set(labels[index] for index in train), key=class_order))
    if name == "logistic_regression":
        model = MulticlassLogistic(classes)
    elif name == "depth3_tree":
        model = ShallowTree(classes, max_depth=3)
    else:
        raise ValueError(name)
    model.fit(matrix[train], [labels[index] for index in train])
    return model.predict(matrix[test])


def _binary_metrics(predicted: Sequence[str], actual: Sequence[str]) -> dict[str, float]:
    if not predicted:
        return {"positive_precision": math.nan, "positive_recall": math.nan,
                "balanced_accuracy": math.nan}
    true_positive = sum(pred == "1" and label == "1"
                        for pred, label in zip(predicted, actual))
    false_positive = sum(pred == "1" and label == "0"
                         for pred, label in zip(predicted, actual))
    true_negative = sum(pred == "0" and label == "0"
                        for pred, label in zip(predicted, actual))
    false_negative = sum(pred == "0" and label == "1"
                         for pred, label in zip(predicted, actual))
    positive_precision = (true_positive / (true_positive + false_positive)
                          if true_positive + false_positive else 1.0)
    positive_recall = (true_positive / (true_positive + false_negative)
                       if true_positive + false_negative else 1.0)
    negative_recall = (true_negative / (true_negative + false_positive)
                       if true_negative + false_positive else 1.0)
    return {
        "positive_precision": positive_precision,
        "positive_recall": positive_recall,
        "balanced_accuracy": (positive_recall + negative_recall) / 2.0,
    }


def _effect_rows(records: list[Mapping[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    labels = np.asarray([int(finite(row.get("material_safe_opportunity"), 0.0))
                         for row in records], dtype=float)
    rows: list[dict[str, Any]] = []
    for column in columns:
        values = np.asarray([finite(row.get(column)) for row in records], dtype=float)
        rows.append({
            "feature": column,
            "effect_size_cohens_d": effect_size(values, labels),
            "spearman_rank_correlation": spearman(values, labels),
            "mutual_information_bits": mutual_information(values, labels),
        })
    return rows


def _feature_stability_rows(records: list[Mapping[str, Any]],
                            columns: list[str],
                            split_groups: Mapping[str, Mapping[str, list[int]]]
                            ) -> list[dict[str, Any]]:
    labels = np.asarray([int(finite(row.get("material_safe_opportunity"), 0.0))
                         for row in records], dtype=float)
    output: list[dict[str, Any]] = []
    for column in columns:
        effects: list[float] = []
        for split_name, groups in split_groups.items():
            for group_name, held_out in groups.items():
                held_out_set = set(held_out)
                train = [index for index in range(len(records)) if index not in held_out_set]
                values = np.asarray([finite(records[index].get(column)) for index in train], dtype=float)
                train_labels = labels[train]
                value = effect_size(values, train_labels)
                if math.isfinite(value):
                    effects.append(value)
        data = np.asarray(effects, dtype=float)
        output.append({
            "feature": column,
            "split_effect_count": int(data.size),
            "split_effect_mean": float(np.mean(data)) if data.size else math.nan,
            "split_effect_std": float(np.std(data, ddof=1)) if data.size > 1 else math.nan,
            "split_effect_min": float(np.min(data)) if data.size else math.nan,
            "split_effect_max": float(np.max(data)) if data.size else math.nan,
        })
    return output


def audit_dataset(output_dir: Path, config: Mapping[str, Any],
                  materiality: Mapping[str, Any]) -> dict[str, Any]:
    feature_rows = read_csv(output_dir / "router_inference_visible_features.csv")
    candidate_rows = read_csv(output_dir / "router_candidate_metrics.csv")
    selection_rows = read_csv(output_dir / "router_scene_selection.csv")
    metadata_rows = read_csv(output_dir / "router_scene_metadata.csv")
    columns = validate_feature_columns(feature_rows, config["forbidden_feature_inputs"])
    features_by_scene = {str(row[FEATURE_ID]): row for row in feature_rows}
    metadata_by_scene = {str(row["scene_id"]): row for row in metadata_rows}
    headroom_rows = build_headroom_rows(
        candidate_rows, selection_rows,
        float(config["learnability"]["material_label_threshold_db"]),
    )
    records: list[dict[str, Any]] = []
    for row in headroom_rows:
        scene_id = str(row["scene_id"])
        if scene_id not in features_by_scene or scene_id not in metadata_by_scene:
            raise ValueError(f"missing feature/metadata row for {scene_id}")
        records.append({
            **row,
            **{key: finite(value) for key, value in features_by_scene[scene_id].items()
               if key != FEATURE_ID},
            "seed": metadata_by_scene[scene_id]["seed"],
            "geometry_look_angle_deg": metadata_by_scene[scene_id].get(
                "look_angle_deg", features_by_scene[scene_id].get("geometry_look_angle_deg")),
        })
    matrix = np.asarray([[finite(row.get(column)) for column in columns]
                         for row in records], dtype=float)
    labels = [str(row["best_safe_action"]) for row in records]
    y1_labels = ["1" if bool(row["material_safe_opportunity"]) else "0"
                 for row in records]
    lookup = _candidate_lookup(candidate_rows)
    oracle_by_scene = {str(row["scene_id"]): row for row in records}
    baseline_results: dict[str, dict[str, Any]] = {}
    j71_predictions = []
    for row in records:
        action, confidence, reason = j71_predict(row, config)
        j71_predictions.append((str(row["scene_id"]), action, reason, confidence))
    low_confidence_threshold = float(
        config["learnability"]["low_confidence_abstain_threshold"])
    baseline_results["J7.1_rule"] = _metrics(
        j71_predictions, lookup, oracle_by_scene, low_confidence_threshold)
    split_rows: list[dict[str, Any]] = []
    y1_split_rows: list[dict[str, Any]] = []
    split_groups = _split_groups(records)
    stability_rows = _feature_stability_rows(records, columns, split_groups)
    for split_name, groups in split_groups.items():
        for group_name, test in sorted(groups.items()):
            train = [index for index in range(len(records)) if index not in set(test)]
            for model_name in ("logistic_regression", "depth3_tree"):
                predictions, confidence = _fit_predictor(
                    model_name, matrix, labels, train, test)
                packed = [
                    (str(records[index]["scene_id"]),
                     prediction if conf >= float(config["learnability"]["low_confidence_abstain_threshold"])
                     else CURRENT,
                     "model",
                     float(conf))
                    for index, prediction, conf in zip(test, predictions, confidence)
                ]
                metrics = _metrics(
                    packed, lookup, oracle_by_scene, low_confidence_threshold)
                split_rows.append({
                    "split": split_name,
                    "held_out_group": group_name,
                    "model": model_name,
                    **metrics,
                })
                y1_predicted, y1_confidence = _fit_predictor(
                    model_name, matrix, y1_labels, train, test)
                y1_packed = [
                    prediction if confidence >= float(
                        config["learnability"]["low_confidence_abstain_threshold"])
                    else "0"
                    for prediction, confidence in zip(y1_predicted, y1_confidence)
                ]
                y1_split_rows.append({
                    "split": split_name,
                    "held_out_group": group_name,
                    "model": model_name,
                    "scene_count": len(test),
                    **_binary_metrics(
                        y1_packed, [y1_labels[index] for index in test]),
                })
    for model_name in ("logistic_regression", "depth3_tree"):
        selected = [row for row in split_rows if row["model"] == model_name]
        baseline_results[model_name] = {
            "split_count": len(selected),
            "safe_action_precision_mean": float(np.mean([
                finite(row["safe_action_precision"]) for row in selected])) if selected else math.nan,
            "unsafe_activation_rate_max": float(np.max([
                finite(row["unsafe_activation_rate"]) for row in selected])) if selected else math.nan,
            "catastrophic_target_loss_activation_rate_max": float(np.max([
                finite(row["catastrophic_target_loss_activation_rate"]) for row in selected]))
            if selected else math.nan,
            "captured_safe_headroom_fraction_mean": float(np.mean([
                finite(row["captured_safe_headroom_fraction"]) for row in selected])) if selected else math.nan,
            "split_metrics": selected,
        }
    y1_baseline_results: dict[str, dict[str, Any]] = {}
    for model_name in ("logistic_regression", "depth3_tree"):
        selected = [row for row in y1_split_rows if row["model"] == model_name]
        y1_baseline_results[model_name] = {
            "split_count": len(selected),
            "positive_precision_mean": float(np.mean([
                finite(row["positive_precision"]) for row in selected])) if selected else math.nan,
            "positive_recall_mean": float(np.mean([
                finite(row["positive_recall"]) for row in selected])) if selected else math.nan,
            "balanced_accuracy_mean": float(np.mean([
                finite(row["balanced_accuracy"]) for row in selected])) if selected else math.nan,
            "split_metrics": selected,
        }
    thresholds = config["learnability"]
    eligible: dict[str, bool] = {}
    for name, result in baseline_results.items():
        precision = finite(result.get("safe_action_precision", result.get(
            "safe_action_precision_mean")))
        unsafe = finite(result.get("unsafe_activation_rate", result.get(
            "unsafe_activation_rate_max")))
        catastrophic = finite(result.get("catastrophic_target_loss_activation_rate",
                                     result.get("catastrophic_target_loss_activation_rate_max")))
        captured = finite(result.get("captured_safe_headroom_fraction",
                                  result.get("captured_safe_headroom_fraction_mean")))
        eligible[name] = bool(
            math.isfinite(precision) and precision >= float(thresholds["safe_action_precision_min"])
            and math.isfinite(unsafe) and unsafe <= float(thresholds["unsafe_activation_rate_max"])
            and math.isfinite(catastrophic) and catastrophic <= float(
                thresholds["catastrophic_target_loss_activation_rate_max"])
            and math.isfinite(captured) and captured >= float(
                thresholds["captured_safe_headroom_fraction_min"]))
    simple_captured = [
        finite(baseline_results[name].get("captured_safe_headroom_fraction",
                                          baseline_results[name].get("captured_safe_headroom_fraction_mean")))
        for name in baseline_results if math.isfinite(finite(
            baseline_results[name].get("captured_safe_headroom_fraction",
                                       baseline_results[name].get("captured_safe_headroom_fraction_mean"))))]
    simple_recovered_most = bool(simple_captured and max(simple_captured) >= float(
        thresholds["simple_selector_headroom_fraction_max"]))
    materiality_passed = bool(materiality.get("materiality_gate_passed", False))
    observable_learnability = bool(
        any(eligible.get(name, False) for name in ("logistic_regression", "depth3_tree")))
    training_gate_passed = bool(materiality_passed and observable_learnability
                                and not simple_recovered_most)
    return {
        "schema_version": 1,
        "analysis": "Router Observable-Only Learnability Audit",
        "router_state": "GO_AI_ROUTER_TRAINING" if training_gate_passed
        else "REOPEN_ROUTER_RESEARCH",
        "training_state": "GO_AI_ROUTER_TRAINING",
        "ai_training": False,
        "training_authorized": False,
        "training_gate_passed": training_gate_passed,
        "materiality_gate_passed": materiality_passed,
        "observable_only_learnability_exists": observable_learnability,
        "deterministic_or_simple_selector_recovered_most_headroom": simple_recovered_most,
        "labels": {
            "Y1": (
                "material_safe_opportunity = safe_headroom_db >= "
                f"{float(config['learnability']['material_label_threshold_db']):g} dB"
            ),
            "Y2": "best_safe_action",
        },
        "feature_columns": columns,
        "forbidden_feature_inputs": config["forbidden_feature_inputs"],
        "effect_rank_mi": _effect_rows(records, columns),
        "baseline_results": baseline_results,
        "y1_baseline_results": y1_baseline_results,
        "baseline_eligibility": eligible,
        "split_metrics": split_rows,
        "y1_split_metrics": y1_split_rows,
        "feature_stability": {
            "split_families": ["seed-out", "angle-out", "family-out"],
            "definition": "feature effect/rank/MI are recomputed only from observable columns; held-out groups are never used to fit models",
            "rows": stability_rows,
        },
        "policy": {
            "low_confidence_action": "A0",
            "mlp_trained": False,
            "source_commit_flip_required_before_training": True,
        },
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "outputs/router_opportunity_v1")
    parser.add_argument("--config", type=Path,
                        default=ROOT / "configs/research/router_opportunity_v1.json")
    parser.add_argument("--materiality-summary", type=Path, default=None)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    materiality_path = (args.materiality_summary.resolve()
                        if args.materiality_summary else output / "router_materiality_summary.json")
    if not materiality_path.is_file():
        raise SystemExit("Materiality summary is required before learnability audit")
    materiality = json.loads(materiality_path.read_text(encoding="utf-8"))
    if not bool(materiality.get("materiality_gate_passed")):
        raise SystemExit("Materiality Gate has not passed; learnability audit is not authorized")
    result = audit_dataset(output, config, materiality)
    write_json(output / "router_learnability_summary.json", result)
    write_csv(output / "router_learnability_effect_rank_mi.csv", result["effect_rank_mi"])
    write_csv(output / "router_learnability_split_metrics.csv", result["split_metrics"])
    write_csv(output / "router_learnability_y1_split_metrics.csv",
              result["y1_split_metrics"])
    write_csv(output / "router_learnability_feature_stability.csv",
              result["feature_stability"]["rows"])
    write_csv(output / "router_learnability_labels.csv", [
        {key: row.get(key) for key in (
            "scene_id", "family", "seed", "safe_headroom_db",
            "material_safe_opportunity", "best_safe_action")}
        for row in result["records"]
    ])
    print(json.dumps({
        "output_dir": str(output),
        "router_state": result["router_state"],
        "training_gate_passed": result["training_gate_passed"],
        "ai_training": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
