#!/usr/bin/env python3
"""
tornhill-sanitize — turn a real run's artifacts into a LEAK-FREE summary.

The engine's outputs (ruleplan.json, routes.json, signals.json, git.json,
risk.json) cite real `path:line`s and code excerpts — fine for a private run, not
for a public benchmark. This script keeps only what proves the engine works
(aggregate counts + per-rule evidence tallies) and destroys everything that could
identify a client:

  - excerpts are dropped entirely (never emitted),
  - real paths/module names are replaced by stable pseudonyms (moduleA, moduleB…),
  - NO framework, language, domain, path, or exact commit/file count is emitted
    (commit history is reduced to a coarse band only),
  - only counts and our own rule ids / family names survive verbatim.

Rule ids (SEC-IDOR-01…) and family names are ours, not the client's, so they are
safe to publish. A reader cannot infer the project's category or framework — the two
vectors that would let a colleague recognise it.

Pure stdlib. Read-only. No network.

Usage:
    python tornhill-sanitize.py --ruleplan ruleplan.json [--routes routes.json] \
        [--signals signals.json] [--git git.json] [--risk risk.json] \
        --label "Repo A" [--format md|json]
"""
from __future__ import annotations

import argparse
import json
import string
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


class Pseudonymizer:
    """Deterministic path -> moduleA/moduleB… map (no real names ever emitted)."""

    def __init__(self) -> None:
        self._map: dict[str, str] = {}

    def _label(self, n: int) -> str:
        letters = string.ascii_uppercase
        s = ""
        n += 1
        while n:
            n, r = divmod(n - 1, 26)
            s = letters[r] + s
        return f"module{s}"

    def of(self, path: str | None) -> str:
        if not path:
            return "module?"
        key = str(path).split(":")[0]  # drop :line
        if key not in self._map:
            self._map[key] = self._label(len(self._map))
        return self._map[key]


def load_json(path: str | None) -> dict | None:
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"could not read {path}: {exc}", file=sys.stderr)
        return None


def commit_band(n) -> str:
    """Coarse band only — an exact commit count is a fingerprint."""
    if not n:
        return "unknown"
    for hi, label in ((500, "<500"), (2000, "500–2k"), (5000, "2k–5k"),
                      (10000, "5k–10k")):
        if n < hi:
            return label
    return "10k+"


def build(args) -> dict:
    ruleplan = load_json(args.ruleplan) or {}
    routes = load_json(args.routes) or {}
    signals = load_json(args.signals) or {}
    git = load_json(args.git) or {}
    risk = load_json(args.risk) or {}
    ps = Pseudonymizer()

    route_rows = routes.get("routes", [])
    no_auth = sum(1 for r in route_rows if not r.get("has_auth"))
    # auth false-alarm reduction: line-local-only would have flagged everything not
    # authed on the route line; resolving global/indirect auth recovers the rest.
    by_src = routes.get("counts", {}).get("by_auth_source", {})
    route_src = by_src.get("route", 0)
    noauth_line_local = len(route_rows) - route_src   # what a naive scan would flag
    noauth_resolved = by_src.get("none", no_auth)     # after full resolution

    # per-rule evidence tallies (rule ids + families are safe to publish)
    per_rule = [
        {"rule_id": p["rule_id"], "family": p.get("family"),
         "deterministic": p.get("deterministic", False),
         "evidence_count": p.get("evidence_count", 0)}
        for p in ruleplan.get("rules", [])
    ]
    fired = [p for p in per_rule if p["evidence_count"] > 0]
    by_family: dict[str, int] = {}
    for p in fired:
        by_family[p["family"]] = by_family.get(p["family"], 0) + p["evidence_count"]

    # hotspots: keep numbers, pseudonymize module names
    hotspots = [
        {"module": ps.of(h.get("path") or h.get("module")),
         "churn": h.get("churn"), "degree": h.get("degree"),
         "risk": round(h.get("risk", 0), 4)}
        for h in risk.get("hotspots", [])[:5]
    ]

    return {
        "schema": "tornhill.benchmark/v1",
        "label": args.label,
        "commit_band": commit_band(git.get("commits_scanned")),
        "totals": {
            "routes": len(route_rows),
            "routes_without_auth": no_auth,
            "noauth_line_local": noauth_line_local,   # naive baseline
            "noauth_resolved": noauth_resolved,       # after auth resolution
            "signal_candidates": sum(signals.get("counts", {}).values()),
            "hotspots": len(risk.get("hotspots", [])),
            "rules_in_pack": len(per_rule),
            "rules_with_evidence": len(fired),
            "candidates_routed": ruleplan.get("counts", {}).get("candidates_routed"),
        },
        "signal_families": signals.get("counts", {}),   # family -> count (no paths)
        "evidence_by_family": dict(sorted(by_family.items())),
        "rules_fired": sorted(fired, key=lambda p: -p["evidence_count"]),
        "top_hotspots_pseudonymized": hotspots,
        "note": "counts + our rule ids only; all client paths/excerpts removed",
    }


def to_md(d: dict) -> str:
    t = d["totals"]
    L = [f"### {d['label']}",
         "",
         f"- commit history: **{d['commit_band']}** (band only)",
         f"- routes enumerated: **{t['routes']}** · no-auth after resolution: "
         f"**{t['noauth_resolved']}** (naive line-local scan would flag "
         f"**{t['noauth_line_local']}**)",
         f"- candidate signals: **{t['signal_candidates']}** · "
         f"hotspots: **{t['hotspots']}**",
         f"- rules with routed evidence: **{t['rules_with_evidence']}"
         f"/{t['rules_in_pack']}** · candidates routed: "
         f"**{t['candidates_routed']}**",
         ""]
    if d["signal_families"]:
        L.append("Signal candidates by family: " +
                 ", ".join(f"`{k}` {v}" for k, v in
                           sorted(d["signal_families"].items())) + "\n")
    if d["rules_fired"]:
        L += ["| rule | family | det | routed evidence |",
              "|------|--------|-----|-----------------|"]
        for p in d["rules_fired"]:
            L.append(f"| {p['rule_id']} | {p['family']} | "
                     f"{'✓' if p['deterministic'] else ''} | {p['evidence_count']} |")
        L.append("")
    if d["top_hotspots_pseudonymized"]:
        L += ["Top churn×centrality hotspots (module names pseudonymized):", "",
              "| module | churn | degree | risk |",
              "|--------|-------|--------|------|"]
        for h in d["top_hotspots_pseudonymized"]:
            L.append(f"| {h['module']} | {h['churn']} | {h['degree']} | {h['risk']} |")
        L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sanitize a run into a leak-free benchmark summary.")
    ap.add_argument("--ruleplan", required=True)
    ap.add_argument("--routes")
    ap.add_argument("--signals")
    ap.add_argument("--git")
    ap.add_argument("--risk")
    ap.add_argument("--label", required=True)
    ap.add_argument("--format", choices=["md", "json"], default="md")
    args = ap.parse_args()

    d = build(args)
    print(json.dumps(d, indent=2) if args.format == "json" else to_md(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
