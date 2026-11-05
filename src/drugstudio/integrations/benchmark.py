"""Target-wise virtual-screening metrics for frozen compound score tables."""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from typing import Any, Iterable


def evaluate_virtual_screening(rows: Iterable[dict[str, Any]], *, higher_is_better: bool = False,
                               seed: int = 20261102, bootstrap_iterations: int = 2000,
                               ef_fractions: tuple[float, ...] = (0.01, 0.05, 0.10)) -> dict[str, Any]:
    if bootstrap_iterations < 0:
        raise ValueError("bootstrap_iterations cannot be negative")
    if not ef_fractions or any(not math.isfinite(value) or not 0 < value <= 1 for value in ef_fractions):
        raise ValueError("EF fractions must be finite and in (0, 1]")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    seen = set()
    total = 0
    failed = 0
    for source in rows:
        total += 1
        protocol = str(source.get("protocol", "")).strip()
        target = str(source.get("target_id", "")).strip()
        compound = str(source.get("compound_id", "")).strip()
        if not protocol or not target or not compound:
            raise ValueError("each row requires protocol, target_id and compound_id")
        key = (protocol, target, compound)
        if key in seen:
            raise ValueError(f"duplicate compound in protocol/target group: {key}")
        seen.add(key)
        label = _parse_label(source.get("label"))
        status = str(source.get("status", "complete")).strip().lower() or "complete"
        score_value = source.get("score")
        score = None
        if status in {"complete", "completed", "succeeded", "success"}:
            try:
                score = float(score_value)
            except (TypeError, ValueError):
                raise ValueError(f"completed row has no numeric score: {key}")
            if not math.isfinite(score):
                raise ValueError(f"completed row has a non-finite score: {key}")
            normalized_status = "complete"
        else:
            failed += 1
            normalized_status = "failed"
        grouped[(protocol, target)].append({"compound_id": compound, "label": label,
                                             "score": score, "status": normalized_status})

    target_results = []
    for (protocol, target), group_rows in sorted(grouped.items()):
        scored = [row for row in group_rows if row["status"] == "complete"]
        active_count = sum(row["label"] for row in scored)
        inactive_count = len(scored) - active_count
        metrics: dict[str, float | None] = {
            "roc_auc": _roc_auc(scored, higher_is_better) if active_count and inactive_count else None,
            "average_precision": _average_precision(scored, higher_is_better) if active_count and inactive_count else None,
        }
        for fraction in ef_fractions:
            metrics[f"ef_at_{fraction:g}"] = _enrichment_factor(scored, fraction, higher_is_better) if active_count and inactive_count else None
        target_results.append({
            "protocol": protocol, "target_id": target,
            "compound_count": len(group_rows), "scored_count": len(scored),
            "failed_count": len(group_rows) - len(scored), "active_count_scored": active_count,
            "inactive_count_scored": inactive_count,
            "failure_rate": (len(group_rows) - len(scored)) / len(group_rows),
            "metrics": metrics,
            "status": "evaluable" if active_count and inactive_count else "not_evaluable",
        })

    protocol_results = []
    protocols = sorted({item["protocol"] for item in target_results})
    rng = random.Random(seed)
    for protocol in protocols:
        members = [item for item in target_results if item["protocol"] == protocol]
        metric_names = list(members[0]["metrics"])
        macro = {}
        for metric in metric_names:
            values = [item["metrics"][metric] for item in members if item["metrics"][metric] is not None]
            macro[metric] = {"mean": statistics.mean(values) if values else None,
                             "evaluable_targets": len(values),
                             "bootstrap_95_ci": _bootstrap_ci(values, rng, bootstrap_iterations)}
        protocol_rows = [row for (name, _), grouped_rows in grouped.items() if name == protocol for row in grouped_rows]
        protocol_results.append({
            "protocol": protocol, "target_count": len(members),
            "evaluable_target_count": sum(item["status"] == "evaluable" for item in members),
            "macro_target_metrics": macro,
            "compound_count": len(protocol_rows),
            "scored_count": sum(row["status"] == "complete" for row in protocol_rows),
            "failed_count": sum(row["status"] != "complete" for row in protocol_rows),
            "failure_rate": (sum(row["status"] != "complete" for row in protocol_rows) / len(protocol_rows)) if protocol_rows else None,
        })
    return {
        "schema_version": 1,
        "method": "target-wise ROC-AUC, average precision and tie-aware expected enrichment at top fractions",
        "score_direction": "higher_is_better" if higher_is_better else "lower_is_better",
        "ef_fractions": list(ef_fractions),
        "bootstrap": {"unit": "target within protocol", "iterations": bootstrap_iterations,
                      "seed": seed, "interval": "percentile 95% CI; unavailable for fewer than two evaluable targets"},
        "row_count": total, "failed_row_count": failed,
        "protocols": protocol_results, "targets": target_results,
        "limitations": [
            "Metrics evaluate ranking against supplied active/inactive labels; they do not establish experimental activity or prospective validity.",
            "Target macro-averages are primary; pooled compound metrics are omitted to avoid allowing large targets to dominate.",
            "Failed rows remain in failure-rate denominators but cannot contribute a ranking score.",
            "Benchmark quality depends on assay labels, decoy construction, compound standardization and protocol separation supplied by the caller.",
            "Bootstrap intervals resample targets and are not informative for one-target benchmarks.",
        ],
    }


