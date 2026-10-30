"""Per-family breakdown of a measured run, from the stored per-task rows."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from ..stats import cluster_bootstrap, mean, template_of


def family_breakdown(document: dict, group: str = "live") -> dict:
    replicates = document["regimes"][group]["replicates"]
    methods = list(replicates[0]["per_method"])
    rows: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for replicate in replicates:
        for method, method_rows in replicate["per_method"].items():
            for row in method_rows:
                rows[row["family"]][method][row["instance_id"]] = row["quality"]
    report: dict[str, dict] = {}
    for family, by_method in sorted(rows.items()):
        static = by_method["static-pckp"]
        entry: dict[str, dict] = {}
        for method in methods:
            qualities = by_method[method]
            groups: dict[str, list[float]] = defaultdict(list)
            for instance_id, value in qualities.items():
                groups[template_of(instance_id)].append(value - static[instance_id])
            entry[method] = {
                "tasks": len(qualities),
                "mean_q": mean(list(qualities.values())),
                "difference_vs_static": cluster_bootstrap(groups)
                if method != "static-pckp"
                else None,
            }
        report[family] = entry
    return report


def render_breakdown(report: dict) -> str:
    lines = [
        "| Family | Method | Tasks | Mean score | Difference vs static [clustered 95% CI] |",
        "|---|---|---|---|---|",
    ]
    for family, entry in report.items():
        for method, row in entry.items():
            diff = row["difference_vs_static"]
            text = (
                "-"
                if diff is None
                else f"{diff['mean']:+.3f} [{diff['ci_low']:+.3f}, {diff['ci_high']:+.3f}]"
            )
            lines.append(f"| {family} | {method} | {row['tasks']} | {row['mean_q']:.3f} | {text} |")
    return "\n".join(lines) + "\n"


def cross_model_summary(document: dict, group: str = "live") -> dict:
    """The same retention decisions, re-scored by other models (no retraining)."""

    replicates = document["regimes"][group]["replicates"]
    models = sorted({m for r in replicates for m in r.get("cross_model", {})})
    report: dict[str, dict] = {}
    for model in models:
        entry: dict[str, dict] = {}
        static = {
            row["instance_id"]: row["quality"]
            for r in replicates
            for row in r["cross_model"][model]["static-pckp"]
        }
        for method in replicates[0]["per_method"]:
            primary = {
                row["instance_id"]: row["quality"]
                for r in replicates
                for row in r["per_method"][method]
            }
            other = {
                row["instance_id"]: row["quality"]
                for r in replicates
                for row in r["cross_model"][model][method]
            }
            groups: dict[str, list[float]] = defaultdict(list)
            for instance_id, value in other.items():
                groups[template_of(instance_id)].append(value - static[instance_id])
            entry[method] = {
                "tasks": len(other),
                "mean_q_primary_model": mean(list(primary.values())),
                "mean_q_other_model": mean(list(other.values())),
                "mean_abs_disagreement": mean([abs(primary[i] - other[i]) for i in other]),
                "difference_vs_static": cluster_bootstrap(groups)
                if method != "static-pckp"
                else None,
            }
        report[model] = entry
    return report


def render_cross_model(report: dict) -> str:
    lines = [
        "| Other model | Method | Tasks | Score (primary model) | Score (other model) | "
        "Mean abs. disagreement | Difference vs static, other model [clustered 95% CI] |",
        "|---|---|---|---|---|---|---|",
    ]
    for model, entry in report.items():
        for method, row in entry.items():
            diff = row["difference_vs_static"]
            text = (
                "-"
                if diff is None
                else f"{diff['mean']:+.3f} [{diff['ci_low']:+.3f}, {diff['ci_high']:+.3f}]"
            )
            lines.append(
                f"| {model.split('/')[-1]} | {method} | {row['tasks']} | "
                f"{row['mean_q_primary_model']:.3f} | {row['mean_q_other_model']:.3f} | "
                f"{row['mean_abs_disagreement']:.3f} | {text} |"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    source = Path(arguments[0] if arguments else "results/live/live_results.json")
    document = json.loads(source.read_text(encoding="utf-8"))
    group = arguments[1] if len(arguments) > 1 else "live"
    report = family_breakdown(document, group)
    destination = source.with_name("by_family.md")
    destination.write_text(render_breakdown(report), encoding="utf-8")
    print(destination.read_text(encoding="utf-8"))
    cross = cross_model_summary(document, group)
    if cross:
        cross_destination = source.with_name("cross_model.md")
        cross_destination.write_text(render_cross_model(cross), encoding="utf-8")
        print(cross_destination.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
