#!/usr/bin/env python3
"""
tornhill-join-risk — deterministic churn × centrality join.

Consumes the two pre-existing tornhill artifacts — the git miner JSON
(tornhill-mine-git.py) and the graph JSON (tornhill-mine-graph.py, or any Tier-1
producer emitting schema tornhill.graph/v1) — aggregates file churn to the SAME
module-depth as the graph nodes, and ranks hotspot modules by a composite risk
score. This makes SKILL.md's headline "churn × centrality hotspot" finding
DETERMINISTIC instead of LLM-reasoned.

Producer-agnostic: it reads only the graph's node degrees, module_depth, and
centrality_quality — never the producer. So an import-miner graph (approx) and an
LSP / MCP graph (precise) join identically; only the stamped precision differs.

Risk = percentile-rank product. Churn and degree are each ranked to [0,1] across
modules, then multiplied. Rank is invariant to magnitude/skew (robust to a few
mega-churn files) and the PRODUCT enforces the AND: a module must score high on
BOTH change pressure and centrality to surface — a high-churn leaf or a stable hub
both fall out. This matches the finding's semantics exactly.

Pure stdlib. Read-only. No network.

Usage:
    python tornhill-join-risk.py --git <git.json> --graph <graph.json> [options]

Options:
    --git <path>     git miner JSON (required)
    --graph <path>   graph JSON, schema tornhill.graph/v1 (required)
    --top <n>        rows (default 15)
    --score percentile|minmax|product   default percentile
    --format json|md     default json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def module_of(path: str, depth: int) -> str:  # identical to the miners (alignment)
    parts = path.split("/")
    return "/".join(parts[:depth]) if len(parts) > depth else path


def load_json(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        raise SystemExit(1)


def percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    """Map each key's value to its [0,1] percentile rank. Ties get the average
    rank. A single value (or all-equal) maps to 1.0."""
    items = sorted(values.items(), key=lambda kv: kv[1])
    n = len(items)
    if n == 1:
        return {items[0][0]: 1.0}
    ranks: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        avg_pos = (i + j) / 2  # 0-based average index of the tie group
        rank = avg_pos / (n - 1)
        for k in range(i, j + 1):
            ranks[items[k][0]] = rank
        i = j + 1
    return ranks


def composite(churn: float, degree: float, rc: float, rd: float,
              method: str, max_churn: float, max_degree: float) -> float:
    if method == "product":
        return churn * degree
    if method == "minmax":
        return (churn / max_churn if max_churn else 0.0) * \
               (degree / max_degree if max_degree else 0.0)
    return rc * rd  # percentile (default)


def join(git: dict, graph: dict, method: str) -> dict:
    depth = graph["module_depth"]
    quality = graph.get("centrality_quality", "unknown")

    module_churn: dict[str, int] = defaultdict(int)
    for row in git.get("churn", []):
        module_churn[module_of(row["path"], depth)] += row["touches"]

    nodes = {n["path"]: n for n in graph.get("nodes", [])}
    degree_map = {p: n["degree"] for p, n in nodes.items()}

    # universe = modules present in BOTH signals (need churn AND a graph node)
    both = sorted(set(module_churn) & set(degree_map))
    rc = percentile_ranks({m: module_churn[m] for m in both}) if both else {}
    rd = percentile_ranks({m: degree_map[m] for m in both}) if both else {}
    max_churn = max((module_churn[m] for m in both), default=0)
    max_degree = max((degree_map[m] for m in both), default=0)

    hotspots = []
    for m in both:
        n = nodes[m]
        risk = composite(module_churn[m], degree_map[m], rc[m], rd[m],
                         method, max_churn, max_degree)
        hotspots.append({
            "module": m, "path": m,
            "churn": module_churn[m], "degree": degree_map[m],
            "degree_in": n["degree_in"], "degree_out": n["degree_out"],
            "churn_rank": round(rc[m], 3), "degree_rank": round(rd[m], 3),
            "risk": round(risk, 4),
            "cite": f"{m} (churn {module_churn[m]}; in-degree {n['degree_in']})",
        })
    hotspots.sort(key=lambda h: (-h["risk"], -h["churn"], h["module"]))

    # churn present but no graph node -> surface the graph-resolution gap, don't hide it
    churn_only = [{"module": m, "churn": module_churn[m], "note": "no graph node"}
                  for m in sorted(set(module_churn) - set(degree_map))]

    return {
        "schema": "tornhill.risk/v1",
        "producer": "join-risk",
        "centrality_quality": quality,
        "module_depth": depth,
        "score_method": method,
        "hotspots": hotspots,
        "churn_only": churn_only,
    }


def to_md(data: dict, top: int) -> str:
    L = [f"# churn × centrality risk — derived join",
         f"_score: {data['score_method']} · centrality_quality: "
         f"{data['centrality_quality']} · module_depth: {data['module_depth']}_\n",
         "## Hotspots (risk-ordered)\n",
         "| module | churn | degree (in/out) | risk |",
         "|--------|-------|-----------------|------|"]
    for h in data["hotspots"][:top]:
        L.append(f"| `{h['module']}` | {h['churn']} | "
                 f"{h['degree']} ({h['degree_in']}/{h['degree_out']}) | {h['risk']} |")
    if not data["hotspots"]:
        L.append("| _none — no module had both churn and a graph node_ | | | |")
    if data["churn_only"]:
        L.append("\n## Churn without a graph node (resolution gap)\n")
        L += ["| module | churn |", "|--------|-------|"]
        for c in data["churn_only"][:top]:
            L.append(f"| `{c['module']}` | {c['churn']} |")
    L.append(f"\n> centrality_quality={data['centrality_quality']} — when `approx`, "
             "degrees come from regex import resolution; treat as directional.")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Join churn × centrality into a risk ranking.")
    ap.add_argument("--git", required=True, help="tornhill-mine-git.py JSON")
    ap.add_argument("--graph", required=True, help="tornhill-mine-graph.py JSON (tornhill.graph/v1)")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--score", choices=["percentile", "minmax", "product"], default="percentile")
    ap.add_argument("--format", choices=["json", "md"], default="json")
    args = ap.parse_args()

    git = load_json(args.git)
    graph = load_json(args.graph)
    if graph.get("schema") != "tornhill.graph/v1":
        print(f"--graph is not tornhill.graph/v1 (got {graph.get('schema')!r})", file=sys.stderr)
        return 1
    # alignment guard: a git miner run at a different module depth would mis-key churn.
    git_depth = git.get("module_depth")
    if git_depth is not None and git_depth != graph["module_depth"]:
        print(f"module_depth mismatch: git={git_depth} graph={graph['module_depth']} "
              "— re-run both miners with the same --module-depth", file=sys.stderr)
        return 1

    data = join(git, graph, args.score)
    print(to_md(data, args.top) if args.format == "md" else json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
