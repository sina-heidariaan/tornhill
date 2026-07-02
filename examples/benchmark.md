---
title: tornhill on real code — a sanitized benchmark
derived-from: engine-run 2026.07
---

# tornhill on real code — a sanitized benchmark

tornhill's deterministic engine was run against **four real, private production
backends**. This page proves the engine works on real code — and discloses **nothing
that could identify the projects**: no names, business categories, languages,
frameworks, commit counts, file counts, or paths. Only engine-behaviour aggregates
and tornhill's own rule ids (`SEC-IDOR-01`, …) appear; those are ours, not the
projects'. The raw runs stay private.

## The engine ran clean

All four backends completed the full pipeline — enumerate routes, scan candidate
signals, build the module graph, mine git, join risk, route rules — with **zero
manual fixups**. Set-wide totals:

| Metric (all four combined) | Value |
|---|---|
| Routes enumerated | **912** |
| Candidate signals¹ | **2,872** |
| Candidates routed into isolated rules | **1,686** |
| Rules with routed evidence (per backend) | **17–19 of 23** |

¹ Capped at 200 per family, so this is a floor, not a ceiling. Candidates are
**evidence to confirm, not findings**.

The router split those ~1,700 candidates into **17–19 single-rule work packets per
backend**. Under the old design all of them would have entered one analysis prompt,
forcing the model to juggle every rule at once — the conflation risk this redesign
removes. Now each rule is confirmed in isolation, with only its own evidence.

## Accuracy: candidates are not findings

The clearest illustration is the auth check. Whether a route requires login is often
declared **away from the route line** — applied to the whole app at once, or through
an indirection layer, or inline. A naïve line-local reading therefore massively
over-reports "no auth."

Across the four backends, resolving app-wide and indirect auth (not just the route
line) changed the picture dramatically:

| Routes flagged "no auth" | Count (all four) |
|---|---|
| Naïve line-local scan | **826** |
| After resolving app-wide + indirect auth | **110** |

**An 87% cut in false "no-auth" candidates — before the per-rule confirmation even
runs.** The remaining 110 are not asserted as findings; they are candidates the
`SEC-AUTH-01` pass confirms against real code, guided by its false-positive guard
("auth applied by an upstream/global policy — trace it"). A missed pattern degrades
to "double-check this," never to a false public claim. The same candidate→confirm
discipline governs every family (PII, injection, N+1, …), not just auth.

## Per-rule routing (pooled across the four)

Which rules received real evidence, and how much — rule ids and families are
tornhill's own, safe to publish:

| Rule | Family | Backends with evidence | Candidates routed |
|---|---|---|---|
| PRI-RESP-01 | privacy | 4/4 | 200 |
| PRI-RETAIN-01 | privacy | 4/4 | 200 |
| SEC-IDOR-01 | security | 4/4 | 173 |
| SEC-RATELIMIT-01 | security | 4/4 | 100 |
| PRI-COLLECT-01 | privacy | 4/4 | 100 |
| PRI-CONSENT-01 | privacy | 4/4 | 100 |
| PRI-ACCESS-01 | privacy | 4/4 | 100 |
| PERF-TIMEOUT-01 | perf | 4/4 | 100 |
| CORR-EQ-01 | correctness | 4/4 | 100 |
| PERF-N1-01 | perf | 4/4 | 90 |
| SEC-INJ-SQL-01 | security | 4/4 | 76 |
| PRI-LOG-01 | privacy | 4/4 | 63 |
| SEC-INJ-EXEC-01 | security | 4/4 | 55 |
| PERF-SYNC-01 | perf | 4/4 | 51 |
| CORR-TXN-01 | correctness | 3/4 | 69 |
| CORR-RACE-01 | correctness | 3/4 | 69 |
| SEC-SECRET-01 | security | 3/4 | 5 |
| SEC-AUTH-01 | security | 2/4 | 23 |
| SEC-DESERIAL-01 | security | 2/4 | 7 |
| SEC-SSRF-01 | security | 2/4 | 5 |

Twenty distinct rules drew real evidence across the set — every family (security,
privacy, perf, correctness) fired on real code.

## Signals a static snapshot can't give

The deterministic git rules need no reasoning and can't come from a point-in-time
view: churn × centrality hotspots, temporal (hidden) coupling, and recurring-fix
pain. On these backends they surfaced the expected shape — a small number of modules
that are both heavily changed and heavily connected concentrate the risk. (Specific
modules and magnitudes are withheld as they are project data.)

## Honesty notes

- Candidate signals are capped per family — the totals are floors.
- `has_auth` is **approximate** (`route|decorator|custom_decorator|class|global|
  public|none`); it can miss unusual patterns (gateway/proxy auth, mount-time
  middleware, other languages). `none` means "confirm this," not "proven open."
- Nothing here is a finding. Findings require the per-rule confirmation pass reading
  the real code — which is not run in this deterministic benchmark.

## Reproduce (on any git repo)

```bash
DIR=/path/to/a/git/repo
python scripts/tornhill-mine-routes.py  "$DIR" --format json > routes.json
python scripts/tornhill-scan-signals.py "$DIR" --format json > signals.json
python scripts/tornhill-mine-graph.py   "$DIR" --module-depth 2 --format json > graph.json
python scripts/tornhill-mine-git.py     "$DIR" --module-depth 2 --top 1000 --format json > git.json
python scripts/tornhill-join-risk.py --git git.json --graph graph.json --format json > risk.json
python scripts/tornhill-route-rules.py --pack audit_pack \
  --signals signals.json --routes routes.json --risk risk.json --format json > ruleplan.json
# leak-free per-repo summary (counts + rule ids only; no framework, domain, or paths):
python scripts/tornhill-sanitize.py --ruleplan ruleplan.json --routes routes.json \
  --signals signals.json --git git.json --risk risk.json --label "Repo (anon)"
```
