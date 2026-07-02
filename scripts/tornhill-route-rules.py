#!/usr/bin/env python3
"""
tornhill-route-rules — the deterministic rule router (the engine's core).

It reads the rule catalog (rules/*.yml) plus the deterministic candidate outputs
(signals.json, routes.json, git.json, risk.json, graph.json) and, for EACH rule in
the requested pack, buckets only the evidence that rule's `consumes` tags match.
The result — ruleplan.json — is a list of per-rule WORK PACKETS. The skill then
walks it one rule at a time, so each analysis pass sees only its own rule + its own
evidence and can never conflate two rules.

Pure stdlib except PyYAML (already required by the renderer). Read-only. No network.

Usage:
    python tornhill-route-rules.py [--rules <dir>] --signals signals.json \
        --routes routes.json [--git git.json] [--risk risk.json] [--graph graph.json] \
        [--pack arch_pack|audit_pack|all] [--top 25] [--format json|md]

Defaults: --rules resolves to the bundled catalog (../rules relative to this file),
--pack all. Any input JSON may be omitted; rules needing a missing source simply get
an empty bucket (honestly surfaced, never invented).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(1)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

STATE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


# ---------- catalog ----------

def load_catalog(rules_dir: Path) -> tuple[dict, list[dict]]:
    idx = yaml.safe_load((rules_dir / "index.yml").read_text(encoding="utf-8"))
    rules: list[dict] = []
    seen: set[str] = set()
    for fam, fname in idx["families"].items():
        path = rules_dir / fname
        if fname in seen:
            continue
        seen.add(fname)
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        file_family = doc.get("family")
        for r in doc.get("rules", []):
            r.setdefault("family", file_family)
            rules.append(r)
    return idx, rules


def families_for_pack(idx: dict, pack: str) -> set[str]:
    if pack == "all":
        return set(idx["families"])
    return set(idx["packs"].get(pack, []))


# ---------- evidence routing ----------

def route_signals(rule: dict, signals: list[dict], top: int) -> list[dict]:
    wanted = set((rule.get("consumes") or {}).get("signals") or [])
    if not wanted:
        return []
    out = [{"subtype": f"{s['family']}/{s['subtype']}", "cite": s["cite"],
            "excerpt": s.get("excerpt", "")}
           for s in signals
           if f"{s['family']}/{s['subtype']}" in wanted]
    return out[:top]


def route_routes(rule: dict, routes: list[dict], top: int) -> list[dict]:
    tokens = (rule.get("consumes") or {}).get("routes") or []
    if not tokens:
        return []
    # group tokens by dimension so [has_auth:true, has_auth:false] = no auth constraint
    auth_vals = {t.split(":", 1)[1] for t in tokens if t.startswith("has_auth:")}
    need_state = "state_mutating" in tokens
    auth_constraint = None
    if auth_vals == {"true"}:
        auth_constraint = True
    elif auth_vals == {"false"}:
        auth_constraint = False
    out = []
    for r in routes:
        if auth_constraint is not None and bool(r.get("has_auth")) != auth_constraint:
            continue
        if need_state and str(r.get("method", "")).upper() not in STATE_METHODS:
            continue
        out.append({"method": r.get("method"), "url": r.get("url"),
                    "cite": r.get("cite") or r.get("file"),
                    "has_auth": bool(r.get("has_auth"))})
    return out[:top]


def route_git(rule: dict, git: dict, top: int) -> dict:
    wanted = (rule.get("consumes") or {}).get("git") or []
    if not wanted or not git:
        return {}
    return {k: (git.get(k) or [])[:top] for k in wanted}


def route_risk(rule: dict, risk: dict, top: int) -> list[dict]:
    if "hotspots" not in ((rule.get("consumes") or {}).get("risk") or []):
        return []
    return (risk.get("hotspots") or [])[:top] if risk else []


def find_cycles(edges: list[dict], limit: int) -> list[list[str]]:
    adj: dict[str, list[str]] = {}
    for e in edges:
        adj.setdefault(e["src"], []).append(e["dst"])
    cycles: list[list[str]] = []
    seen_keys: set[frozenset] = set()

    def dfs(start: str, node: str, path: list[str]):
        if len(cycles) >= limit:
            return
        for nxt in adj.get(node, []):
            if nxt == start and len(path) >= 2:
                key = frozenset(path)
                if key not in seen_keys:
                    seen_keys.add(key)
                    cycles.append(path + [start])
            elif nxt not in path and len(path) < 6:
                dfs(start, nxt, path + [nxt])

    for n in list(adj):
        if len(cycles) >= limit:
            break
        dfs(n, n, [n])
    return cycles


def route_graph(rule: dict, graph: dict, top: int) -> list[dict]:
    tokens = (rule.get("consumes") or {}).get("graph") or []
    if not tokens or not graph:
        return []
    out: list[dict] = []
    if "degree" in tokens:
        nodes = sorted(graph.get("nodes", []),
                       key=lambda n: n.get("degree", 0), reverse=True)
        out += [{"kind": "node", "id": n["id"], "path": n.get("path"),
                 "degree": n.get("degree"), "degree_in": n.get("degree_in"),
                 "degree_out": n.get("degree_out"), "cite": n.get("path")}
                for n in nodes[:top]]
    if "cycle" in tokens:
        for cyc in find_cycles(graph.get("edges", []), top):
            out.append({"kind": "cycle", "path_chain": cyc})
    return out


def build(idx: dict, rules: list[dict], pack: str, data: dict, top: int) -> dict:
    fams = families_for_pack(idx, pack)
    signals = (data.get("signals") or {}).get("signals", [])
    routes = (data.get("routes") or {}).get("routes", [])
    git = data.get("git") or {}
    risk = data.get("risk") or {}
    graph = data.get("graph") or {}

    plan = []
    routed = 0
    for rule in rules:
        if rule.get("family") not in fams:
            continue
        ev = {
            "signals": route_signals(rule, signals, top),
            "routes": route_routes(rule, routes, top),
            "git": route_git(rule, git, top),
            "risk": route_risk(rule, risk, top),
            "graph": route_graph(rule, graph, top),
        }
        n = (len(ev["signals"]) + len(ev["routes"]) + len(ev["risk"])
             + len(ev["graph"]) + sum(len(v) for v in ev["git"].values()))
        routed += n
        plan.append({
            "rule_id": rule["id"],
            "family": rule.get("family"),
            "title": rule.get("title", ""),
            "deterministic": bool(rule.get("deterministic", False)),
            "evidence_required": rule.get("evidence_required", ["code_anchor"]),
            "evidence": ev,
            "evidence_count": n,
        })

    sha = ""
    for key in ("signals", "routes", "git", "risk", "graph"):
        d = data.get(key)
        if d and d.get("derived_from"):
            sha = d["derived_from"]
            break
    with_ev = sum(1 for p in plan if p["evidence_count"] > 0)
    return {
        "schema": "tornhill.ruleplan/v1",
        "catalog_version": idx.get("catalog_version"),
        "pack": pack,
        "derived_from": sha,
        "sources": {k: bool(data.get(k)) for k in
                    ("signals", "routes", "git", "risk", "graph")},
        "counts": {"rules": len(plan), "rules_with_evidence": with_ev,
                   "candidates_routed": routed},
        "note": "per-rule work packets — confirm each rule against real code, one at a time",
        "rules": plan,
    }


def to_md(data: dict) -> str:
    L = [f"# ruleplan — pack `{data['pack']}` (catalog {data['catalog_version']})",
         f"_rules: {data['counts']['rules']} · with evidence: "
         f"{data['counts']['rules_with_evidence']} · candidates routed: "
         f"{data['counts']['candidates_routed']}_\n",
         "| rule | family | det | evidence |",
         "|------|--------|-----|----------|"]
    for p in data["rules"]:
        L.append(f"| {p['rule_id']} | {p['family']} | "
                 f"{'✓' if p['deterministic'] else ''} | {p['evidence_count']} |")
    return "\n".join(L) + "\n"


def load_json(path: str | None) -> dict | None:
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"could not read {path}: {exc}", file=sys.stderr)
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Route deterministic candidates to isolated rule cards.")
    ap.add_argument("--rules", default=str(Path(__file__).resolve().parent.parent / "rules"))
    ap.add_argument("--signals")
    ap.add_argument("--routes")
    ap.add_argument("--git")
    ap.add_argument("--risk")
    ap.add_argument("--graph")
    ap.add_argument("--pack", default="all",
                    choices=["all", "arch_pack", "audit_pack"])
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--format", choices=["json", "md"], default="json")
    args = ap.parse_args()

    rules_dir = Path(args.rules).resolve()
    if not (rules_dir / "index.yml").exists():
        print(f"rule catalog not found: {rules_dir}/index.yml", file=sys.stderr)
        return 1
    idx, rules = load_catalog(rules_dir)
    data = {
        "signals": load_json(args.signals),
        "routes": load_json(args.routes),
        "git": load_json(args.git),
        "risk": load_json(args.risk),
        "graph": load_json(args.graph),
    }
    out = build(idx, rules, args.pack, data, args.top)
    print(to_md(out) if args.format == "md" else json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