def _parse_label(value: Any) -> int:
    normalized = str(value).strip().lower()
    if normalized in {"1", "active", "positive", "true"}:
        return 1
    if normalized in {"0", "inactive", "decoy", "negative", "false"}:
        return 0
    raise ValueError(f"label must be active/1 or inactive/0, got {value!r}")


def _ordered(rows: list[dict[str, Any]], higher_is_better: bool) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: ((-row["score"] if higher_is_better else row["score"]),
                                         row["compound_id"]))


def _roc_auc(rows: list[dict[str, Any]], higher_is_better: bool) -> float:
    ranked = sorted(rows, key=lambda row: row["score"], reverse=not higher_is_better)
    positive = sum(row["label"] for row in ranked)
    negative = len(ranked) - positive
    rank_sum = 0.0
    index = 0
    while index < len(ranked):
        end = index + 1
        while end < len(ranked) and ranked[end]["score"] == ranked[index]["score"]:
            end += 1
        average_rank = ((index + 1) + end) / 2
        rank_sum += average_rank * sum(row["label"] for row in ranked[index:end])
        index = end
    return (rank_sum - positive * (positive + 1) / 2) / (positive * negative)


def _average_precision(rows: list[dict[str, Any]], higher_is_better: bool) -> float:
    ranked = _ordered(rows, higher_is_better)
    positives = sum(row["label"] for row in ranked)
    seen = hits = 0
    ap = 0.0
    index = 0
    while index < len(ranked):
        end = index + 1
        while end < len(ranked) and ranked[end]["score"] == ranked[index]["score"]:
            end += 1
        block_hits = sum(row["label"] for row in ranked[index:end])
        block_size = end - index
        seen += block_size
        hits += block_hits
        if block_hits:
            ap += (block_hits / positives) * (hits / seen)
        index = end
    return ap


def _enrichment_factor(rows: list[dict[str, Any]], fraction: float, higher_is_better: bool) -> float:
    ranked = _ordered(rows, higher_is_better)
    top_n = max(1, math.ceil(len(ranked) * fraction))
    selected = hits = 0.0
    index = 0
    while index < len(ranked) and selected < top_n:
        end = index + 1
        while end < len(ranked) and ranked[end]["score"] == ranked[index]["score"]:
            end += 1
        take = min(float(end - index), top_n - selected)
        block_hits = sum(row["label"] for row in ranked[index:end])
        hits += take * block_hits / (end - index)
        selected += take
        index = end
    prevalence = sum(row["label"] for row in ranked) / len(ranked)
    return (hits / selected) / prevalence


def _bootstrap_ci(values: list[float], rng: random.Random, iterations: int) -> list[float] | None:
    if len(values) < 2 or iterations == 0:
        return None
    estimates = sorted(statistics.mean(rng.choice(values) for _ in values) for _ in range(iterations))
    low_index = max(0, math.floor(0.025 * (iterations - 1)))
    high_index = min(iterations - 1, math.ceil(0.975 * (iterations - 1)))
    return [estimates[low_index], estimates[high_index]]
